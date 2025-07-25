import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
import os

load_dotenv()

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

scope = "user-read-playback-state"
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET,
    redirect_uri=SPOTIPY_REDIRECT_URI,
    scope=scope
))

slack_client = WebClient(token=SLACK_BOT_TOKEN)

def set_slack_status(text, emoji="🎵"):
    try:
        slack_client.users_profile_set(profile={
            "status_text": text,
            "status_emoji": emoji,
            "status_expiration": 0
        })
        print(f"Set status to: {emoji} | {text}")
    except SlackApiError as e:
        print("Slack-Fehler:", e.response["error"])

def clear_status():
    set_slack_status("", "")

def set_previous_status(text, emoji):
    try:
        slack_client.users_profile_set(profile={
            "status_text": text,
            "status_emoji": emoji,
            "status_expiration": 0
        })
        print(f"Set status to: {emoji} | {text}")
    except SlackApiError as e:
        print("Slack-Fehler:", e.response["error"])

last_track = None

previous_status_early = slack_client.users_profile_get()
previous_status_text = previous_status_early["profile"]["status_text"]
previous_status_emoji = previous_status_early["profile"]["status_emoji"]

print(f"Your current status is: {previous_status_emoji} | {previous_status_text}")

try:
    while True:
        playback = sp.current_playback()
        if playback and playback.get("is_playing"):
            track = playback["item"]
            name = track["name"]
            artist = ", ".join([a["name"] for a in track["artists"]])
            status_text = f"{name} – {artist}"

            if status_text != last_track:
                set_slack_status("Listening to: " + status_text)
                last_track = status_text
            else:
                print("Song/Status didn't change")
        else:
            set_previous_status(previous_status_text, previous_status_emoji)
            last_track = None

        print("Checking again in 20s")
        time.sleep(5)
        print("Checking again in 15")
        time.sleep(5)
        print("Checking again in 10")
        time.sleep(5)
        print("Checking again in 5s")
        time.sleep(5)
        print("Checking again")

except KeyboardInterrupt:
    set_previous_status(previous_status_text, previous_status_emoji)
    print("Finished.")
