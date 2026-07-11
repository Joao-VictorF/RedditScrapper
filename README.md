# Reddit Knowledge Ingestor

Repository for collecting Reddit posts/comments and preparing high-quality datasets for RAG pipelines.

Suggested repository rename:

- `reddit-knowledge-ingestor`
- Alternative: `reddit-rag-ingestor`

## What This Project Includes

Two retrieval modes are supported:

1. Live Reddit retrieval (`src/main.py`) via public Reddit JSON endpoints.
2. Historical dump import (`src/import_archive.py`, `src/import_archive_batch.py`) from external datasets (for example Arctic Shift exports).

This separation is intentional: live retrieval is great for recent windows and incremental updates, while dump import is best for deep history backfill.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Retrieval Mode 1: Live Reddit JSON (Current Script)

Run:

```bash
python3 src/main.py \
  --subreddit MachineLearning \
  --start-date 2026-01-01 \
  --end-date 2026-06-30 \
  --max-posts 0 \
  --run-label semester-1 \
  --requests-per-minute 10 \
  --structured-logs \
  --rag-min-comment-chars 20 \
  --rag-max-comments 200
```

You can also add direct links through `links.txt`.

### Cookie Authentication (Important)

In many environments, live Reddit requests only work reliably when an authenticated browser cookie is provided. In practice, this is often the difference between successful retrieval and `403 Blocked`.

Recommended pattern:

1. Copy the full `Cookie:` request header from browser DevTools (Network tab).
2. Export it as `REDDIT_COOKIE` in the same terminal session.
3. Run the script with `--reddit-cookie "$REDDIT_COOKIE"`.

Example:

```bash
export REDDIT_COOKIE='Cookie: loid=...; reddit_session=...; token_v2=...'
python3 src/main.py \
  --subreddit MachineLearning \
  --start-date 2026-01-01 \
  --end-date 2026-06-30 \
  --max-posts 100 \
  --reddit-cookie "$REDDIT_COOKIE" \
  --user-agent "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0"
```

Quick validation:

```bash
python3 -c "import os; v=os.getenv('REDDIT_COOKIE',''); print(bool(v), len(v))"
```

Expected output: `True` and a length greater than zero.

Notes:

- The importer accepts both raw cookie strings and strings prefixed with `Cookie:`.
- Keep cookies out of git and logs.
- Rotate/revoke your Reddit session after tests if credentials were exposed.

### Live Retrieval Limitations

This mode uses `r/<sub>/new.json` pagination. It is reliable for recent windows but has practical listing depth limits.

- In real tests, pagination often stops around ~1000 reachable posts for very active feeds.
- Older date windows may return zero candidates, even when older posts exist.
- Some environments/networks may receive `403 Blocked`.

Best use cases:

- Recent data collection
- Incremental daily/weekly updates
- Smaller/short-term RAG datasets (for example ~1000 posts/comments can already provide useful signal)

### Live Run Artifacts

Each execution writes to `results/runs`:

```text
results/runs/
  subreddit=<name>/
    period=<start>_to_<end>/
      run=<timestamp>__label=<label>/
        corpus.jsonl
        coverage_posts.jsonl
        pending_comments.jsonl
        checkpoint.json
        summary.json
        inputs/links.txt
```

## Retrieval Mode 2: Historical Dump Import (Arctic Shift / Similar)

Use this mode when you need older historical coverage beyond live listing depth.

Recommended sources/tools:

- Arctic Shift project: https://github.com/ArthurHeitmann/arctic_shift
- Arctic Shift download tool: https://arctic-shift.photon-reddit.com/download-tool
- Arctic Shift API docs: https://github.com/ArthurHeitmann/arctic_shift/tree/master/api

### Import a Single Range

```bash
python3 src/import_archive.py \
  --posts-file ../posts_2021-05-17-2022-05-17.jsonl \
  --comments-file ../comments_2021-05-17_2022-05-17.jsonl \
  --subreddit plantedtank \
  --output results/imported/corpus_plantedtank_2021_2022.jsonl \
  --summary-file results/imported/summary_plantedtank_2021_2022.json \
  --tmp-db .tmp/import_plantedtank_2021_2022.sqlite3
```

### Import All Downloaded Ranges (Batch)

```bash
python3 src/import_archive_batch.py \
  --input-dir .. \
  --subreddit plantedtank \
  --output-dir results/imported \
  --tmp-db-dir .tmp \
  --skip-existing
```

Dry-run preview:

```bash
python3 src/import_archive_batch.py --input-dir .. --subreddit plantedtank --dry-run
```

### Historical Import Notes

- This flow is additive and does not modify `src/main.py`.
- Comments are joined to posts by `link_id -> post_id`.
- Coverage can be above 1.0 in historical imports because post/comment snapshots were not always captured at exactly the same time.

## Next Dataset Step: Merge Imported Yearly Corpora

Script (already created): `src/merge_imported_corpus.py`

Purpose:

- Merge multiple yearly imported corpora into one deduplicated corpus.
- Deduplicate posts by post ID.
- Merge and deduplicate comments for overlapping posts.
- Recompute `stats` and `rag` fields.

## RAG Guidance

See `docs/RAG_PIPELINE.md` for a full implementation checklist (cleaning, chunking, embedding, indexing, evaluation).

## Testing

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

