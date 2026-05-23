# Spotify Tools

Python scripts for exporting Spotify data to CSV. No external libraries used.

## Setup

Create `.env` in the project root:

```text
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
OUTPUT_DIR=C:\Users\nub\Downloads
```

Register the same redirect URI in Spotify Developer Dashboard.

For local scripts, this is usually recommended:
```
http://127.0.0.1:8888/callback
```
For a hosted/server app, you can use another HTTPS callback URL instead, for example:
```
https://your-domain.com/callback
```
The value in .env must exactly match one of the redirect URIs configured in Spotify.

## Scripts
```
python spotify_export_liked.py
python spotify_export_playlists.py
python spotify_export_top_tracks.py
python spotify_export_recently_played.py
python spotify_monitor_recently_played.py
python spotify_export_listening_history.py
```

## Output
Liked songs:
```
OUTPUT_DIR/liked_songs.csv
```
Playlists:
```
OUTPUT_DIR/playlists/<playlist name>.csv
```
Listen history monitoring:
```
OUTPUT_DIR/listening_history.db
OUTPUT_DIR/listening_history.csv
```
Existing files are not overwritten. New files get suffixes like:
```
liked_songs_1.csv
```

## Notes
`spotify_monitor_recently_played.py` runs continuously and periodically checks Spotify's recently played history.<br>
Based on testing, tracks usually appear there only after they are fully or mostly listened to; quickly skipped tracks may not be stored by Spotify.<br>
If you manually skip track to the end, the song is also added to the history.<br>
The script saves whatever Spotify returns into `listening_history.db` and ignores duplicates automatically.

`spotify_export_listening_history.py` exports that SQLite history to CSV.

