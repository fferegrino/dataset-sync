#!/usr/bin/env bash
# Install dataset-sync into its own virtualenv so it never touches the Python (or any other toolchain) the job uses.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="${RUNNER_TEMP:?}/dataset-sync-venv"

if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --quiet --upgrade pip==26.2.1
  "$venv/bin/python" -m pip install --quiet -r "$root/requirements.txt"
fi

echo "DATASET_SYNC_PYTHON=$venv/bin/python" >> "$GITHUB_ENV"
echo "DATASET_SYNC_ROOT=$root" >> "$GITHUB_ENV"
