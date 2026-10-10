"""Upload a dataset directory to every configured destination, updating the source of truth last."""
import argparse
import os
from datetime import date
from pathlib import Path

from dataset_sync import backends
from dataset_sync.files import fingerprint, load_state, matches_any, parse_patterns, prefix_digest, state_file
from dataset_sync.retry import with_retries


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


def upload_order(destinations, source):
    # If a mirror fails, the source still holds the old data, so the next run redoes the work and retries every
    # destination. Updating the source first would make the next run see no changes and leave the mirror behind.
    return sorted(destinations, key=lambda backend: backend.name == source)


def write_outputs(github_output, changed, uploaded):
    if github_output:
        with open(github_output, "a") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")
            handle.write(f"uploaded={','.join(uploaded)}\n")


def publish(destinations, directory, state_path, append_only=(), message=None, force=False, dry_run=False,
            github_output=None):
    if not destinations:
        raise SystemExit("No destinations configured")
    directory = Path(directory)
    state = load_state(state_path)
    if state is None and not force:
        raise SystemExit("No fetch record for this directory; run fetch first, or pass --force to upload anyway")

    after = fingerprint(directory)
    if not after:
        raise SystemExit(f"{directory} has no data files; refusing to upload")

    source = state["source"] if state else None
    changed = state is None or state["fingerprint"] != after
    if state and changed:
        added, removed, modified = describe_changes(state["fingerprint"], after)
        print(f"Added: {added or '-'}\nRemoved: {removed or '-'}\nModified: {modified or '-'}")
        violations = append_only_violations(state["fingerprint"], after, directory, append_only)
        if violations:
            raise SystemExit("Refusing to upload:\n  " + "\n  ".join(violations))
    elif state:
        print(f"No changes since fetching from {source}")

    if source and source not in {backend.name for backend in destinations}:
        print(f"Warning: {source} is the source of truth but not a destination, so the next run will start from the "
              f"same data again")

    # Check every destination before uploading anywhere, so a typo cannot leave them half updated.
    for backend in destinations:
        backend.validate()
        if not dry_run:
            backend.check_credentials()

    message = message or f"{date.today()} update"
    uploaded = []
    for backend in upload_order(destinations, source):
        if backend.name == source:
            wanted = changed or force
        else:
            wanted = changed or force or backend.idempotent
        if not wanted:
            print(f"Skipping {backend.name}: nothing changed")
            continue
        if dry_run:
            print(f"Dry run: would upload to {backend.name} {backend.dataset}")
            continue
        with_retries(lambda: backend.upload(directory, message), f"{backend.name} upload to {backend.dataset}")
        print(f"Uploaded to {backend.name} {backend.dataset}")
        uploaded.append(backend.name)

    write_outputs(github_output, changed, uploaded)
    return uploaded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="data", help="directory holding the dataset files")
    parser.add_argument("--state-dir", required=True, help="where fetch left its record")
    parser.add_argument("--append-only", default="", help="glob patterns of files that may only be appended to")
    parser.add_argument("--message", help="commit message / version notes; defaults to '<today> update'")
    parser.add_argument("--force", action="store_true", help="upload to every destination even if nothing changed")
    parser.add_argument("--dry-run", action="store_true", help="run every check but skip the uploads")
    backends.add_arguments(parser)
    args = parser.parse_args()

    publish(
        list(backends.from_arguments(args).values()),
        args.path,
        state_file(args.state_dir, args.path),
        append_only=parse_patterns(args.append_only),
        message=args.message,
        force=args.force,
        dry_run=args.dry_run,
        github_output=os.environ.get("GITHUB_OUTPUT"),
    )


if __name__ == "__main__":
    main()
