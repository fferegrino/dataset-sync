# Writing scrapers for dataset-sync

`fetch` and `publish` only move files. Whether a dataset stays correct depends on the code that runs between them. These practices come from moving five scrapers onto dataset-sync (`yu-gi-oh`, `yu-gi-oh-decks`, `humble-scraping`, `mananeras` and `uk-blogs`), and most of them fix a bug one of those scrapers had.

## The contract

- When your code starts, `data/` holds the whole current dataset and nothing else. When it ends, whatever is in `data/` is the new dataset. There is no other state: nothing in git, nothing carried over from a previous run.
- Read and write `data/` only for the dataset itself. Put downloads, raw HTML, temporary files and bookkeeping somewhere else, such as `raw/` in mananeras or `.fetched.json` in uk-blogs. Anything left in `data/` gets published.
- Do not write `README.md`, `dataset-metadata.json` or `.gitattributes` at the top of `data/`. They are treated as metadata and never uploaded as data. The card and Kaggle metadata live at the repository root and are passed to `publish`.
- Exit non-zero when something is wrong. `publish` then does not run, nothing is uploaded, the next run starts again from the published data, and the healthcheck reports the failure.

## Keep the cursor in the data

An incremental scraper needs to know where it stopped. That cursor must be part of the published dataset, because there is nowhere else for it to survive between runs, and a `source: kaggle` run has to find it on Kaggle too.

- Prefer deriving it from the data itself. mananeras compares URL slugs with the transcript filenames already in `data/`, and uk-blogs compares URLs with the `url` field of the posts already in `data/`. Both used to keep a separate `urls.txt` or `scraped_urls.txt` that Kaggle never had, so seeding from Kaggle would have downloaded the whole archive again.
- When it cannot be derived cheaply, keep it as a small file inside `data/`, as yu-gi-oh-decks does with `_last_deck_num.txt`.
- A missing cursor may mean a new dataset; an unreadable one must stop the run. Silently restarting from zero re-appends everything ever published.
- Only advance the cursor after the data it covers is on disk, so a crash cannot skip records.
- Only record an item as done once it has really been stored. mananeras used to record every downloaded URL, including pages it then failed to parse, so those transcripts were never retried.

## Same input, same bytes

`publish` compares file fingerprints with what `fetch` downloaded. Any byte that changes counts as an update: a Kaggle version and a Hugging Face commit, every run.

- Sort rows and keys in a fixed order and write columns in a fixed order.
- Do not stamp files with the run time unless something else changed. yu-gi-oh only rewrites `info.txt` when the cards did.
- Ignore volatile fields when deciding whether anything changed. Page views and votes move on every download, so yu-gi-oh compares its tables without them.
- Pin whatever affects the output format (CSV line endings with `newline=""`, JSON indentation, library versions in `requirements.txt`), so existing files are not rewritten just because a dependency changed.
- Check it: run the scraper twice in a row on a fetched copy. The second run should report no changes.

## Choose a layout the guards can protect

Each upload replaces the whole dataset, so the layout decides which mistakes `publish` can catch.

| Shape | Example | Guard |
| --- | --- | --- |
| Append-only shards that fill up and close | yu-gi-oh-decks: `deck_num // 10000` per CSV | `append-only: "*.csv"` refuses any rewrite of existing rows |
| One immutable file per record | mananeras: one `.txt` per transcript | `append-only: "*.txt"`; costs one Kaggle listing call per 200 files |
| Files updated in place | humble-scraping: a month's bundles change while on sale | Deletion guard only, no `append-only` |
| Full snapshot | yu-gi-oh: four CSVs replaced weekly | Deletion guard plus minimum-size checks in the scraper |

- Never delete or rename files as a side effect. `publish` refuses deletions unless `allow-deletions` is set, so a renamed file fails the run.
- Choose a file name that does not change when the record does. uk-blogs keeps no `append-only` because a renamed blog rewrites the post's file under the same path.
- Mind the file count. Kaggle lists 200 files per page and rate limits the listing; uk-blogs needs about 55 calls with 10,919 files. Fewer, larger files list faster and work better in the Hugging Face viewer.
- Mind the total size. `fetch` downloads the whole dataset on every run. When that stops being reasonable, do what uk-blogs does (fetch a recent window, commit additively to Hugging Face, mirror to Kaggle weekly) or use your own pipeline, as london-cycles does.

## Fail loudly on the source, quietly on a record

Two kinds of failure need opposite handling.

When the source itself is broken, stop the run before anything is written:

- Set a timeout on every request and check the status (`raise_for_status()` or equivalent). A request without a timeout can hang until the job's `timeout-minutes`.
- Retry transient errors (429, 5xx, timeouts) with backoff, then give up with a clear message.
- Validate the response shape before using it. A JSON error object, an HTML challenge page or an empty list must not be read as no data.
- Refuse to replace a full snapshot with a suspiciously small one. yu-gi-oh stops below 100 sets or 1,000 cards instead of publishing a broken API response.
- Write files atomically (write a temporary file, then `os.replace`), so a crash never leaves half a file behind.

When a single record is broken, skip it and carry on:

- Log it as a warning (`print(f"::warning::...")` shows up as an annotation in GitHub Actions) and do not record it in the cursor, so the next run retries it. One bad uk-blogs post used to fail every run until someone fixed it by hand.
- Never use a bare `except:`. Catch what you expect and let the rest fail the run.

## Crawl incrementally, and stop early

- Walk the listing newest first and stop at the first page with nothing new. uk-blogs used to break out of the inner loop only, and walked the whole index on every run.
- Deduplicate by id. Listings shift while you page through them, so the same record can appear on two consecutive pages, as yu-gi-oh-decks found.
- Refuse to crawl without a cursor when the data should have one. uk-blogs stops if the fetched window holds no posts, because it would otherwise crawl and download the whole index.
- Wait between pages and send a User-Agent that identifies the project.

## Timestamps

- Use timezone-aware UTC (`datetime.now(timezone.utc)`), not naive local time.
- If the source only gives relative times ("3 hours ago"), convert them with the query time and keep the schedule frequent enough for the precision you need. That is why yu-gi-oh-decks stays daily.

## Workflow

Each scraper is wrapped in the same shape of workflow; see the [readme](readme.md#usage) for a full example.

- `permissions: contents: read`. The workflow never pushes to git.
- A `workflow_dispatch` `source` input passed to `fetch`, so a new or broken destination can be filled from the other one.
- A `concurrency` group with `cancel-in-progress: false`, so two runs never publish over each other. Workflows that publish the same dataset share one group.
- A `timeout-minutes` that fits the slowest normal run.
- `healthcheck-url` on `publish` and a `ping` step with `if: failure()`.
- Install the scraper's own dependencies, pinned. The actions keep theirs in a private virtualenv, so any language or toolchain works.

## Test before the first scheduled run

Run it locally against real data:

```bash
python -m dataset_sync.fetch --from huggingface --huggingface-dataset owner/name --path data --state-dir .state
python scrape.py
python -m dataset_sync.publish --path data --state-dir .state --huggingface-dataset owner/name --dry-run
```

- Run it twice. The second run should change nothing.
- Delete the newest few records from `data/` and run again. They should come back byte for byte, and nothing else should change.
- Run it with `data/` empty and check that it either builds the dataset from scratch on purpose or refuses to.
