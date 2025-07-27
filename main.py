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
from firebase_admin import credentials, firestore
from datetime import datetime

load_dotenv()

app = Flask(__name__)

cred = credentials.Certificate(os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_PATH"))
firebase_admin.initialize_app(cred)
db = firestore.client()

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
            redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
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

if __name__ == "__main__":
    print("Starting Spotify-Slack Sync Backend Server on port 1605...")
    print("Available endpoints:")
    print("  POST /sync/start/<firebase_uid> - Start sync for user")
    print("  POST /sync/stop/<firebase_uid> - Stop sync for user") 
    
    app.run(host="0.0.0.0", port=1605, debug=True)