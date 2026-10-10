import fnmatch
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Files the hosting services use to describe a dataset. They are managed per destination and never treated as data.
METADATA_NAMES = frozenset({"dataset-metadata.json", "README.md", ".gitattributes"})
# huggingface_hub keeps download bookkeeping here when downloading into a local directory.
IGNORED_DIRS = frozenset({".cache"})
CHUNK_SIZE = 1 << 20


def data_files(directory):
    """Yield (relative posix path, path) for every data file, skipping top-level metadata."""
    root = Path(directory)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or relative.parts[0] in IGNORED_DIRS:
            continue
        if len(relative.parts) == 1 and relative.name in METADATA_NAMES:
            continue
        yield relative.as_posix(), path


def strip_non_data(directory):
    root = Path(directory)
    for name in METADATA_NAMES:
        (root / name).unlink(missing_ok=True)
    for name in IGNORED_DIRS:
        shutil.rmtree(root / name, ignore_errors=True)


def file_digest(path):
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def prefix_digest(path, length):
    digest = hashlib.sha256()
    remaining = length
    with open(path, "rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def fingerprint(directory):
    return {
        name: {"size": path.stat().st_size, "sha256": file_digest(path)}
        for name, path in data_files(directory)
    }


@contextmanager
def staged(directory, extras=None):
    """A throwaway copy of the data files plus destination-specific extras, so uploads never touch the data directory."""
    directory = Path(directory)
    # Staging beside the data keeps it on the same filesystem, so files can be hard-linked instead of copied.
    with tempfile.TemporaryDirectory(dir=directory.parent, prefix=".dataset-sync-stage-") as scratch:
        root = Path(scratch)
        for name, path in data_files(directory):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, target)
            except OSError:
                shutil.copy2(path, target)
        for name, source in (extras or {}).items():
            shutil.copyfile(source, root / name)
        yield root


def state_file(state_dir, data_directory):
    """Where fetch leaves its record for publish; keyed by data directory so one job can sync several datasets."""
    key = hashlib.sha1(str(Path(data_directory).resolve()).encode()).hexdigest()[:16]
    return Path(state_dir) / f"{key}.json"


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def load_state(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None


def parse_patterns(value):
    """Split a whitespace- or comma-separated list of glob patterns."""
    return [pattern for pattern in (value or "").replace(",", " ").split() if pattern]


def matches_any(name, patterns):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
