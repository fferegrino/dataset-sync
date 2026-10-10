"""Upload a new Kaggle dataset version when the directory differs from its fetched fingerprint."""
import argparse
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path

from dataset_files import (
    METADATA_FILE,
    fingerprint,
    load_fingerprint,
    matches_any,
    parse_patterns,
    prefix_digest,
)

RETRY_DELAYS_SECONDS = (0, 15, 45)


def describe_changes(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    return added, removed, modified


def append_only_violations(before, after, directory, patterns):
    """Files matching the patterns may only grow, keeping their previous bytes as an exact prefix."""
    violations = []
    for name, entry in before.items():
        if not matches_any(name, patterns):
            continue
        if name not in after:
            violations.append(f"{name} was deleted")
        elif after[name]["size"] < entry["size"]:
            violations.append(f"{name} shrank from {entry['size']} to {after[name]['size']} bytes")
        elif after[name] != entry and prefix_digest(Path(directory) / name, entry["size"]) != entry["sha256"]:
            violations.append(f"{name} changed existing content instead of appending")
    return violations


def create_version(api, directory, message):
    last_error = None
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        if delay:
            time.sleep(delay)
        try:
            api.dataset_create_version(str(directory), message, dir_mode="zip", quiet=False)
            return
        except Exception as exc:
            last_error = exc
            print(f"Kaggle upload attempt {attempt} failed: {exc}", file=sys.stderr)
    raise SystemExit(f"Kaggle upload failed: {last_error}")


def write_output(github_output, changed):
    if github_output:
        with open(github_output, "a") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")


def upload(api, directory, fingerprint_path, metadata=None, append_only=(), message=None, dry_run=False,
           github_output=None):
    directory = Path(directory)
    before = load_fingerprint(fingerprint_path)
    after = fingerprint(directory)

    added, removed, modified = describe_changes(before, after)
    if not (added or removed or modified):
        print("No dataset changes; skipping the upload")
        write_output(github_output, False)
        return False

    print(f"Added: {added or '-'}\nRemoved: {removed or '-'}\nModified: {modified or '-'}")
    if not after:
        raise SystemExit(f"{directory} is empty; refusing to upload")
    violations = append_only_violations(before, after, directory, append_only)
    if violations:
        raise SystemExit("Refusing to upload:\n  " + "\n  ".join(violations))

    if metadata:
        if not Path(metadata).is_file():
            raise SystemExit(f"{metadata} is missing; Kaggle needs it to know which dataset to update")
        destination = directory / METADATA_FILE
        if Path(metadata).resolve() != destination.resolve():
            shutil.copyfile(metadata, destination)
    if not (directory / METADATA_FILE).exists():
        raise SystemExit(f"{directory / METADATA_FILE} is missing; Kaggle needs it to know which dataset to update")

    if dry_run:
        print("Dry run; not uploading")
    else:
        create_version(api, directory, message or f"{date.today()} update")
        print("Uploaded a new dataset version")
    write_output(github_output, True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="directory holding the dataset files")
    parser.add_argument("--fingerprint", required=True, help="fingerprint saved by fetch.py")
    parser.add_argument("--metadata", help=f"{METADATA_FILE} to copy into the directory before uploading")
    parser.add_argument("--append-only", default="", help="glob patterns of files that may only be appended to")
    parser.add_argument("--message", help="version notes; defaults to '<today> update'")
    parser.add_argument("--dry-run", action="store_true", help="run every check but skip the upload")
    args = parser.parse_args()

    api = None
    if not args.dry_run:
        if not os.environ.get("KAGGLE_KEY") or not os.environ.get("KAGGLE_USERNAME"):
            raise SystemExit("KAGGLE_KEY and KAGGLE_USERNAME must be set")
        from kaggle import api

    upload(
        api,
        args.directory,
        args.fingerprint,
        metadata=args.metadata,
        append_only=parse_patterns(args.append_only),
        message=args.message,
        dry_run=args.dry_run,
        github_output=os.environ.get("GITHUB_OUTPUT"),
    )


if __name__ == "__main__":
    main()
