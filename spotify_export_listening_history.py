import os
import sqlite3
from pathlib import Path

from libs import app_logger as log
from libs import csv_export


ENV_FILE = ".env"
DB_FILE_NAME = "listening_history.db"
OUTPUT_FILE_NAME = "listening_history.csv"

FIELDNAMES = [
    "played_at",
    "track_id",
    "track_name",
    "artists",
    "album",
    "release_date",
    "duration_ms",
    "popularity",
    "explicit",
    "spotify_url",
    "isrc",
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
        "output_dir": get_required_env("OUTPUT_DIR"),
    }


def get_database_path(output_dir):
    return Path(output_dir) / DB_FILE_NAME


def get_history_rows(database_path):
    if not database_path.exists():
        raise FileNotFoundError(f"Database file does not exist: {database_path}")

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row

        cursor = connection.execute("""
            SELECT
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
            FROM listening_history
            ORDER BY played_at ASC
        """)

        return [dict(row) for row in cursor.fetchall()]


def main():
    config = get_config()
    database_path = get_database_path(config["output_dir"])

    rows = get_history_rows(database_path)

    output_path = csv_export.write_csv(
        rows=rows,
        output_dir=config["output_dir"],
        file_name=OUTPUT_FILE_NAME,
        fieldnames=FIELDNAMES,
    )

    log.info(f"Exported {len(rows)} listening history rows to {output_path}.")


if __name__ == "__main__":
    main()