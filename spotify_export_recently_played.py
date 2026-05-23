import os
from pathlib import Path

from libs import app_logger as log
from libs.csv_export import write_csv
from libs.spotify_auth import get_spotify_access_token, spotify_get


ENV_FILE = ".env"
OUTPUT_FILE_NAME = "recently_played.csv"
SCOPE = "user-read-recently-played"

FIELDNAMES = [
    "played_at",
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


def get_recently_played_output_dir(output_dir):
    return Path(output_dir) / "recently_played"


def track_to_row(item):
    track = item.get("track") or {}
    album = track.get("album") or {}

    return {
        "played_at": item.get("played_at", ""),
        "track_name": track.get("name", ""),
        "artists": ", ".join(artist["name"] for artist in track.get("artists", [])),
        "album": album.get("name", ""),
        "release_date": album.get("release_date", ""),
        "duration_ms": track.get("duration_ms", ""),
        "popularity": track.get("popularity", ""),
        "explicit": track.get("explicit", ""),
        "spotify_url": track.get("external_urls", {}).get("spotify", ""),
        "isrc": track.get("external_ids", {}).get("isrc", ""),
        "track_id": track.get("id", ""),
    }


def get_recently_played(access_token):
    rows = []
    url = "https://api.spotify.com/v1/me/player/recently-played?limit=50"

    log.info("Fetching recently played tracks...")

    data = spotify_get(url, access_token)

    for item in data["items"]:
        rows.append(track_to_row(item))

    return rows


def main():
    config = get_config()

    access_token = get_spotify_access_token(
        config["client_id"],
        config["client_secret"],
        config["redirect_uri"],
        SCOPE,
    )

    rows = get_recently_played(access_token)

    output_path = write_csv(
        rows=rows,
        output_dir = config["output_dir"],
        file_name=OUTPUT_FILE_NAME,
        fieldnames=FIELDNAMES,
    )

    log.info(f"Exported {len(rows)} recently played tracks to {output_path}.")


if __name__ == "__main__":
    main()