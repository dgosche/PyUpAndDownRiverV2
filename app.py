"""
Up and Down the River — Flask Web App
Difficulty levels: easy | medium | hard

Easy:   Random card from legal pool. Bids randomly.
Medium: Tracks played cards this round, counts remaining trump,
        picks the cheapest winning card or cheapest loser.
        Bids with suit-strength awareness.
Hard:   Full card memory across tricks, infers opponent voids,
        plans conservatively when ahead of bid, aggressively
        when behind. Bids with precise trick-probability estimate.
"""

import random
from flask import Flask, session, jsonify, request, render_template
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "updownriver-secret-2024")

# ─── Constants ────────────────────────────────────────────────────────────────

SUITS      = ["♠","♥","♦","♣"]
SUIT_NAMES = {"♠":"Spades","♥":"Hearts","♦":"Diamonds","♣":"Clubs"}
RANKS      = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
RANK_VAL   = {r:i for i,r in enumerate(RANKS)}
TRUMP      = "♠"
ALL_NAMES  = ["You","Alice","Bob","Charlie","Don"]
ROUNDS     = list(range(1,11)) + list(range(9,0,-1))

# ─── Core card logic ──────────────────────────────────────────────────────────

def build_deck():
    return [{"rank":r,"suit":s} for s in SUITS for r in RANKS]

def shuffle_deck(deck):
    d = deck[:]
    random.shuffle(d)
    return d

def card_rank_in_trick(card, lead_suit, trump):
    if trump and card["suit"] == trump:
        return (2, RANK_VAL[card["rank"]])
    if lead_suit and card["suit"] == lead_suit:
        return (1, RANK_VAL[card["rank"]])
    return (0, RANK_VAL[card["rank"]])

def trick_winner_idx(trick, lead_suit, trump):
    best   = 0
    best_s = card_rank_in_trick(trick[0]["card"], lead_suit, trump)
    for i in range(1, len(trick)):
        s = card_rank_in_trick(trick[i]["card"], lead_suit, trump)
        if s > best_s:
            best_s = s
            best   = i
    return trick[best]["player"]

def calc_score(bid, tricks):
    if bid == 0 and tricks == 0: return 10
    if bid == tricks:            return bid * 10
    return tricks

def legal_pool(hand, lead_suit, trick):
    """Cards the player is allowed to play."""
    if trick and lead_suit:
        can = [c for c in hand if c["suit"] == lead_suit]
        return can if can else hand
    return hand

def current_trick_best(trick, lead_suit, trump):
    """Return the card currently winning the trick (or None)."""
    if not trick:
        return None
    return max((t["card"] for t in trick),
               key=lambda c: card_rank_in_trick(c, lead_suit, trump))

# ─── AI — EASY ────────────────────────────────────────────────────────────────
# Bid: random number in [0, num_cards], respecting dealer rule.
# Play: pick a random legal card.

def easy_bid(hand, num_cards, is_dealer, forbidden):
    options = [b for b in range(num_cards+1) if b != forbidden]
    return random.choice(options) if options else 0

def easy_play(hand, trick, lead_suit, trump):
    pool = legal_pool(hand, lead_suit, trick)
    return random.choice(pool)

# ─── AI — MEDIUM ──────────────────────────────────────────────────────────────
# Bid: count high cards and trump, estimate tricks with simple heuristic.
# Play: follow suit using cheapest winner when need tricks,
#       cheapest loser otherwise. Uses played-card memory to know
#       whether high cards are still live.

def medium_bid(hand, trump, num_cards, is_dealer, forbidden, played_cards):
    """
    Estimate tricks by checking whether each high card is still
    the best remaining in its suit (using played_cards set).
    """
    remaining = set()  # cards not yet played (excluding own hand)
    all_cards  = {(r,s) for s in SUITS for r in RANKS}
    hand_set   = {(c["rank"],c["suit"]) for c in hand}
    played_set = {(c["rank"],c["suit"]) for c in played_cards}
    remaining  = all_cards - hand_set - played_set

    est = 0.0
    for c in hand:
        suit = c["suit"]
        rank = c["rank"]
        rv   = RANK_VAL[rank]
        # Count how many cards of this suit with higher rank are still out
        higher_out = sum(1 for (r,s) in remaining
                         if s == suit and RANK_VAL[r] > rv)
        if suit == trump:
            # Trump: likely trick if nothing higher remains
            if higher_out == 0:
                est += 1.0
            elif higher_out <= 1:
                est += 0.6
            else:
                est += 0.25
        else:
            if higher_out == 0:
                est += 0.85
            elif higher_out <= 1:
                est += 0.4
            else:
                est += 0.1

    bid = max(0, min(round(est), num_cards))
    if is_dealer and forbidden is not None and bid == forbidden:
        bid = (forbidden-1) if forbidden > 0 else (forbidden+1)
        bid = max(0, min(bid, num_cards))
    return bid

def medium_play(hand, trick, lead_suit, trump, my_bid, my_tricks, played_cards):
    pool = legal_pool(hand, lead_suit, trick)
    need = my_tricks < my_bid

    # Identify cards in pool that would currently win the trick
    best_in_trick = current_trick_best(trick, lead_suit, trump)
    if best_in_trick:
        winners = [c for c in pool
                   if card_rank_in_trick(c, lead_suit, trump) >
                      card_rank_in_trick(best_in_trick, lead_suit, trump)]
    else:
        winners = pool  # leading — any card "wins" for now

    losers  = [c for c in pool if c not in winners]

    if need:
        if winners:
            # Play cheapest winner (save high cards for later)
            return min(winners, key=lambda c: card_rank_in_trick(c, lead_suit, trump))
        else:
            # Can't win — dump cheapest card
            return min(pool, key=lambda c: card_rank_in_trick(c, lead_suit, trump))
    else:
        # Don't need more tricks — dump cheapest card that won't accidentally win
        if losers:
            return min(losers, key=lambda c: card_rank_in_trick(c, lead_suit, trump))
        # All cards in pool would win — play cheapest to waste as little as possible
        return min(pool, key=lambda c: card_rank_in_trick(c, lead_suit, trump))

# ─── AI — HARD ────────────────────────────────────────────────────────────────
# Bid: full probability estimate using remaining deck knowledge.
#      Knows which suits opponents have shown void in.
# Play: plans conservatively when at/over bid, aggressively when behind.
#       Avoids wasting high trump. Leads strategically.

def hard_bid(hand, trump, num_cards, is_dealer, forbidden,
             played_cards, opponent_voids):
    """
    Probability-weighted trick estimate.
    opponent_voids: dict {player_idx: set of suits they've shown void in}
    """
    all_cards  = {(r,s) for s in SUITS for r in RANKS}
    hand_set   = {(c["rank"],c["suit"]) for c in hand}
    played_set = {(c["rank"],c["suit"]) for c in played_cards}
    unseen     = all_cards - hand_set - played_set  # cards not in my hand and not played

    est = 0.0
    for c in hand:
        suit = c["suit"]
        rank = c["rank"]
        rv   = RANK_VAL[rank]

        # Higher cards of same suit still unseen (could beat me)
        higher_unseen = [(r,s) for (r,s) in unseen
                         if s == suit and RANK_VAL[r] > rv]
        # Higher trump unseen (can always override non-trump)
        higher_trump  = [(r,s) for (r,s) in unseen
                         if s == trump and RANK_VAL[r] > rv]

        if suit == trump:
            danger = len(higher_unseen)  # only higher trump beats trump
            if danger == 0:   est += 1.0
            elif danger == 1: est += 0.72
            elif danger == 2: est += 0.48
            else:             est += 0.2
        else:
            # Non-trump: higher same-suit cards are dangerous,
            # but also any trump (unless opponents shown void in trump)
            same_suit_danger = len(higher_unseen)
            trump_danger     = len(higher_trump)

            if same_suit_danger == 0 and trump_danger == 0:
                est += 0.95
            elif same_suit_danger == 0:
                # I have the highest in suit but trump can override
                est += max(0.3, 0.85 - 0.12 * trump_danger)
            else:
                est += max(0.05, 0.5 - 0.1 * same_suit_danger - 0.05 * trump_danger)

    bid = max(0, min(round(est), num_cards))
    if is_dealer and forbidden is not None and bid == forbidden:
        bid = (forbidden-1) if forbidden > 0 else (forbidden+1)
        bid = max(0, min(bid, num_cards))
    return bid

def hard_play(hand, trick, lead_suit, trump, my_bid, my_tricks,
              played_cards, opponent_voids, tricks_remaining):
    """
    Full lookahead play.
    - Tracks whether we still need tricks or must avoid them.
    - Avoids wasting top trump when unnecessary.
    - When leading: leads a suit opponents are void in to draw trump out,
      or leads lowest non-trump when shedding tricks.
    - opponent_voids: {player_idx: set of suits shown void}
    - tricks_remaining: how many tricks are left in the round
    """
    pool   = legal_pool(hand, lead_suit, trick)
    need   = my_tricks < my_bid
    excess = my_tricks - my_bid  # positive = already over bid

    all_cards  = {(r,s) for s in SUITS for r in RANKS}
    hand_set   = {(c["rank"],c["suit"]) for c in hand}
    played_set = {(c["rank"],c["suit"]) for c in played_cards}
    unseen     = all_cards - hand_set - played_set

    def strength(c):
        return card_rank_in_trick(c, lead_suit or c["suit"], trump)

    best_in_trick = current_trick_best(trick, lead_suit, trump)

    # ── LEADING the trick ───────────────────────────────────────
    if not trick:
        if need:
            # Want to win: lead highest trump if it's the best remaining
            my_trumps = sorted([c for c in hand if c["suit"]==trump],
                               key=lambda c: RANK_VAL[c["rank"]], reverse=True)
            if my_trumps:
                top_trump = my_trumps[0]
                higher_trump_unseen = [(r,s) for (r,s) in unseen
                                       if s==trump and RANK_VAL[r] > RANK_VAL[top_trump["rank"]]]
                if not higher_trump_unseen:
                    return top_trump  # guaranteed winner

            # Lead highest non-trump in a suit opponents haven't voided
            non_trump = [c for c in hand if c["suit"] != trump]
            if non_trump:
                # Prefer suits where no opponent has shown void (they must follow)
                safe = [c for c in non_trump
                        if not any(c["suit"] in voids
                                   for voids in opponent_voids.values())]
                pool_lead = safe if safe else non_trump
                return max(pool_lead, key=lambda c: RANK_VAL[c["rank"]])

            # Only trump left
            return max(hand, key=lambda c: RANK_VAL[c["rank"]])

        else:
            # Avoid winning: lead lowest card, prefer suits opponents are void in
            # (they'll trump it, ensuring we lose)
            void_suits = {suit for voids in opponent_voids.values() for suit in voids}
            dumpable   = [c for c in hand if c["suit"] in void_suits and c["suit"] != trump]
            if dumpable:
                return min(dumpable, key=lambda c: RANK_VAL[c["rank"]])
            # Otherwise lead lowest non-trump
            non_trump = [c for c in hand if c["suit"] != trump]
            if non_trump:
                return min(non_trump, key=lambda c: RANK_VAL[c["rank"]])
            return min(hand, key=lambda c: RANK_VAL[c["rank"]])

    # ── FOLLOWING in the trick ───────────────────────────────────
    if best_in_trick:
        winners = [c for c in pool
                   if card_rank_in_trick(c, lead_suit, trump) >
                      card_rank_in_trick(best_in_trick, lead_suit, trump)]
    else:
        winners = pool
    losers = [c for c in pool if c not in winners]

    if need:
        if winners:
            # Play cheapest winner (preserve high cards)
            # But if tricks_remaining is low and we need multiple, use a sure winner
            if tricks_remaining <= 2 and my_bid - my_tricks > 1:
                return max(winners, key=lambda c: card_rank_in_trick(c, lead_suit, trump))
            return min(winners, key=lambda c: card_rank_in_trick(c, lead_suit, trump))
        else:
            # Can't win this trick — dump lowest
            return min(pool, key=lambda c: card_rank_in_trick(c, lead_suit, trump))
    else:
        # Don't need trick — play cheapest non-winner
        if losers:
            # Among losers, prefer to dump high non-trump cards
            # (they're dangerous in future tricks where we'd accidentally win)
            high_losers = [c for c in losers if c["suit"] != trump]
            if high_losers:
                return max(high_losers, key=lambda c: RANK_VAL[c["rank"]])
            return min(losers, key=lambda c: card_rank_in_trick(c, lead_suit, trump))
        # All pool cards would win — play cheapest to waste minimum
        return min(pool, key=lambda c: card_rank_in_trick(c, lead_suit, trump))

# ─── Dispatcher: route to correct difficulty ──────────────────────────────────

def dispatch_bid(player_idx, g):
    hand      = g["hands"][player_idx]
    trump     = g["trump"]
    nc        = ROUNDS[g["round_idx"]]
    is_dealer = (player_idx == g["dealer_idx"])
    sum_so_far = sum(b for b in g["bids"] if b is not None)
    if is_dealer:
        fval      = nc - sum_so_far
        forbidden = fval if 0 <= fval <= nc else None
    else:
        forbidden = None

    diff = g["difficulty"]

    if diff == "easy":
        return easy_bid(hand, nc, is_dealer, forbidden)
    elif diff == "medium":
        return medium_bid(hand, trump, nc, is_dealer, forbidden,
                          g["played_cards"])
    else:  # hard
        return hard_bid(hand, trump, nc, is_dealer, forbidden,
                        g["played_cards"], g["opponent_voids"])

def dispatch_play(player_idx, g):
    hand      = g["hands"][player_idx]
    trick     = g["trick"]
    lead_suit = g["lead_suit"]
    trump     = g["trump"]
    my_bid    = g["bids"][player_idx]
    my_tricks = g["tricks_won"][player_idx]
    nc        = ROUNDS[g["round_idx"]]
    tr_rem    = nc - g["trick_num"]  # tricks remaining after this one

    diff = g["difficulty"]

    if diff == "easy":
        return easy_play(hand, trick, lead_suit, trump)
    elif diff == "medium":
        return medium_play(hand, trick, lead_suit, trump, my_bid, my_tricks,
                           g["played_cards"])
    else:  # hard
        return hard_play(hand, trick, lead_suit, trump, my_bid, my_tricks,
                         g["played_cards"], g["opponent_voids"], tr_rem)

# ─── Session / game state ─────────────────────────────────────────────────────

def new_game(num_players, difficulty="medium"):
    names = ALL_NAMES[:num_players]
    return {
        "num_players":    num_players,
        "difficulty":     difficulty,   # easy | medium | hard
        "names":          names,
        "scores":         [0]*num_players,
        "dealer_idx":     0,
        "round_idx":      0,
        "round_log":      [],
        "phase":          "setup",
        "hands":          [[] for _ in range(num_players)],
        "bids":           [None]*num_players,
        "tricks_won":     [0]*num_players,
        "trick":          [],
        "lead_suit":      None,
        "current_player": 0,
        "bid_order":      [],
        "bid_pos":        0,
        "trick_num":      0,
        "trump":          TRUMP,
        "log":            [],
        "message":        "",
        "waiting_for":    "human",
        # Memory structures (updated as cards are played)
        "played_cards":   [],   # list of {rank,suit} played so far this round
        "opponent_voids": {},   # {player_idx: [suit,...]} suits shown void
    }

def deal_round(g):
    n  = g["num_players"]
    nc = ROUNDS[g["round_idx"]]
    deck = shuffle_deck(build_deck())
    hands = [[] for _ in range(n)]
    for cn in range(nc):
        for p in range(n):
            hands[p].append(deck[cn*n + p])
    g["hands"]          = hands
    g["bids"]           = [None]*n
    g["tricks_won"]     = [0]*n
    g["trick"]          = []
    g["lead_suit"]      = None
    g["trick_num"]      = 0
    g["trump"]          = TRUMP
    g["phase"]          = "bidding"
    g["bid_order"]      = [(g["dealer_idx"]+1+i) % n for i in range(n)]
    g["bid_pos"]        = 0
    g["message"]        = ""
    g["played_cards"]   = []   # reset memory each round
    g["opponent_voids"] = {p: [] for p in range(1, n)}
    g["log"].insert(0, f"── Round {g['round_idx']+1}/19: {nc} card{'s' if nc>1 else ''} ──")

def record_card_played(g, player_idx, card):
    """Update memory structures when any card is played."""
    g["played_cards"].append({"rank": card["rank"], "suit": card["suit"]})
    # Detect voids: if a non-zero player doesn't follow the lead suit
    lead = g["lead_suit"]
    if lead and card["suit"] != lead and player_idx != 0:
        voids = g["opponent_voids"].setdefault(player_idx, [])
        if lead not in voids:
            voids.append(lead)

def advance_ai_bidding(g):
    n  = g["num_players"]
    nc = ROUNDS[g["round_idx"]]
    while g["bid_pos"] < n:
        bidder = g["bid_order"][g["bid_pos"]]
        if bidder == 0:
            g["phase"]       = "bidding"
            g["waiting_for"] = "human"
            g["message"]     = "Your turn to bid!"
            return
        bid = dispatch_bid(bidder, g)
        g["bids"][bidder] = bid
        g["log"].insert(0, f"{g['names'][bidder]} bids {bid}")
        g["bid_pos"] += 1

    g["phase"]          = "playing"
    g["current_player"] = (g["dealer_idx"]+1) % n
    g["waiting_for"]    = "human" if g["current_player"] == 0 else "ai"
    g["message"]        = "Bidding complete — tricks begin!"
    advance_ai_playing(g)

def advance_ai_playing(g):
    n  = g["num_players"]
    nc = ROUNDS[g["round_idx"]]

    while True:
        if len(g["trick"]) == n:
            winner = trick_winner_idx(g["trick"], g["lead_suit"], g["trump"])
            g["tricks_won"][winner] += 1
            wname = g["names"][winner]
            g["log"].insert(0, f"Trick {g['trick_num']+1}: {wname} wins")
            g["message"]         = f"✦ {wname} wins the trick!"
            g["trick_num"]      += 1
            g["current_player"]  = winner
            g["trick"]           = []
            g["lead_suit"]       = None
            if g["trick_num"] >= nc:
                end_round(g)
                return

        cp = g["current_player"]
        if cp == 0:
            g["waiting_for"] = "human"
            if not g["trick"]:
                g["message"] = "Your turn — lead a card"
            else:
                ls  = g["lead_suit"]
                can = [c for c in g["hands"][0] if c["suit"]==ls]
                g["message"] = (f"Your turn — must follow {SUIT_NAMES[ls]}"
                                if can else "Your turn — play any card")
            return

        # AI plays
        card = dispatch_play(cp, g)
        g["hands"][cp].remove(card)
        if not g["trick"]:
            g["lead_suit"] = card["suit"]
        record_card_played(g, cp, card)
        g["trick"].append({"player": cp, "card": card})
        g["log"].insert(0, f"{g['names'][cp]}: {card['rank']}{card['suit']}")
        g["current_player"] = (cp+1) % n

def end_round(g):
    n   = g["num_players"]
    pts = [calc_score(g["bids"][p], g["tricks_won"][p]) for p in range(n)]
    for p in range(n):
        g["scores"][p] += pts[p]
    g["round_log"].append({
        "round":      g["round_idx"]+1,
        "num_cards":  ROUNDS[g["round_idx"]],
        "bids":       list(g["bids"]),
        "tricks_won": list(g["tricks_won"]),
        "pts":        pts,
    })
    g["dealer_idx"] = (g["dealer_idx"]+1) % n
    g["round_idx"] += 1
    if g["round_idx"] >= len(ROUNDS):
        g["phase"]   = "game_over"
        g["message"] = "Game over!"
    else:
        g["phase"]   = "round_end"
        nc = ROUNDS[g["round_idx"]]
        g["message"] = f"Round complete! Next: {nc} card{'s' if nc>1 else ''}"

def get_forbidden(g):
    nc        = ROUNDS[g["round_idx"]]
    bid_order = g["bid_order"]
    bid_pos   = g["bid_pos"]
    if bid_pos >= len(bid_order): return None
    bidder = bid_order[bid_pos]
    if bidder != 0: return None
    if 0 != g["dealer_idx"]: return None
    sum_so_far = sum(b for b in g["bids"] if b is not None)
    fval       = nc - sum_so_far
    return fval if 0 <= fval <= nc else None

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    session.clear()
    return render_template("index.html")

@app.route("/api/start", methods=["POST"])
def api_start():
    data        = request.get_json()
    num_players = max(2, min(5, int(data.get("num_players", 3))))
    difficulty  = data.get("difficulty", "medium")
    if difficulty not in ("easy","medium","hard"):
        difficulty = "medium"
    g = new_game(num_players, difficulty)
    deal_round(g)
    advance_ai_bidding(g)
    session["game"] = g
    return jsonify(state_for_client(g))

@app.route("/api/bid", methods=["POST"])
def api_bid():
    g = session.get("game")
    if not g or g["phase"] != "bidding":
        return jsonify({"error":"not your turn to bid"}), 400
    bid = int(request.get_json().get("bid", 0))
    nc  = ROUNDS[g["round_idx"]]
    forbidden = get_forbidden(g)
    if bid < 0 or bid > nc or bid == forbidden:
        return jsonify({"error":f"invalid bid {bid}"}), 400
    g["bids"][0] = bid
    g["log"].insert(0, f"You bid {bid}")
    g["bid_pos"] += 1
    advance_ai_bidding(g)
    session["game"] = g
    return jsonify(state_for_client(g))

@app.route("/api/play", methods=["POST"])
def api_play():
    g = session.get("game")
    if not g or g["phase"] != "playing" or g["current_player"] != 0:
        return jsonify({"error":"not your turn"}), 400
    data = request.get_json()
    rank, suit = data.get("rank"), data.get("suit")
    hand = g["hands"][0]
    card = next((c for c in hand if c["rank"]==rank and c["suit"]==suit), None)
    if not card:
        return jsonify({"error":"card not in hand"}), 400
    if g["lead_suit"]:
        can_follow = [c for c in hand if c["suit"]==g["lead_suit"]]
        if can_follow and card["suit"] != g["lead_suit"]:
            return jsonify({"error":f"Must follow suit: {SUIT_NAMES[g['lead_suit']]}"}), 400
    hand.remove(card)
    if not g["trick"]:
        g["lead_suit"] = card["suit"]
    record_card_played(g, 0, card)
    g["trick"].append({"player":0,"card":card})
    g["log"].insert(0, f"You: {card['rank']}{card['suit']}")
    g["current_player"] = 1 % g["num_players"]
    advance_ai_playing(g)
    session["game"] = g
    return jsonify(state_for_client(g))

@app.route("/api/next_round", methods=["POST"])
def api_next_round():
    g = session.get("game")
    if not g or g["phase"] != "round_end":
        return jsonify({"error":"not round end"}), 400
    deal_round(g)
    advance_ai_bidding(g)
    session["game"] = g
    return jsonify(state_for_client(g))

@app.route("/api/state", methods=["GET"])
def api_state():
    g = session.get("game")
    if not g: return jsonify({"phase":"setup"})
    return jsonify(state_for_client(g))

def state_for_client(g):
    nc        = ROUNDS[g["round_idx"]] if g["round_idx"] < len(ROUNDS) else 0
    forbidden = get_forbidden(g) if g["phase"]=="bidding" else None
    return {
        "phase":          g["phase"],
        "round_num":      g["round_idx"]+1 if g["round_idx"] < len(ROUNDS) else len(ROUNDS),
        "num_cards":      nc,
        "names":          g["names"],
        "scores":         g["scores"],
        "dealer_idx":     g["dealer_idx"],
        "bids":           g["bids"],
        "tricks_won":     g["tricks_won"],
        "trick":          g["trick"],
        "lead_suit":      g["lead_suit"],
        "current_player": g["current_player"],
        "hand":           g["hands"][0],
        "ai_card_counts": [len(g["hands"][p]) for p in range(1, g["num_players"])],
        "message":        g["message"],
        "log":            g["log"][:30],
        "forbidden_bid":  forbidden,
        "waiting_for":    g["waiting_for"],
        "round_log":      g["round_log"],
        "trump":          g["trump"],
        "num_players":    g["num_players"],
        "difficulty":     g["difficulty"],
        "rounds_total":   len(ROUNDS),
        "round_idx":      g["round_idx"],
    }

if __name__ == "__main__":
    import threading, webbrowser, sys

    # When bundled by PyInstaller, templates live next to the exe
    if getattr(sys, "frozen", False):
        import os
        base = sys._MEIPASS
        app.template_folder = os.path.join(base, "templates")

    # Open browser automatically after a short delay
    def open_browser():
        import time
        time.sleep(1.2)
        webbrowser.open("http://localhost:5000")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
