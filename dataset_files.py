import fnmatch
import hashlib
import json
from pathlib import Path

# Kaggle reads this file to configure the upload; it is not part of the dataset contents.
METADATA_FILE = "dataset-metadata.json"
CHUNK_SIZE = 1 << 20


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
    root = Path(directory)
    return {
        path.relative_to(root).as_posix(): {"size": path.stat().st_size, "sha256": file_digest(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != METADATA_FILE
    }


def save_fingerprint(prints, path):
    Path(path).write_text(json.dumps(prints, indent=2, sort_keys=True))


def load_fingerprint(path):
    return json.loads(Path(path).read_text())


def parse_patterns(value):
    """Split a whitespace- or comma-separated list of glob patterns."""
    return [pattern for pattern in value.replace(",", " ").split() if pattern]


def matches_any(name, patterns):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
