import os
from pathlib import Path

import app_logger as log
from csv_export import write_csv
from spotify_auth import get_spotify_access_token, spotify_get


ENV_FILE = ".env"
SCOPE = "user-top-read"

FIELDNAMES = [
    "rank",
    "time_range",
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


def track_to_row(track, rank, time_range):
    album = track.get("album") or {}

    return {
        "rank": rank,
        "time_range": time_range,
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


def get_top_tracks(access_token, time_range):
    rows = []
    url = f"https://api.spotify.com/v1/me/top/tracks?limit=50&time_range={time_range}"

    log.info(f"Fetching top tracks for {time_range}...")

    data = spotify_get(url, access_token)

    for index, track in enumerate(data["items"], start=1):
        rows.append(track_to_row(track, index, time_range))

    return rows


def main():
    config = get_config()

    access_token = get_spotify_access_token(
        config["client_id"],
        config["client_secret"],
        config["redirect_uri"],
        SCOPE,
    )

    output_dir = config["output_dir"]

    for time_range in ["short_term", "medium_term", "long_term"]:
        rows = get_top_tracks(access_token, time_range)

        output_path = write_csv(
            rows=rows,
            output_dir=output_dir,
            file_name=f"top_tracks_{time_range}.csv",
            fieldnames=FIELDNAMES,
        )

        log.info(f"Exported {len(rows)} top tracks to {output_path}.")


if __name__ == "__main__":
    main()