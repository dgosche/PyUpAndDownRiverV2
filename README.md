# Up and Down the River — Web App

## Play locally (on your PC or phone on the same WiFi)

1. Install Python 3.9+ if you don't have it
2. Open a terminal in this folder and run:

```
pip install flask gunicorn
python app.py
```

3. Open http://localhost:5000 on your PC
4. To play on your phone (same WiFi network):
   - Find your PC's local IP address:
     - Windows: run `ipconfig` in Command Prompt, look for "IPv4 Address" (e.g. 192.168.1.42)
     - Mac: run `ipconfig getifaddr en0`
   - On your phone browser go to: http://192.168.1.42:5000
     (replace with your actual IP)

---

## Deploy to Render (free, play from anywhere)

Render gives you a free public URL anyone can open on any device.

1. Create a free account at https://render.com
2. Push this folder to a GitHub repository:
   ```
   git init
   git add .
   git commit -m "Up and Down the River web app"
   ```
   Then create a new repo on GitHub and push to it.

3. On Render:
   - Click "New +" → "Web Service"
   - Connect your GitHub repo
   - Settings:
     - **Build Command:** `pip install -r requirements.txt`
     - **Start Command:** `gunicorn app:app`
     - **Instance Type:** Free
   - Click "Create Web Service"

4. Render gives you a URL like `https://your-app-name.onrender.com`
   Share that link — anyone can play from any phone or browser, no install needed!

---

## Notes

- Each browser tab/session is a separate game
- The free Render tier spins down after 15 min of inactivity
  (first load after that takes ~30 seconds to wake up)
- To keep it always on, upgrade to Render's $7/month paid tier
