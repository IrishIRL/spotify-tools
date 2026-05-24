import os
import sqlite3
import time
from pathlib import Path

from libs import app_logger as log
from libs.spotify_auth import SpotifyClient


ENV_FILE = "../.env"

SCOPE = (
    "user-read-currently-playing "
    "user-read-playback-state "
    "user-modify-playback-state"
)

DB_FILE_NAME = "shuffle_probe.db"

ACTIVE_POLL_SECONDS = 1

PAUSED_SHORT_POLL_SECONDS = 1
PAUSED_LONG_POLL_SECONDS = 30

PAUSED_LONG_THRESHOLD_SECONDS = 60
MAX_PAUSE_SECONDS = 900


def load_env(path=ENV_FILE):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing {path} file")

    with open(path, encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                raise ValueError(f"Invalid line in {path}:{line_number}: {line}")

            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def get_required_env(name):
    value = os.environ.get(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


def get_config():
    load_env()

    return {
        "client_id": get_required_env("SPOTIFY_CLIENT_ID"),
        "client_secret": get_required_env("SPOTIFY_CLIENT_SECRET"),
        "redirect_uri": get_required_env("SPOTIFY_REDIRECT_URI"),
        "output_dir": get_required_env("OUTPUT_DIR"),
    }


def get_database_path(output_dir):
    return Path(output_dir) / DB_FILE_NAME


def create_database(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shuffle_probe (
            observed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            track_id TEXT,
            track_name TEXT,
            artists TEXT,
            album TEXT,
            release_date TEXT,
            duration_ms INTEGER,
            popularity INTEGER,
            explicit TEXT,
            spotify_url TEXT,
            isrc TEXT
        )
    """)

    connection.commit()


def current_track_to_row(track):
    album = track.get("album") or {}

    return (
        track.get("id", ""),
        track.get("name", ""),
        ", ".join(artist["name"] for artist in track.get("artists", [])),
        album.get("name", ""),
        album.get("release_date", ""),
        track.get("duration_ms", None),
        track.get("popularity", None),
        str(track.get("explicit", "")),
        track.get("external_urls", {}).get("spotify", ""),
        track.get("external_ids", {}).get("isrc", ""),
    )


def insert_observed_track(connection, track):
    row = current_track_to_row(track)

    connection.execute("""
        INSERT INTO shuffle_probe (
            track_id,
            track_name,
            artists,
            album,
            release_date,
            duration_ms,
            popularity,
            explicit,
            spotify_url,
            isrc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, row)

    connection.commit()


def get_current_playback(spotify):
    return spotify.get("https://api.spotify.com/v1/me/player/currently-playing")


def skip_to_next_track(spotify):
    spotify.post("https://api.spotify.com/v1/me/player/next")


def wait_for_playback_resume(spotify):
    paused_since = time.time()
    was_long_pause_logged = False

    log.warn("Playback paused. Waiting for resume...")

    while True:
        paused_duration = time.time() - paused_since

        if paused_duration >= MAX_PAUSE_SECONDS:
            log.warn("Playback paused too long. Stopping script.")
            return False

        if paused_duration >= PAUSED_LONG_THRESHOLD_SECONDS:
            poll_seconds = PAUSED_LONG_POLL_SECONDS

            if not was_long_pause_logged:
                log.info(
                    f"Playback paused for over "
                    f"{PAUSED_LONG_THRESHOLD_SECONDS} seconds. "
                    f"Switching to {PAUSED_LONG_POLL_SECONDS}s polling."
                )

                was_long_pause_logged = True

        else:
            poll_seconds = PAUSED_SHORT_POLL_SECONDS

        time.sleep(poll_seconds)

        try:
            data = get_current_playback(spotify)

        except Exception as error:
            log.error(f"Playback check failed: {error}")
            continue

        if data and data.get("is_playing"):
            log.info("Playback resumed.")
            return True


def run_shuffle_probe(spotify, database_path):
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database_path) as connection:
        create_database(connection)

        log.info(f"Shuffle probe database: {database_path}")
        log.info("Running forever. Stop with Ctrl+C.")

        while True:
            try:
                data = get_current_playback(spotify)

            except Exception as error:
                log.error(f"Playback request failed: {error}")
                time.sleep(PAUSED_SHORT_POLL_SECONDS)
                continue

            if not data:
                resumed = wait_for_playback_resume(spotify)

                if not resumed:
                    break

                continue

            if not data.get("is_playing"):
                resumed = wait_for_playback_resume(spotify)

                if not resumed:
                    break

                continue

            track = data.get("item")

            if not track:
                log.warn("No current track found.")
                time.sleep(PAUSED_SHORT_POLL_SECONDS)
                continue

            if track.get("type") != "track":
                log.warn("Currently playing item is not a track.")
                time.sleep(PAUSED_SHORT_POLL_SECONDS)
                continue

            insert_observed_track(connection, track)

            artists = ", ".join(
                artist["name"]
                for artist in track.get("artists", [])
            )

            log.info(f"{track.get('name', '')} - {artists}")

            try:
                skip_to_next_track(spotify)

            except Exception as error:
                log.error(f"Failed to skip track: {error}")

            time.sleep(ACTIVE_POLL_SECONDS)

        log.info("Shuffle probe stopped.")


def main():
    config = get_config()

    spotify = SpotifyClient(
        config["client_id"],
        config["client_secret"],
        config["redirect_uri"],
        SCOPE,
    )

    database_path = get_database_path(config["output_dir"])

    run_shuffle_probe(spotify, database_path)


if __name__ == "__main__":
    main()