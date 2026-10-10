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


def wants_upload(backend, source, changed, force):
    if backend.name == source:
        return changed or force
    return changed or force or backend.idempotent


def write_outputs(github_output, changed, uploaded):
    if github_output:
        with open(github_output, "a") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")
            handle.write(f"uploaded={','.join(uploaded)}\n")


def publish(destinations, directory, state_path, append_only=(), message=None, force=False, dry_run=False,
            allow_deletions=False, github_output=None):
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
        if removed and not allow_deletions:
            violations += [f"{name} was deleted (pass allow-deletions to publish deletions)" for name in removed
                           if f"{name} was deleted" not in violations]
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

    planned = [backend for backend in upload_order(destinations, source)
               if wants_upload(backend, source, changed, force)]
    for backend in destinations:
        if backend not in planned:
            print(f"Skipping {backend.name}: nothing changed")

    if dry_run:
        for backend in planned:
            print(f"Dry run: would upload to {backend.name} {backend.dataset} (deletions not checked)")
        write_outputs(github_output, changed, [])
        return []

    # Uploads replace whole datasets, so a destination holding files the directory lacks would lose them. That is
    # how a mirror that drifted, or a source edited by hand, loses data; refuse unless deletions are intended.
    if not allow_deletions:
        doomed = []
        for backend in planned:
            pending = with_retries(lambda: backend.pending_deletions(directory),
                                   f"{backend.name} file listing of {backend.dataset}")
            doomed += [f"{name} would be deleted from {backend.name}" for name in pending]
        if doomed:
            raise SystemExit("Refusing to upload:\n  " + "\n  ".join(doomed)
                             + "\nPass allow-deletions if these files should go.")

    message = message or f"{date.today()} update"
    uploaded = []
    for backend in planned:
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
    parser.add_argument("--dry-run", action="store_true",
                        help="run the local checks but skip the uploads and the destination deletion check")
    parser.add_argument("--allow-deletions", action="store_true",
                        help="let uploads remove files the destinations have but the directory lacks")
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
        allow_deletions=args.allow_deletions,
        github_output=os.environ.get("GITHUB_OUTPUT"),
    )


if __name__ == "__main__":
    main()
