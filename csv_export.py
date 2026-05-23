
import csv
from pathlib import Path


def get_available_output_path(output_dir, file_name):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / file_name

    if not output_path.exists():
        return output_path

    stem = output_path.stem
    suffix = output_path.suffix
    counter = 1

    while True:
        candidate = output_dir / f"{stem}_{counter}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def write_csv(rows, output_dir, file_name, fieldnames):
    output_path = get_available_output_path(output_dir, file_name)

    with open(output_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return output_path