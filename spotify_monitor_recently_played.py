import os
import sqlite3
import time
from pathlib import Path

from libs import app_logger as log
from libs.spotify_auth import get_spotify_access_token, spotify_get


ENV_FILE = ".env"
SCOPE = "user-read-recently-played"

DB_FILE_NAME = "listening_history.db"
POLL_INTERVAL_SECONDS = 1800


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
        CREATE TABLE IF NOT EXISTS listening_history (
            played_at TEXT NOT NULL,
            track_id TEXT NOT NULL,
            track_name TEXT,
            artists TEXT,
            album TEXT,
            release_date TEXT,
            duration_ms INTEGER,
            popularity INTEGER,
            explicit TEXT,
            spotify_url TEXT,
            isrc TEXT,
            PRIMARY KEY (played_at, track_id)
        )
    """)

    connection.commit()


def recently_played_item_to_row(item):
    track = item.get("track") or {}
    album = track.get("album") or {}

    return (
        item.get("played_at", ""),
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


def get_recently_played_rows(access_token):
    url = "https://api.spotify.com/v1/me/player/recently-played?limit=50"
    data = spotify_get(url, access_token)

    rows = []

    for item in data["items"]:
        row = recently_played_item_to_row(item)

        played_at = row[0]
        track_id = row[1]

        if played_at and track_id:
            rows.append(row)

    rows.sort(key=lambda row: row[0])

    return rows


def insert_rows(connection, rows):
    cursor = connection.cursor()

    cursor.executemany("""
        INSERT OR IGNORE INTO listening_history (
            played_at,
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)

    connection.commit()

    return cursor.rowcount


def monitor_recently_played(access_token, database_path):
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database_path) as connection:
        create_database(connection)

        log.info(f"Listening history database: {database_path}")
        log.info(f"Polling every {POLL_INTERVAL_SECONDS} seconds.")

        while True:
            rows = get_recently_played_rows(access_token)
            inserted_count = insert_rows(connection, rows)

            log.info(f"Fetched {len(rows)} recently played tracks. Added {inserted_count} new rows.")

            time.sleep(POLL_INTERVAL_SECONDS)


def main():
    config = get_config()

    access_token = get_spotify_access_token(
        config["client_id"],
        config["client_secret"],
        config["redirect_uri"],
        SCOPE,
    )

    database_path = get_database_path(config["output_dir"])

    monitor_recently_played(access_token, database_path)


if __name__ == "__main__":
    main()