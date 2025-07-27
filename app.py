from flask import Flask, redirect, request, session, render_template_string, jsonify
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

LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Login - Account Connector</title>
    <script src="https://www.gstatic.com/firebasejs/9.22.0/firebase-app-compat.js"></script>
    <script src="https://www.gstatic.com/firebasejs/9.22.0/firebase-auth-compat.js"></script>
</head>
<body>
    <h1>Account Connector</h1>
    
    <div id="login-section">
        <h2>Please log in with Firebase</h2>
        <button onclick="signInWithEmail()">Sign In with Email</button>
        <button onclick="signInWithGoogle()">Sign In with Google</button>
        <div id="error-message"></div>
    </div>
    
    <div id="user-info" style="display: none;">
        <h2>Welcome!</h2>
        <p>Email: <span id="user-email"></span></p>
        <p>UID: <span id="user-uid"></span></p>
        <button onclick="proceedToDashboard()">Go to Dashboard</button>
        <button onclick="signOut()">Sign Out</button>
    </div>

    <script>
        const firebaseConfig = {{ firebase_config | safe }};
        firebase.initializeApp(firebaseConfig);
        
        const auth = firebase.auth();
        
        auth.onAuthStateChanged(function(user) {
            if (user) {
                document.getElementById('login-section').style.display = 'none';
                document.getElementById('user-info').style.display = 'block';
                document.getElementById('user-email').textContent = user.email;
                document.getElementById('user-uid').textContent = user.uid;
            } else {
                document.getElementById('login-section').style.display = 'block';
                document.getElementById('user-info').style.display = 'none';
            }
        });
        
        function signInWithEmail() {
            const email = prompt("Enter your email:");
            const password = prompt("Enter your password:");
            
            if (email && password) {
                auth.signInWithEmailAndPassword(email, password)
                .catch(function(error) {
                    if (error.code === 'auth/user-not-found') {
                        auth.createUserWithEmailAndPassword(email, password)
                        .catch(function(createError) {
                            showError(createError.message);
                        });
                    } else {
                        showError(error.message);
                    }
                });
            }
        }
        
        function signInWithGoogle() {
            const provider = new firebase.auth.GoogleAuthProvider();
            auth.signInWithPopup(provider)
            .catch(function(error) {
                showError(error.message);
            });
        }
        
        function signOut() {
            auth.signOut();
        }
        
        function showError(message) {
            document.getElementById('error-message').textContent = message;
        }
        
        async function proceedToDashboard() {
            try {
                const user = auth.currentUser;
                if (user) {
                    const token = await user.getIdToken();
                    
                    const response = await fetch('/verify_token', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ idToken: token })
                    });
                    
                    if (response.ok) {
                        window.location.href = '/dashboard';
                    } else {
                        showError('Authentication failed');
                    }
                }
            } catch (error) {
                showError('Authentication error: ' + error.message);
            }
        }
    </script>
</body>
</html>
'''

DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard - Account Connector</title>
</head>
<body>
    <h1>Account Dashboard</h1>
    
    <div>
        <p><strong>Email:</strong> {{ user_email }}</p>
        <p><strong>Firebase UID:</strong> {{ firebase_uid }}</p>
        {% if last_login %}
        <p><strong>Last Login:</strong> {{ last_login }}</p>
        {% endif %}
    </div>
    
    <button onclick="location.href='/logout'">Logout</button>
    
    <div>
        <h2>Slack Account</h2>
        <p>Status: {{ 'Connected' if slack_connected else 'Not Connected' }}</p>
        {% if slack_connected %}
            <p>Slack User ID: {{ slack_user_id }}</p>
            {% if slack_connected_at %}
            <p>Connected: {{ slack_connected_at }}</p>
            {% endif %}
            <button onclick="location.href='/slack/disconnect'">Disconnect Slack</button>
        {% else %}
            <p>Connect your Slack account to enable integration.</p>
            <button onclick="location.href='/slack/login'">Connect Slack</button>
        {% endif %}
    </div>
    
    <div>
        <h2>Spotify Account</h2>
        <p>Status: {{ 'Connected' if spotify_connected else 'Not Connected' }}</p>
        {% if spotify_connected %}
            <p>Spotify account connected successfully.</p>
            {% if spotify_connected_at %}
            <p>Connected: {{ spotify_connected_at }}</p>
            {% endif %}
            <button onclick="location.href='/spotify/disconnect'">Disconnect Spotify</button>
        {% else %}
            <p>Connect your Spotify account to enable music integration.</p>
            <button onclick="location.href='/spotify/login'">Connect Spotify</button>
        {% endif %}
    </div>
</body>
</html>
'''

@app.route("/")
def index():
    return render_template_string(LOGIN_TEMPLATE, firebase_config=json.dumps(FIREBASE_CONFIG))

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

@app.route("/dashboard")
@require_auth
def dashboard():
    firebase_uid = session['firebase_uid']
    
    user_data = get_user_data(firebase_uid)
    
    slack_data = user_data.get('slack', {})
    spotify_data = user_data.get('spotify', {})
    
    return render_template_string(DASHBOARD_TEMPLATE,
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
        
        return "Slack linked successfully! <a href='/dashboard'>Return to Dashboard</a>"
        
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
    
    return redirect('/dashboard')

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

        return "Spotify linked successfully! <a href='/dashboard'>Return to Dashboard</a>"
        
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
    
    return redirect('/dashboard')

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8888, debug=True)