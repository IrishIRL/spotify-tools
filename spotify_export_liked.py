import csv
import os
from pathlib import Path

import app_logger as log
from spotify_auth import get_spotify_access_token, spotify_get


ENV_FILE = ".env"
OUTPUT_FILE_NAME = "liked_songs.csv"
SCOPE = "user-library-read"


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


def track_to_row(item):
    track = item["track"]
    album = track["album"]

    return {
        "added_at": item["added_at"],
        "track_name": track["name"],
        "artists": ", ".join(artist["name"] for artist in track["artists"]),
        "album": album["name"],
        "release_date": album["release_date"],
        "duration_ms": track["duration_ms"],
        "popularity": track["popularity"],
        "explicit": track["explicit"],
        "spotify_url": track["external_urls"]["spotify"],
        "isrc": track.get("external_ids", {}).get("isrc", ""),
        "track_id": track["id"],
    }


def get_liked_songs(access_token):
    rows = []
    url = "https://api.spotify.com/v1/me/tracks?limit=50"
    page = 1

    while url:
        log.info(f"Fetching page {page}...")

        data = spotify_get(url, access_token)

        for item in data["items"]:
            rows.append(track_to_row(item))

        log.info(f"Fetched {len(rows)} songs so far.")

        url = data["next"]
        page += 1

    return rows


def write_csv(rows, output_dir):
    output_path = Path(output_dir) / OUTPUT_FILE_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "added_at",
        "track_name",
        "artists",
        "album",
        "release_date",
        "duration_ms",
        "popularity",
        "explicit",
        "spotify_url",
        "isrc",
        "track_id",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def main():
    config = get_config()

    access_token = get_spotify_access_token(
        config["client_id"],
        config["client_secret"],
        config["redirect_uri"],
        SCOPE,
    )

    rows = get_liked_songs(access_token)
    output_path = write_csv(rows, config["output_dir"])

    log.info(f"Exported {len(rows)} songs to {output_path}.")


if __name__ == "__main__":
    main()