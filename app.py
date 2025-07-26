from flask import Flask, redirect, request, session
import os
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET")
SLACK_REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

def init_db():
    conn = sqlite3.connect("tokens.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            slack_user_id TEXT PRIMARY KEY,
            slack_access_token TEXT,
            spotify_access_token TEXT,
            spotify_refresh_token TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route("/")
def index():
    return '''
        <h1>Connect your accounts</h1>
        <a href="/slack/login">1. Connect Slack</a><br>
        <a href="/spotify/login">2. Connect Spotify</a>
    '''

@app.route("/slack/login")
def slack_login():
    auth_url = (
        "https://slack.com/oauth/v2/authorize"
        f"?client_id={SLACK_CLIENT_ID}"
        f"&user_scope=users.profile:read,users.profile:write"
        f"&redirect_uri={SLACK_REDIRECT_URI}"
    )
    return redirect(auth_url)

@app.route("/slack/callback")
def slack_callback():
    try:
        error = request.args.get("error")
        if error:
            return f"Error: {error}", 400
        
        code = request.args.get("code")
        if not code:
            return "Error: No authorization code received", 400
        
        resp = requests.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": SLACK_REDIRECT_URI
            }
        )
        
        resp_data = resp.json()
        
        if not resp_data.get("ok"):
            error_msg = resp_data.get("error", "Unknown error")
            return f"Error: {error_msg}", 400
        
        slack_user_id = None
        slack_access_token = None
        
        if "authed_user" in resp_data:
            authed_user = resp_data["authed_user"]
            slack_user_id = authed_user.get("id")
            slack_access_token = authed_user.get("access_token")
        
        if not slack_user_id and "user_id" in resp_data:
            slack_user_id = resp_data["user_id"]
        
        if not slack_access_token and "access_token" in resp_data:
            slack_access_token = resp_data["access_token"]
        
        if not slack_user_id or not slack_access_token:
            return "Error: Missing user information", 400
        
        session["slack_user_id"] = slack_user_id

        conn = sqlite3.connect("tokens.db")
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO users (slack_user_id, slack_access_token)
            VALUES (?, ?)
        ''', (slack_user_id, slack_access_token))
        conn.commit()
        conn.close()
        
        return "Slack linked! Now <a href='/spotify/login'>link Spotify</a>."
        
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/spotify/login")
def spotify_login():
    auth_url = (
        "https://accounts.spotify.com/authorize"
        f"?response_type=code"
        f"&client_id={SPOTIFY_CLIENT_ID}"
        f"&redirect_uri={SPOTIFY_REDIRECT_URI}"
        f"&scope=user-read-playback-state"
    )
    return redirect(auth_url)

@app.route("/spotify/callback")
def spotify_callback():
    pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8888, debug=True)
