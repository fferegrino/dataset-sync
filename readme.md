# dataset-sync

Composite GitHub Actions that keep a dataset on Hugging Face and/or Kaggle up to date without storing the data in git:

- **`fetch`** downloads the current dataset from its source of truth (Hugging Face by default, or Kaggle) into a directory.
- **`publish`** uploads that directory to every configured destination if it changed since `fetch`, updating the source of truth last.
- **`ping`** pings a [healthchecks.io](https://healthchecks.io) check, so a run that stops happening or fails before `publish` raises an alert.

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
          huggingface-dataset: feregrino/yugioh-decks
          kaggle-dataset: ioexception/yugioh-decks
          kaggle-api-token: ${{ secrets.KAGGLE_API_TOKEN }}

      - uses: actions/setup-node@v5      # or setup-python, setup-go, ...
      - run: npm ci && node download.js  # updates the files in data/

      - uses: fferegrino/dataset-sync/publish@v1
        with:
          huggingface-dataset: feregrino/yugioh-decks
          huggingface-card: dataset-card.md
          kaggle-dataset: ioexception/yugioh-decks
          kaggle-metadata: dataset-metadata.json
          append-only: "*.csv"
          huggingface-token: ${{ secrets.HF_TOKEN }}
          kaggle-api-token: ${{ secrets.KAGGLE_API_TOKEN }}
          healthcheck-url: ${{ secrets.HEALTHCHECK_URL }}

      # Reports failures in the steps before publish, which never runs then.
      - uses: fferegrino/dataset-sync/ping@v1
        if: failure()
        with:
          url: ${{ secrets.HEALTHCHECK_URL }}
          status: fail
```

See [Writing scrapers for dataset-sync](writing-scrapers.md) for how to write the code that updates `data/`.

The repository keeps its code plus each destination's metadata (`dataset-metadata.json` for Kaggle, a dataset card for Hugging Face); the data directory should not be committed.

## How publishing decides what to upload

| Destination | Uploaded when |
| --- | --- |
| The source `fetch` read from | The data changed (or `force`) |
| Hugging Face as a mirror | Every run. The Hub skips files it already has, so an unchanged run costs nothing and a mirror that missed an update catches up |
| Kaggle as a mirror | The data changed (or `force`), because every Kaggle upload creates a new version |

Mirrors are uploaded before the source. If a mirror fails, the source still holds the old data, so the next run redoes the work and retries every destination. Every destination is validated (metadata files, credentials) before anything is uploaded.

Each upload is a full replacement, the same as a Kaggle version: on Hugging Face, files that no longer exist locally are deleted, apart from `README.md` and `.gitattributes`. `dataset-metadata.json`, `README.md` and `.gitattributes` at the top of the data directory are always treated as metadata, never data.

## Guards against losing data

- `fetch` compares what it downloaded with the source's own file list: every file must be present at the right size, and match its sha256 where the source has one (large files on Hugging Face). Otherwise the run stops and the data directory is left as it was, because anything missing would later look deleted.
- `publish` refuses to delete anything unless `allow-deletions` is set: neither files removed from the data directory since `fetch`, nor files a destination holds that the directory lacks (a mirror that drifted, or a file added by hand). This is checked for every destination before anything is uploaded.
- `append-only` lists glob patterns of files that may only grow. A run that deletes, shrinks or rewrites the existing bytes of one of them fails before uploading anywhere.

Kaggle keeps every dataset version and a Hugging Face dataset is a git repository, so even a bad upload can be rolled back.

Every call to a destination is retried after 15 and 45 seconds. Kaggle lists files 200 per page and rate limits the calls, so each page of a Kaggle listing is retried on its own, waiting up to four minutes, before the run gives up.

## Dead man's switch

A scheduled workflow that stops running, or keeps failing, is silent by default. With `healthcheck-url` set, `publish` pings the check after every successful run, whether or not anything was uploaded, and pings `<url>/fail` when it fails. A failure earlier in the job skips `publish`, so add the `ping` action with `if: failure()` and `status: fail` at the end of the job, as in the usage example; a failure in `publish` then pings `/fail` twice, which is harmless. Set the check's period to the schedule and its grace to a bit more than a normal run.

Pings never fail the job, and an empty URL skips them, so forks and workflows without the secret still run. Jobs that do not use `publish` can report success with `ping` too.

## Inputs

### `fetch`

| Input | Default | Description |
| --- | --- | --- |
| `from` | `huggingface` | Source of truth: `huggingface` or `kaggle` |
| `huggingface-dataset` | | `owner/name` of the Hugging Face dataset |
| `kaggle-dataset` | | `owner/slug` of the Kaggle dataset |
| `path` | `data` | Directory to fill; replaced entirely |
| `huggingface-token` | | Only needed for private datasets |
| `kaggle-api-token` | | Kaggle API token; needed when reading from Kaggle |
| `kaggle-username`, `kaggle-key` | | Legacy alternative to `kaggle-api-token` |

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
| `allow-deletions` | `false` | Let uploads remove files the fetched data or a destination has but the directory lacks |
| `huggingface-token` | | Write token, needed when uploading to Hugging Face |
| `kaggle-api-token` | | Kaggle API token; needed when uploading to Kaggle |
| `kaggle-username`, `kaggle-key` | | Legacy alternative to `kaggle-api-token` |
| `healthcheck-url` | | Ping URL of a healthchecks.io check; leave empty to skip the pings |

Outputs: `changed` (`'true'` when the data differs from what `fetch` downloaded) and `uploaded` (comma-separated destinations that received an upload).

### `ping`

| Input | Default | Description |
| --- | --- | --- |
| `url` | | Ping URL of the check; skipped with a warning when empty |
| `status` | `success` | `success`, `fail` or `start` |

## Adding a new destination

Run the workflow once from the existing source, e.g. `workflow_dispatch` with `source: kaggle` to seed an empty Hugging Face dataset; Hugging Face mirrors are synced on every run. A new Kaggle mirror needs one run with `force: true`.

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export HF_TOKEN=... KAGGLE_API_TOKEN=...
# Or legacy: export KAGGLE_USERNAME=... KAGGLE_KEY=...
.venv/bin/python -m dataset_sync.fetch --from huggingface --huggingface-dataset owner/name --path data --state-dir .state
# ... update the files in data/ ...
.venv/bin/python -m dataset_sync.publish --path data --state-dir .state --huggingface-dataset owner/name --dry-run
```

`fetch` refuses to overwrite a non-empty directory unless you pass `--replace`. `--dry-run` runs the local checks only: it needs no credentials, so it does not ask the destinations which files they hold.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```
