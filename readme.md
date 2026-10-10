# kaggle-dataset-refresh

A composite GitHub Action that keeps a Kaggle dataset up to date without storing the data in git. Each run downloads the latest version of the dataset, runs your update command over the files, and uploads a new version only if a file changed.

Kaggle versions replace the whole dataset, so the action always works on a full copy: whatever your command leaves in the data directory becomes the next version.

## Usage

```yaml
name: Refresh dataset

on:
  schedule:
    - cron: "0 11 * * *"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: dataset
  cancel-in-progress: false

jobs:
  refresh:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v7
      - uses: fferegrino/kaggle-dataset-refresh@v1
        with:
          dataset: ioexception/yugioh-decks
          command: python download.py
          append-only: "*.csv"
          kaggle-key: ${{ secrets.KAGGLE_KEY }}
```

The repository keeps its code, its `requirements.txt` and a `dataset-metadata.json` at the root; the data directory should not be committed.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `dataset` | | `owner/slug` of the Kaggle dataset |
| `command` | | Command that updates the files in `data-directory` |
| `working-directory` | `.` | Directory to run `command` from |
| `data-directory` | `data` | Where the dataset files are downloaded, relative to the repository root |
| `metadata` | `dataset-metadata.json` | Copied into the data directory before uploading |
| `append-only` | | Glob patterns of files that may only be appended to. A run that deletes, shrinks or rewrites one of them fails instead of uploading |
| `requirements` | `requirements.txt` | Installed if it exists |
| `python-version` | `3.13` | |
| `kaggle-username` | `ioexception` | |
| `kaggle-key` | | Kaggle API key |

The `changed` output is `'true'` when a new version was uploaded.

## Running locally

```bash
pip install -r requirements.txt
export KAGGLE_USERNAME=... KAGGLE_KEY=...
python fetch.py ioexception/yugioh-decks data --fingerprint fingerprint.json
# ... update the files in data/ ...
python upload.py data --fingerprint fingerprint.json --metadata dataset-metadata.json --append-only "*.csv" --dry-run
```

`fetch.py` refuses to overwrite a non-empty directory unless you pass `--replace`.

## Tests

```bash
python -m unittest discover -s tests
```
