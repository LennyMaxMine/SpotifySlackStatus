from flask import Flask, redirect, request, session, render_template, jsonify
import os
import requests
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, auth, firestore
import json
from flask_cors import CORS
from datetime import datetime
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import time
import threading

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

CORS(app, supports_credentials=True)

cred = credentials.Certificate(os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_PATH"))
firebase_admin.initialize_app(cred)

db = firestore.client()

SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET")
SLACK_REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

FIREBASE_CONFIG = {
    "apiKey": os.getenv("FIREBASE_API_KEY"),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
    "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
    "appId": os.getenv("FIREBASE_APP_ID")
}

existing_services = ["YOUTUBE", "APPLE_MUSIC", "SPOTIFY"]

def verify_firebase_token(id_token):
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        print(f"Token verification failed: {e}")
        return None

def require_auth(f):
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'firebase_uid' not in session:
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function

def get_user_data(firebase_uid):
    try:
        user_ref = db.collection('users').document(firebase_uid)
        user_doc = user_ref.get()
        if user_doc.exists:
            return user_doc.to_dict()
        return {}
    except Exception as e:
        print(f"Error getting user data: {e}")
        return {}

def update_user_data(firebase_uid, data):
    try:
        user_ref = db.collection('users').document(firebase_uid)
        user_ref.set(data, merge=True)
        return True
    except Exception as e:
        print(f"Error updating user data: {e}")
        return False

def get_user_tokens(firebase_uid):
    try:
        user_ref = db.collection('users').document(firebase_uid)
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            slack_token = user_data.get('slack', {}).get('access_token')
            spotify_access_token = user_data.get('spotify', {}).get('access_token')
            spotify_refresh_token = user_data.get('spotify', {}).get('refresh_token')
            return slack_token, spotify_access_token, spotify_refresh_token
        return None, None, None
    except Exception as e:
        print(f"Error getting user tokens: {e}")
        return None, None, None

def refresh_spotify_token(firebase_uid, refresh_token):
    try:
        sp_oauth = SpotifyOAuth(
            client_id=os.getenv("SPOTIPY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
            redirect_uri="127.0.0.1:8888/callback", #os.getenv("SPOTIPY_REDIRECT_URI"),
            scope="user-read-playback-state"
        )
        
        token_info = sp_oauth.refresh_access_token(refresh_token)
        new_access_token = token_info['access_token']
        
        user_ref = db.collection('users').document(firebase_uid)
        user_ref.update({
            'spotify.access_token': new_access_token
        })
        
        return new_access_token
    except Exception as e:
        print(f"Error refreshing Spotify token: {e}")
        return None


# Slack Syncing

sync_threads = {}
sync_status = {}  

def get_current_track_from_priority(user_data):
    priority_list = user_data.get("priority", {}).get("list", "")
    if not priority_list:
        return None

    services = [s.strip() for s in priority_list.split(",") if s.strip()]
    for service in services:
        field = f"last_{service.lower()}"
        if field in user_data and user_data[field]:
            return user_data[field]
    return None

def slack_sync_worker(firebase_uid):
    slack_token, _, _ = get_user_tokens(firebase_uid)
    if not slack_token:
        sync_status[firebase_uid]['active'] = False
        sync_status[firebase_uid]['error'] = "No Slack token"
        return

    slack_client = WebClient(token=slack_token)

    while sync_status.get(firebase_uid, {}).get('active', False):
        try:
            user_data = get_user_data(firebase_uid)
            if not user_data:
                sync_status[firebase_uid]['error'] = "User not found"
                time.sleep(10)
                continue

            track = get_current_track_from_priority(user_data)
            if not track:
                sync_status[firebase_uid]['error'] = "No track found"
                time.sleep(10)
                continue

            status_text = f"{track['artist']} – {track['name']}"
            slack_client.users_profile_set(profile={
                "status_text": status_text,
                "status_emoji": ":musical_note:",
                "status_expiration": 0
            })

            sync_status[firebase_uid]['current_song'] = status_text
            sync_status[firebase_uid]['last_update'] = datetime.now().isoformat()
            sync_status[firebase_uid]['error'] = None
            time.sleep(15)

        except Exception as e:
            sync_status[firebase_uid]['error'] = str(e)
            sync_status[firebase_uid]['error_count'] = sync_status[firebase_uid].get('error_count', 0) + 1
            time.sleep(10)

@app.route('/sync/slack/start/<firebase_uid>', methods=['POST'])
def start_sync(firebase_uid):
    try:
        slack_token, _, _ = get_user_tokens(firebase_uid)
        if not slack_token:
            return jsonify({"error": "Slack account not connected", "success": False}), 400

        if firebase_uid in sync_status:
            sync_status[firebase_uid]['active'] = False
            time.sleep(1)

        sync_status[firebase_uid] = {
            'active': True,
            'current_song': None,
            'last_update': None,
            'error': None,
            'error_count': 0
        }

        thread = threading.Thread(target=slack_sync_worker, args=(firebase_uid,), daemon=True)
        thread.start()
        sync_threads[firebase_uid] = thread

        return jsonify({'success': True, 'message': f'Slack sync started for {firebase_uid}'})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/sync/slack/stop/<firebase_uid>', methods=['POST'])
def stop_sync(firebase_uid):
    try:
        if firebase_uid in sync_status:
            sync_status[firebase_uid]['active'] = False
        return jsonify({'success': True, 'message': f'Slack sync stopped for {firebase_uid}'})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/sync/slack/status/<firebase_uid>', methods=['GET'])
def get_sync_status(firebase_uid):
    try:
        status = sync_status.get(firebase_uid, {})
        running = firebase_uid in sync_threads and sync_threads[firebase_uid].is_alive()
        return jsonify({
            'running': running,
            'active': status.get('active', False),
            'current_song': status.get('current_song'),
            'last_update': status.get('last_update'),
            'error': status.get('error'),
            'error_count': status.get('error_count', 0)
        })
    except Exception as e:
        return jsonify({'error': str(e), 'running': False}), 500


# Pages

@app.route("/")
def index():
    return render_template('login.html', firebase_config=json.dumps(FIREBASE_CONFIG))

@app.route("/linked-accounts")
@require_auth
def linked_accounts():
    firebase_uid = session['firebase_uid']
    
    user_data = get_user_data(firebase_uid)
    
    slack_data = user_data.get('slack', {})
    spotify_data = user_data.get('spotify', {})
    
    return render_template('linked_accounts.html',
                         user_email=session.get('user_email'),
                         firebase_uid=firebase_uid,
                         last_login=user_data.get('last_login'),
                         slack_connected=bool(slack_data.get('access_token')),
                         slack_user_id=slack_data.get('user_id'),
                         slack_connected_at=slack_data.get('connected_at'),
                         spotify_connected=bool(spotify_data.get('access_token')),
                         spotify_connected_at=spotify_data.get('connected_at'))

@app.route("/dashboard")
@require_auth
def dashboard():
    firebase_uid = session['firebase_uid']
    user_data = get_user_data(firebase_uid)

    slack_data = user_data.get('slack', {})
    spotify_data = user_data.get('spotify', {})

    return render_template(
        'dashboard.html',
        user_email=session.get('user_email'),
        firebase_uid=firebase_uid,
        slack_connected=bool(slack_data.get('access_token')),
        slack_user_id=slack_data.get('user_id'),
        slack_connected_at=slack_data.get('connected_at'),
        spotify_connected=bool(spotify_data.get('access_token')),
        spotify_connected_at=spotify_data.get('connected_at')
    )

@app.route("/test")
def test_page():
    firebase_config = {
        "apiKey": os.getenv("FIREBASE_API_KEY"),
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
        "projectId": os.getenv("FIREBASE_PROJECT_ID"),
    }
    return f'''
    <!DOCTYPE html>
    <html lang="de">
    <head>
      <meta charset="UTF-8" />
      <title>API Testseite</title>
      <style>
        body {{ font-family: Arial, sans-serif; margin: 2rem; }}
        label {{ display: block; margin-top: 1rem; }}
        input {{ padding: 0.5rem; width: 300px; max-width: 100%; }}
        button {{ margin-top: 1.5rem; padding: 0.5rem 1rem; font-size: 1rem; }}
        .result {{ margin-top: 1rem; white-space: pre-wrap; background: #eee; padding: 1rem; border-radius: 5px; }}
        hr {{ margin: 2rem 0; }}
      </style>
      <script src="https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js"></script>
      <script src="https://www.gstatic.com/firebasejs/9.23.0/firebase-auth-compat.js"></script>
    </head>
    <body>
      <h1>API Testseite</h1>

      <h2>/api/set_client_status</h2>
      <label for="name">Name (Fallback: apfel)</label>
      <input type="text" id="name" placeholder="apfel" />

      <label for="artist">Artist (Fallback: keksdose)</label>
      <input type="text" id="artist" placeholder="keksdose" />

      <label for="source">Source (Fallback: youtube)</label>
      <input type="text" id="source" placeholder="youtube" />

      <button id="sendStatusBtn">Status senden</button>
      <div class="result" id="statusResult"></div>

      <hr>

      <h2>/api/set_priority</h2>
      <label for="priorityList">Liste der Services (Komma getrennt, z. B. youtube,spotify,slack)</label>
      <input type="text" id="priorityList" placeholder="youtube,spotify,slack" />

      <button id="sendPriorityBtn">Priorität senden</button>
      <div class="result" id="priorityResult"></div>

      <script>
        const firebaseConfig = {json.dumps(firebase_config)};
        firebase.initializeApp(firebaseConfig);

        firebase.auth().signInAnonymously().catch(e => console.error("Login Fehler:", e));

        document.getElementById('sendStatusBtn').addEventListener('click', async () => {{
          const resultDiv = document.getElementById('statusResult');
          try {{
            const user = firebase.auth().currentUser;
            if (!user) {{
              alert("Nicht eingeloggt");
              return;
            }}
            const idToken = await user.getIdToken();
            const firebase_uid = user.uid;

            const name = document.getElementById('name').value.trim() || "apfel";
            const artist = document.getElementById('artist').value.trim() || "keksdose";
            const source = document.getElementById('source').value.trim() || "youtube";

            const payload = {{ name, artist, source }};
            const response = await fetch(`http://localhost:8888/api/set_client_status/${{firebase_uid}}`, {{
              method: "POST",
              headers: {{
                "Content-Type": "application/json",
                "Authorization": "Bearer " + idToken
              }},
              body: JSON.stringify(payload)
            }});
            const json = await response.json();
            resultDiv.textContent = JSON.stringify(json, null, 2);
          }} catch (e) {{
            resultDiv.textContent = "Fehler beim Senden: " + e.message;
          }}
        }});

        document.getElementById('sendPriorityBtn').addEventListener('click', async () => {{
          const resultDiv = document.getElementById('priorityResult');
          try {{
            const user = firebase.auth().currentUser;
            if (!user) {{
              alert("Nicht eingeloggt");
              return;
            }}
            const idToken = await user.getIdToken();
            const firebase_uid = user.uid;

            const listRaw = document.getElementById('priorityList').value.trim() || "youtube,spotify,slack";
            const list = listRaw.split(",").map(s => s.trim()).filter(Boolean);

            const payload = {{ list }};
            const response = await fetch(`http://localhost:8888/api/set_priority/${{firebase_uid}}`, {{
              method: "POST",
              headers: {{
                "Content-Type": "application/json",
                "Authorization": "Bearer " + idToken
              }},
              body: JSON.stringify(payload)
            }});
            const json = await response.json();
            resultDiv.textContent = JSON.stringify(json, null, 2);
          }} catch (e) {{
            resultDiv.textContent = "Fehler beim Senden: " + e.message;
          }}
        }});
      </script>
    </body>
    </html>
    '''


# Oauth

@app.route("/verify_token", methods=["POST"])
def verify_token():
    try:
        data = request.get_json()
        id_token = data.get('idToken')
        
        decoded_token = verify_firebase_token(id_token)
        if decoded_token:
            session['firebase_uid'] = decoded_token['uid']
            session['user_email'] = decoded_token.get('email', '')
            
            user_data = {
                'email': decoded_token.get('email', ''),
                'last_login': firestore.SERVER_TIMESTAMP,
                'display_name': decoded_token.get('name', ''),
            }
            update_user_data(decoded_token['uid'], user_data)
            
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Invalid token"}), 401
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect('/')

@app.route("/slack/login")
@require_auth
def slack_login():
    auth_url = (
        "https://slack.com/oauth/v2/authorize"
        f"?client_id={SLACK_CLIENT_ID}"
        f"&user_scope=users.profile:read,users.profile:write"
        f"&redirect_uri={SLACK_REDIRECT_URI}"
    )
    return redirect(auth_url)

@app.route("/slack/callback")
@require_auth
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
        
        firebase_uid = session['firebase_uid']

        slack_data = {
            'slack': {
                'user_id': slack_user_id,
                'access_token': slack_access_token,
                'connected_at': firestore.SERVER_TIMESTAMP
            }
        }
        update_user_data(firebase_uid, slack_data)
        
        return "Slack linked successfully! <a href='/linked-accounts'>Return to Linked Accounts</a>"
        
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/slack/disconnect")
@require_auth
def slack_disconnect():
    firebase_uid = session['firebase_uid']
    
    user_ref = db.collection('users').document(firebase_uid)
    user_ref.update({
        'slack': firestore.DELETE_FIELD
    })
    
    return redirect('/linked-accounts')

@app.route("/spotify/login")
@require_auth
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
@require_auth
def spotify_callback():
    try:
        code = request.args.get("code")
        if not code:
            return "Error: No authorization code received", 400
            
        token_resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": SPOTIFY_REDIRECT_URI,
                "client_id": SPOTIFY_CLIENT_ID,
                "client_secret": SPOTIFY_CLIENT_SECRET
            }
        ).json()

        if "access_token" not in token_resp:
            return f"Error: {token_resp.get('error_description', 'Unknown error')}", 400

        access_token = token_resp["access_token"]
        refresh_token = token_resp["refresh_token"]

        firebase_uid = session['firebase_uid']

        spotify_data = {
            'spotify': {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'connected_at': firestore.SERVER_TIMESTAMP
            }
        }
        update_user_data(firebase_uid, spotify_data)

        return "Spotify linked successfully! <a href='/linked-accounts'>Return to Linked Accounts</a>"
        
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/spotify/disconnect")
@require_auth
def spotify_disconnect():
    firebase_uid = session['firebase_uid']
    
    user_ref = db.collection('users').document(firebase_uid)
    user_ref.update({
        'spotify': firestore.DELETE_FIELD
    })
    
    return redirect('/linked-accounts')

@app.route("/api/user/tokens")
@require_auth
def get_user_tokens():
    firebase_uid = session['firebase_uid']
    user_data = get_user_data(firebase_uid)
    
    return jsonify({
        'slack': {
            'user_id': user_data.get('slack', {}).get('user_id'),
            'has_token': bool(user_data.get('slack', {}).get('access_token'))
        },
        'spotify': {
            'has_token': bool(user_data.get('spotify', {}).get('access_token'))
        }
    })


# Spotify Pull

spotify_pull_status = {}
spotify_threads = {}

def spotify_pull_worker(firebase_uid):
    spotify_pull_status[firebase_uid] = True

    _, spotify_token, spotify_refresh_token = get_user_tokens(firebase_uid)
    sp = spotipy.Spotify(auth=spotify_token)

    while spotify_pull_status.get(firebase_uid, False):
        try:
            playback = sp.current_playback()
            if playback and playback.get("is_playing"):
                track = playback["item"]
                if track:
                    song_data = {
                        "name": track["name"],
                        "artist": ", ".join(a["name"] for a in track["artists"]),
                        "updated": datetime.now().isoformat()
                    }
                    update_user_data(firebase_uid, {"last_spotify": song_data})
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                new_token = refresh_spotify_token(firebase_uid, spotify_refresh_token)
                if new_token:
                    sp = spotipy.Spotify(auth=new_token)
                else:
                    print(f"Spotify token refresh failed for {firebase_uid}")
            else:
                print(f"Spotify API error for {firebase_uid}: {e}")
        except Exception as e:
            print(f"Spotify pull error for {firebase_uid}: {e}")

        time.sleep(30)

    print(f"Spotify pull stopped for {firebase_uid}")

@app.route('/spotify/pull/start/<firebase_uid>', methods=['POST'])
def start_spotify_pull(firebase_uid):
    if not spotify_pull_status.get(firebase_uid, False):
        spotify_pull_status[firebase_uid] = True
        t = threading.Thread(target=spotify_pull_worker, args=(firebase_uid,), daemon=True)
        t.start()
        spotify_threads[firebase_uid] = t
    return jsonify({'success': True, 'message': 'Spotify pulling started'})

@app.route('/spotify/pull/stop/<firebase_uid>', methods=['POST'])
def stop_spotify_pull(firebase_uid):
    spotify_pull_status[firebase_uid] = False
    return jsonify({'success': True, 'message': 'Spotify pulling stopped'})

@app.route('/spotify/pull/status/<firebase_uid>', methods=['GET'])
def spotify_pull_status_route(firebase_uid):
    active = spotify_pull_status.get(firebase_uid, False)
    return jsonify({'success': True, 'pulling': active})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8888, debug=True)

