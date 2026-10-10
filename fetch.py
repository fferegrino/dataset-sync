"""Replace a directory with the latest version of a Kaggle dataset and fingerprint its files."""
import argparse
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from dataset_files import fingerprint, save_fingerprint

RETRY_DELAYS_SECONDS = (0, 15, 45)


def download_archive(api, dataset, destination):
    last_error = None
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        if delay:
            time.sleep(delay)
        try:
            api.dataset_download_files(dataset, path=str(destination), force=True, quiet=True, unzip=False)
        except Exception as exc:
            last_error = exc
            print(f"Kaggle download attempt {attempt} failed: {exc}", file=sys.stderr)
            continue

        archives = sorted(Path(destination).glob("*.zip"))
        if len(archives) == 1:
            return archives[0]
        last_error = f"expected one archive, found {[archive.name for archive in archives]}"
        print(f"Kaggle download attempt {attempt} failed: {last_error}", file=sys.stderr)

    raise SystemExit(f"Kaggle download of {dataset} failed: {last_error}")


def fetch(api, dataset, directory, fingerprint_path, replace=False):
    target = Path(directory)
    if target.exists() and any(target.iterdir()) and not replace:
        raise SystemExit(f"{target} is not empty; pass --replace to overwrite it with the Kaggle version")

    target.parent.mkdir(parents=True, exist_ok=True)
    # Stage next to the target so the final swap is a rename on the same filesystem.
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".fetch-") as scratch:
        scratch = Path(scratch)
        archive = download_archive(api, dataset, scratch / "download")
        staging = scratch / "staging"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)

        prints = fingerprint(staging)
        if not prints:
            raise SystemExit(f"Kaggle returned no files for {dataset}; refusing to continue")

        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)

    save_fingerprint(prints, fingerprint_path)
    total = sum(entry["size"] for entry in prints.values())
    print(f"Fetched {len(prints)} files ({total / 1_048_576:.1f} MiB) from {dataset} into {target}")
    return prints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="owner/slug of the Kaggle dataset")
    parser.add_argument("directory", help="directory to fill with the dataset files")
    parser.add_argument("--fingerprint", required=True, help="where to save the file fingerprint")
    parser.add_argument("--replace", action="store_true", help="overwrite a non-empty directory")
    args = parser.parse_args()

    if not os.environ.get("KAGGLE_KEY") or not os.environ.get("KAGGLE_USERNAME"):
        raise SystemExit("KAGGLE_KEY and KAGGLE_USERNAME must be set")
    from kaggle import api

    fetch(api, args.dataset, args.directory, args.fingerprint, replace=args.replace)


if __name__ == "__main__":
    main()
