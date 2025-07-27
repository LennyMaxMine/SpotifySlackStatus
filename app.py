from flask import Flask, redirect, request, session, render_template, jsonify
import os
import requests
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, auth, firestore
import json

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

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

@app.route("/")
def index():
    return render_template('login.html', firebase_config=json.dumps(FIREBASE_CONFIG))

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8888, debug=True)