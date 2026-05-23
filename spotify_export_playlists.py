import os
import re

import app_logger as log
from csv_export import write_csv
from spotify_auth import get_spotify_access_token, spotify_get


ENV_FILE = ".env"
SCOPE = "playlist-read-private playlist-read-collaborative"

FIELDNAMES = [
    "playlist_name",
    "playlist_id",
    "added_at",
    "added_by",
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


def safe_file_name(name):
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip().strip(".")

    if not name:
        return "playlist"

    return f"{name}.csv"


def get_current_user_playlists(access_token):
    playlists = []
    url = "https://api.spotify.com/v1/me/playlists?limit=50"
    page = 1

    while url:
        log.info(f"Fetching playlists page {page}...")

        data = spotify_get(url, access_token)
        playlists.extend(data["items"])

        log.info(f"Fetched {len(playlists)} playlists so far.")

        url = data["next"]
        page += 1

    return playlists


def track_to_row(playlist, item):
    track = item.get("track")

    if not track:
        return None

    if track.get("type") != "track":
        return None

    album = track["album"]
    added_by = item.get("added_by") or {}

    return {
        "playlist_name": playlist["name"],
        "playlist_id": playlist["id"],
        "added_at": item.get("added_at", ""),
        "added_by": added_by.get("id", ""),
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


def get_playlist_tracks(access_token, playlist):
    rows = []
    url = f"https://api.spotify.com/v1/playlists/{playlist['id']}/tracks?limit=100"
    page = 1

    while url:
        log.info(f"Fetching '{playlist['name']}' page {page}...")

        data = spotify_get(url, access_token)

        for item in data["items"]:
            row = track_to_row(playlist, item)

            if row:
                rows.append(row)

        log.info(f"Fetched {len(rows)} tracks from '{playlist['name']}' so far.")

        url = data["next"]
        page += 1

    return rows


def main():
    config = get_config()

    access_token = get_spotify_access_token(
        config["client_id"],
        config["client_secret"],
        config["redirect_uri"],
        SCOPE,
    )

    playlists = get_current_user_playlists(access_token)

    for playlist in playlists:
        rows = get_playlist_tracks(access_token, playlist)
        file_name = safe_file_name(playlist["name"])

        output_path = write_csv(
            rows=rows,
            output_dir=config["output_dir"],
            file_name=file_name,
            fieldnames=FIELDNAMES,
        )

        log.info(f"Exported {len(rows)} tracks from '{playlist['name']}' to {output_path}.")


if __name__ == "__main__":
    main()