"""Replace a directory with the current dataset from its source of truth and record what was fetched."""
import argparse
import os
import shutil
import tempfile
from pathlib import Path

from dataset_sync import backends
from dataset_sync.files import fingerprint, save_state, state_file, strip_non_data
from dataset_sync.retry import with_retries


def fetch(backend, directory, state_path, replace=False):
    target = Path(directory)
    if target.exists() and any(target.iterdir()) and not replace:
        raise SystemExit(f"{target} is not empty; pass --replace to overwrite it with the {backend.name} version")

    target.parent.mkdir(parents=True, exist_ok=True)
    # Stage next to the target so the final swap is a rename on the same filesystem.
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".dataset-sync-fetch-") as scratch:
        staging = Path(scratch) / "staging"

        def download():
            # Each attempt starts clean so a half-finished download never leaks into the next one.
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            backend.download(staging)

        with_retries(download, f"{backend.name} download of {backend.dataset}")
        strip_non_data(staging)
        prints = fingerprint(staging)
        if not prints:
            raise SystemExit(f"{backend.name} returned no files for {backend.dataset}; refusing to continue")

        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)

    save_state(state_path, {"source": backend.name, "dataset": backend.dataset, "fingerprint": prints})
    total = sum(entry["size"] for entry in prints.values())
    print(f"Fetched {len(prints)} files ({total / 1_048_576:.1f} MiB) from {backend.name} {backend.dataset} into {target}")
    return prints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", default="huggingface", choices=("huggingface", "kaggle"))
    parser.add_argument("--path", default="data", help="directory to fill with the dataset files")
    parser.add_argument("--state-dir", required=True, help="where to leave the record publish reads")
    parser.add_argument("--replace", action="store_true", help="overwrite a non-empty directory")
    backends.add_arguments(parser)
    args = parser.parse_args()

    configured = backends.from_arguments(args)
    if args.source not in configured:
        raise SystemExit(f"--from {args.source} needs --{args.source}-dataset")
    fetch(configured[args.source], args.path, state_file(args.state_dir, args.path), replace=args.replace)


if __name__ == "__main__":
    main()
