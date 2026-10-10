# dataset-sync

Two composite GitHub Actions that keep a dataset on Hugging Face and/or Kaggle up to date without storing the data in git:

- **`fetch`** downloads the current dataset from its source of truth (Hugging Face by default, or Kaggle) into a directory.
- **`publish`** uploads that directory to every configured destination if it changed since `fetch`, updating the source of truth last.

Anything can run between them, in any language: the actions install their own dependencies into a private virtualenv and leave the job's toolchain alone.

## Usage

```yaml
name: Refresh dataset

on:
  schedule:
    - cron: "0 11 * * *"
  workflow_dispatch:
    inputs:
      source:
        description: Where to read the current dataset from
        type: choice
        options: [huggingface, kaggle]
        default: huggingface

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

      - uses: fferegrino/dataset-sync/fetch@v1
        with:
          from: ${{ inputs.source || 'huggingface' }}
          huggingface-dataset: fferegrino/yugioh-decks
          kaggle-dataset: ioexception/yugioh-decks
          kaggle-username: ioexception
          kaggle-key: ${{ secrets.KAGGLE_KEY }}

      - uses: actions/setup-node@v5      # or setup-python, setup-go, ...
      - run: npm ci && node download.js  # updates the files in data/

      - uses: fferegrino/dataset-sync/publish@v1
        with:
          huggingface-dataset: fferegrino/yugioh-decks
          huggingface-card: dataset-card.md
          kaggle-dataset: ioexception/yugioh-decks
          kaggle-metadata: dataset-metadata.json
          append-only: "*.csv"
          huggingface-token: ${{ secrets.HF_TOKEN }}
          kaggle-username: ioexception
          kaggle-key: ${{ secrets.KAGGLE_KEY }}
```

The repository keeps its code plus each destination's metadata (`dataset-metadata.json` for Kaggle, a dataset card for Hugging Face); the data directory should not be committed.

## How publishing decides what to upload

| Destination | Uploaded when |
| --- | --- |
| The source `fetch` read from | The data changed (or `force`) |
| Hugging Face as a mirror | Every run. The Hub skips files it already has, so an unchanged run costs nothing and a mirror that missed an update catches up |
| Kaggle as a mirror | The data changed (or `force`), because every Kaggle upload creates a new version |

Mirrors are uploaded before the source. If a mirror fails, the source still holds the old data, so the next run redoes the work and retries every destination. Every destination is validated (metadata files, credentials) before anything is uploaded.

Each upload is a full replacement, the same as a Kaggle version: on Hugging Face, files that no longer exist locally are deleted, apart from `README.md` and `.gitattributes`. `dataset-metadata.json`, `README.md` and `.gitattributes` at the top of the data directory are always treated as metadata, never data.

`append-only` lists glob patterns of files that may only grow. A run that deletes, shrinks or rewrites the existing bytes of one of them fails before uploading anywhere.

## Inputs

### `fetch`

| Input | Default | Description |
| --- | --- | --- |
| `from` | `huggingface` | Source of truth: `huggingface` or `kaggle` |
| `huggingface-dataset` | | `owner/name` of the Hugging Face dataset |
| `kaggle-dataset` | | `owner/slug` of the Kaggle dataset |
| `path` | `data` | Directory to fill; replaced entirely |
| `huggingface-token` | | Only needed for private datasets |
| `kaggle-username`, `kaggle-key` | | Needed when reading from Kaggle |

### `publish`

| Input | Default | Description |
| --- | --- | --- |
| `huggingface-dataset` | | Leave empty to skip Hugging Face. The dataset repository must already exist |
| `huggingface-card` | | Uploaded as `README.md`; leave empty to keep the card already on the Hub |
| `kaggle-dataset` | | Leave empty to skip Kaggle |
| `kaggle-metadata` | `dataset-metadata.json` | Its `id` must match `kaggle-dataset` |
| `path` | `data` | |
| `append-only` | | Space or comma separated glob patterns |
| `message` | `<today> update` | Commit message / version notes |
| `force` | `false` | Upload everywhere even if nothing changed |
| `huggingface-token` | | Write token, needed when uploading to Hugging Face |
| `kaggle-username`, `kaggle-key` | | Needed when uploading to Kaggle |

Outputs: `changed` (`'true'` when the data differs from what `fetch` downloaded) and `uploaded` (comma-separated destinations that received an upload).

## Adding a new destination

Run the workflow once from the existing source, e.g. `workflow_dispatch` with `source: kaggle` to seed an empty Hugging Face dataset; Hugging Face mirrors are synced on every run. A new Kaggle mirror needs one run with `force: true`.

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export HF_TOKEN=... KAGGLE_USERNAME=... KAGGLE_KEY=...
.venv/bin/python -m dataset_sync.fetch --from huggingface --huggingface-dataset owner/name --path data --state-dir .state
# ... update the files in data/ ...
.venv/bin/python -m dataset_sync.publish --path data --state-dir .state --huggingface-dataset owner/name --dry-run
```

`fetch` refuses to overwrite a non-empty directory unless you pass `--replace`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```
