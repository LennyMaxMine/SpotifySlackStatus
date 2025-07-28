from flask import Flask, jsonify, request
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
import os
import threading
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)

CORS(app, supports_credentials=True)

cred = credentials.Certificate(os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_PATH"))
firebase_admin.initialize_app(cred)
db = firestore.client()

existing_services = ["YOUTUBE", "APPLE_MUSIC", "SPOTIFY"]

sync_threads = {}
sync_status = {}  

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
            redirect_uri="127.0.0.1:1605/callback", #os.getenv("SPOTIPY_REDIRECT_URI"),
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

def spotify_slack_sync(firebase_uid):
    try:
        slack_token, spotify_token, spotify_refresh_token = get_user_tokens(firebase_uid)
        
        if not slack_token or not spotify_token:
            sync_status[firebase_uid] = {
                'active': False,
                'error': 'Missing tokens',
                'last_update': datetime.now().isoformat()
            }
            return
        
        slack_client = WebClient(token=slack_token)
        sp = spotipy.Spotify(auth=spotify_token)
        
        try:
            original_status_response = slack_client.users_profile_get()
            original_status_text = original_status_response["profile"]["status_text"]
            original_status_emoji = original_status_response["profile"]["status_emoji"]
        except Exception as e:
            print(f"Error getting original status: {e}")
            original_status_text = ""
            original_status_emoji = ""
        
        last_status = None
        error_count = 0
        
        sync_status[firebase_uid] = {
            'active': True,
            'current_song': None,
            'last_update': datetime.now().isoformat(),
            'error_count': 0,
            'original_status': {'text': original_status_text, 'emoji': original_status_emoji}
        }
        
        print(f"Started sync for user {firebase_uid}")
        print(f"Original status: {original_status_emoji} | {original_status_text}")
        
        while sync_status.get(firebase_uid, {}).get('active', False):
            try:
                try:
                    playback = sp.current_playback()
                except spotipy.exceptions.SpotifyException as e:
                    if e.http_status == 401:
                        print(f"Spotify token expired for user {firebase_uid}, refreshing...")
                        new_token = refresh_spotify_token(firebase_uid, spotify_refresh_token)
                        if new_token:
                            sp = spotipy.Spotify(auth=new_token)
                            playback = sp.current_playback()
                        else:
                            raise Exception("Failed to refresh Spotify token")
                    else:
                        raise e
                
                if playback and playback.get("is_playing"):
                    track = playback["item"]
                    if track:
                        name = track["name"]
                        artists = track.get("artists", [])
                        if artists:
                            artist = ", ".join([a["name"] for a in artists])
                        else:
                            artist = "Unknown Artist"
                        
                        status_text = f"{name} – {artist}"
                        
                        if status_text != last_status:
                            try:
                                slack_client.users_profile_set(profile={
                                    "status_text": f"Listening to: {status_text}",
                                    "status_emoji": "🎵",
                                    "status_expiration": 0
                                })
                                last_status = status_text
                                sync_status[firebase_uid]['current_song'] = status_text
                                sync_status[firebase_uid]['last_update'] = datetime.now().isoformat()
                                print(f"Updated status for {firebase_uid}: {status_text}")
                            except SlackApiError as e:
                                print(f"Slack API error: {e.response['error']}")
                                error_count += 1
                else:
                    if last_status != original_status_text:
                        try:
                            slack_client.users_profile_set(profile={
                                "status_text": original_status_text,
                                "status_emoji": original_status_emoji,
                                "status_expiration": 0
                            })
                            last_status = original_status_text
                            sync_status[firebase_uid]['current_song'] = None
                            sync_status[firebase_uid]['last_update'] = datetime.now().isoformat()
                            print(f"Restored original status for {firebase_uid}")
                        except SlackApiError as e:
                            print(f"Slack API error: {e.response['error']}")
                            error_count += 1
                
                error_count = 0
                sync_status[firebase_uid]['error_count'] = 0
                
                time.sleep(30)
                
            except Exception as e:
                error_count += 1
                sync_status[firebase_uid]['error_count'] = error_count
                sync_status[firebase_uid]['last_update'] = datetime.now().isoformat()
                print(f"Sync error for user {firebase_uid}: {e}")
                
                if error_count > 3:
                    print(f"Too many errors for user {firebase_uid}, stopping sync")
                    try:
                        slack_client.users_profile_set(profile={
                            "status_text": original_status_text,
                            "status_emoji": original_status_emoji,
                            "status_expiration": 0
                        })
                    except:
                        pass
                    break
                
                time.sleep(10)
    
    except Exception as e:
        print(f"Critical error in sync thread for user {firebase_uid}: {e}")
        if firebase_uid in sync_status:
            sync_status[firebase_uid]['error'] = str(e)
    
    finally:
        if firebase_uid in sync_status:
            sync_status[firebase_uid]['active'] = False
        if firebase_uid in sync_threads:
            del sync_threads[firebase_uid]
        print(f"Sync stopped for user {firebase_uid}")

@app.route('/sync/start/<firebase_uid>', methods=['POST'])
def start_sync(firebase_uid):
    try:
        slack_token, spotify_token, spotify_refresh_token = get_user_tokens(firebase_uid)
        
        if not slack_token or not spotify_token:
            return jsonify({
                'error': 'Both Slack and Spotify accounts must be connected',
                'success': False
            }), 400
        
        if firebase_uid in sync_status:
            sync_status[firebase_uid]['active'] = False
            time.sleep(1)  
        
        sync_thread = threading.Thread(
            target=spotify_slack_sync,
            args=(firebase_uid,),
            daemon=True
        )
        sync_thread.start()
        sync_threads[firebase_uid] = sync_thread
        
        return jsonify({
            'success': True,
            'message': f'Sync started for user {firebase_uid}'
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'success': False
        }), 500

@app.route('/sync/stop/<firebase_uid>', methods=['POST'])
def stop_sync(firebase_uid):
    try:
        if firebase_uid in sync_status:
            sync_status[firebase_uid]['active'] = False
            
            if 'original_status' in sync_status[firebase_uid]:
                slack_token, _, _ = get_user_tokens(firebase_uid)
                if slack_token:
                    try:
                        slack_client = WebClient(token=slack_token)
                        original = sync_status[firebase_uid]['original_status']
                        slack_client.users_profile_set(profile={
                            "status_text": original['text'],
                            "status_emoji": original['emoji'],
                            "status_expiration": 0
                        })
                    except Exception as e:
                        print(f"Error restoring original status: {e}")
        
        return jsonify({
            'success': True,
            'message': f'Sync stopped for user {firebase_uid}'
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'success': False
        }), 500

@app.route('/sync/status/<firebase_uid>', methods=['GET'])
def get_sync_status(firebase_uid):
    """Get sync status for a specific user"""
    try:
        user_sync_status = sync_status.get(firebase_uid, {})
        sync_running = firebase_uid in sync_threads and sync_threads[firebase_uid].is_alive()
        
        return jsonify({
            'running': sync_running,
            'active': user_sync_status.get('active', False),
            'current_song': user_sync_status.get('current_song'),
            'last_update': user_sync_status.get('last_update'),
            'error_count': user_sync_status.get('error_count', 0),
            'error': user_sync_status.get('error')
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'running': False
        }), 500

@app.route('/sync/list', methods=['GET'])
def list_active_syncs():
    """List all active sync sessions"""
    try:
        active_syncs = {}
        for uid, status in sync_status.items():
            if status.get('active', False):
                active_syncs[uid] = {
                    'current_song': status.get('current_song'),
                    'last_update': status.get('last_update'),
                    'error_count': status.get('error_count', 0)
                }
        
        return jsonify({
            'active_syncs': active_syncs,
            'total_active': len(active_syncs)
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e)
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'active_syncs': len([uid for uid, status in sync_status.items() if status.get('active', False)]),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/user/tokens/<firebase_uid>', methods=['GET'])
def get_user_tokens_api(firebase_uid):
    try:
        slack_token, spotify_access_token, spotify_refresh_token = get_user_tokens(firebase_uid)
        return jsonify({
            'slack_token': bool(slack_token),
            'spotify_access_token': bool(spotify_access_token),
            'spotify_refresh_token': bool(spotify_refresh_token)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/sync/reset/<firebase_uid>', methods=['POST'])
def reset_sync(firebase_uid):
    try:
        if firebase_uid in sync_status:
            sync_status.pop(firebase_uid)
        if firebase_uid in sync_threads:
            sync_threads.pop(firebase_uid)
        return jsonify({'success': True, 'message': f'Sync data reset for {firebase_uid}'})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/spotify/now/<firebase_uid>', methods=['GET'])
def get_current_spotify_track(firebase_uid):
    try:
        _, spotify_token, spotify_refresh_token = get_user_tokens(firebase_uid)
        if not spotify_token:
            return jsonify({'error': 'No Spotify token found'}), 400

        sp = spotipy.Spotify(auth=spotify_token)
        try:
            playback = sp.current_playback()
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 401:
                new_token = refresh_spotify_token(firebase_uid, spotify_refresh_token)
                if not new_token:
                    return jsonify({'error': 'Unable to refresh token'}), 401
                sp = spotipy.Spotify(auth=new_token)
                playback = sp.current_playback()
            else:
                raise e

        if not playback or not playback.get("is_playing"):
            return jsonify({'is_playing': False})

        track = playback["item"]
        return jsonify({
            'is_playing': True,
            'name': track["name"],
            'artists': [a["name"] for a in track.get("artists", [])],
            'album': track["album"]["name"]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/slack/status/<firebase_uid>', methods=['POST'])
def set_slack_status(firebase_uid):
    try:
        slack_token, _, _ = get_user_tokens(firebase_uid)
        if not slack_token:
            return jsonify({'error': 'No Slack token found'}), 400

        data = request.get_json()
        status_text = data.get('text', '')
        status_emoji = data.get('emoji', '')

        slack_client = WebClient(token=slack_token)
        slack_client.users_profile_set(profile={
            "status_text": status_text,
            "status_emoji": status_emoji,
            "status_expiration": 0
        })

        if firebase_uid in sync_status:
            sync_status[firebase_uid]['original_status'] = {
                'text': status_text,
                'emoji': status_emoji
            }
        else:
            sync_status[firebase_uid] = {
                'original_status': {
                    'text': status_text,
                    'emoji': status_emoji
                },
                'active': False,
                'current_song': None,
                'error_count': 0,
                'last_update': datetime.now().isoformat()
            }

        return jsonify({'success': True, 'message': 'Slack status updated and saved as original'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

        
global_status = {}  # firebase_uid -> {'text': str, 'emoji': str, 'last_update': str}
status_threads = {}  # firebase_uid -> Thread
spotify_threads = {}  # firebase_uid -> Thread
spotify_active = {}   # firebase_uid -> bool
slack_worker_status = {}  # firebase_uid -> bool

def global_status_worker(firebase_uid):
    slack_worker_status[firebase_uid] = True
    while slack_worker_status.get(firebase_uid, False):
        if firebase_uid in global_status:
            slack_token, _, _ = get_user_tokens(firebase_uid)
            if slack_token:
                status = global_status[firebase_uid]
                try:
                    slack_client = WebClient(token=slack_token)
                    slack_client.users_profile_set(profile={
                        "status_text": status.get('text', ''),
                        "status_emoji": status.get('emoji', ''),
                        "status_expiration": 0
                    })
                    print(f"Global status updated for {firebase_uid}: {status.get('text', '')}")
                except SlackApiError as e:
                    print(f"Failed to update Slack status for {firebase_uid}: {e.response['error']}")
        time.sleep(30)
    print(f"Slack worker stopped for {firebase_uid}")

def spotify_pull_worker(firebase_uid):
    spotify_active[firebase_uid] = True
    slack_token, spotify_token, spotify_refresh_token = get_user_tokens(firebase_uid)
    sp = spotipy.Spotify(auth=spotify_token)

    while spotify_active.get(firebase_uid, False):
        try:
            playback = sp.current_playback()
            if playback and playback.get("is_playing"):
                track = playback["item"]
                if track:
                    song_text = f"{track['name']} – {', '.join(a['name'] for a in track.get('artists', []))}"
                    global_status[firebase_uid] = {
                        'text': f"Listening to: {song_text}",
                        'emoji': '🎵',
                        'last_update': datetime.now().isoformat()
                    }
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

@app.route('/global/status/<firebase_uid>', methods=['POST'])
def set_global_status(firebase_uid):
    data = request.get_json()
    text = data.get('text', '')
    emoji = data.get('emoji', '')

    global_status[firebase_uid] = {
        'text': text,
        'emoji': emoji,
        'last_update': datetime.now().isoformat()
    }

    if not slack_worker_status.get(firebase_uid, False):
        t = threading.Thread(target=global_status_worker, args=(firebase_uid,), daemon=True)
        t.start()
        status_threads[firebase_uid] = t

    return jsonify({'success': True, 'message': 'Global status updated'})

@app.route('/spotify/pull/start/<firebase_uid>', methods=['POST'])
def start_spotify_pull(firebase_uid):
    if not spotify_active.get(firebase_uid, False):
        spotify_active[firebase_uid] = True
        t = threading.Thread(target=spotify_pull_worker, args=(firebase_uid,), daemon=True)
        t.start()
        spotify_threads[firebase_uid] = t
    return jsonify({'success': True, 'message': 'Spotify pulling started'})

@app.route('/spotify/pull/stop/<firebase_uid>', methods=['POST'])
def stop_spotify_pull(firebase_uid):
    spotify_active[firebase_uid] = False
    return jsonify({'success': True, 'message': 'Spotify pulling stopped'})

@app.route('/spotify/pull/status/<firebase_uid>', methods=['GET'])
def spotify_pull_status_route(firebase_uid):
    active = spotify_active.get(firebase_uid, False)
    return jsonify({'success': True, 'pulling': active})

@app.route('/slack/worker/start/<firebase_uid>', methods=['POST'])
def start_slack_worker(firebase_uid):
    if not slack_worker_status.get(firebase_uid, False):
        slack_worker_status[firebase_uid] = True
        t = threading.Thread(target=global_status_worker, args=(firebase_uid,), daemon=True)
        t.start()
        status_threads[firebase_uid] = t
    return jsonify({"success": True, "message": "Slack worker started"})

@app.route('/slack/worker/stop/<firebase_uid>', methods=['POST'])
def stop_slack_worker(firebase_uid):
    slack_worker_status[firebase_uid] = False
    return jsonify({"success": True, "message": "Slack worker stopped"})

@app.route('/slack/worker/status/<firebase_uid>', methods=['GET'])
def slack_worker_status_route(firebase_uid):
    active = slack_worker_status.get(firebase_uid, False)
    return jsonify({"success": True, "active": active})

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

def check_if_source_exists(src):
    for s in existing_services:
        if src.lower() == s.lower():
            return(True)
    return(False)

@app.route('/api/set_client_status/<firebase_uid>', methods=['POST'])
def set_client_status(firebase_uid):
    try:
        id_token = request.headers.get('Authorization')
        if not id_token or not id_token.startswith('Bearer '):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        
        id_token = id_token.split('Bearer ')[1]

        decoded_token = auth.verify_id_token(id_token)
        uid_from_token = decoded_token.get('uid')

        if uid_from_token != firebase_uid:
            return jsonify({"error": "UID mismatch"}), 403

        data = request.get_json()
        name = data.get('name')
        artist = data.get('artist')
        source = data.get('source')

        if not all([name, artist, source]):
            return jsonify({"error": "Missing name, artist or source"}), 400

        field_name = f"last_{source.lower()}"
        update_data = {
            field_name: {
                "name": name,
                "artist": artist,
                "updated": datetime.now().isoformat()
            }
        }

        if check_if_source_exists(source) == False:
            return jsonify({"error": f"Non existent service ({source.lower()})"}), 400

        success = update_user_data(firebase_uid, update_data)
        if success:
            return jsonify({"status": "ok"}), 200
        else:
            return jsonify({"error": "Failed to update user data"}), 500

    except Exception as e:
        print(f"Error in set_client_status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/set_priority/<firebase_uid>', methods=['POST'])
def set_priority(firebase_uid):
    try:
        id_token = request.headers.get('Authorization')
        if not id_token or not id_token.startswith('Bearer '):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        
        id_token = id_token.split('Bearer ')[1]

        decoded_token = auth.verify_id_token(id_token)
        uid_from_token = decoded_token.get('uid')

        if uid_from_token != firebase_uid:
            return jsonify({"error": "UID mismatch"}), 403

        data = request.get_json()
        list = data.get('list')

        if not all([list]):
            return jsonify({"error": "Missing name, artist or source"}), 400

        str_list = ""

        for item in list:
            if check_if_source_exists(item) == False:
                return jsonify({"error": f"Non existent service ({item.lower()})"}), 400

            str_list = f"{str_list},{item}"
        
        if len(list) != len(existing_services):
            return jsonify({"error": f"Missing services"}), 400 



        field_name = f"priority"
        update_data = {
            field_name: {
                "list": str_list,
            }
        }

        success = update_user_data(firebase_uid, update_data)
        if success:
            return jsonify({"status": "ok"}), 200
        else:
            return jsonify({"error": "Failed to update user data"}), 500

    except Exception as e:
        print(f"Error in set_client_status: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=1605, debug=True)
