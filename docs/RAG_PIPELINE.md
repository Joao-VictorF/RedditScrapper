# RAG Pipeline Guide

This guide defines a practical, production-friendly RAG pipeline for Reddit data.

## Goal

Build a robust retrieval stack where comments remain first-class content, because most domain value is in discussion threads, answers, and reasoning.

## Data Layers

Use two data layers:

1. Raw Layer (audit/source-of-truth)
- Keep merged posts and full comments.
- Preserve as much original structure as possible.
- Use this layer for reprocessing and quality audits.

2. RAG Layer (retrieval-optimized)
- Clean and normalize text.
- Keep metadata required for filtering/ranking.
- Create chunks and embeddings optimized for retrieval latency and quality.

## End-to-End Steps

## 1) Retrieve Data

You have two retrieval strategies:

1. Live Reddit JSON retrieval (`src/main.py`)
- Best for recent windows and incremental collection.
- Limitation: listing depth from `new.json` is finite.

2. Historical dump retrieval (Arctic Shift / similar)
- Best for older historical windows.
- Import with `src/import_archive.py` or `src/import_archive_batch.py`.

## 2) Merge and Deduplicate

Use `src/merge_imported_corpus.py` to create a unified corpus:

- Deduplicate by post ID.
- Merge duplicate post records from overlapping ranges.
- Merge/deduplicate comment records.
- Recompute stats and RAG helper fields.

## 3) Cleaning and Normalization

Before embedding, run a treatment pass:

- Remove empty comments and known placeholders (`[deleted]`, `[removed]`).
- Normalize whitespace and line breaks.
- Optionally remove low-signal boilerplate.
- Keep permalink and IDs for traceability.

Recommended defaults:

- Min comment length: 20 to 40 characters.
- Keep all comments in raw layer; filter only in RAG layer.

## 4) Chunking Strategy

Recommended baseline:

- Chunk unit: comment-centric chunks with post context.
- Add lightweight post context to each chunk:
  - post title
  - short post body (or excerpt)
- Target chunk size: 400 to 800 tokens.
- Overlap: 10% to 20% (or ~50 to 120 tokens).

If threads are critical:

- Use parent-child aware chunking for short reply chains.
- Avoid giant monolithic chunks per post.

## 5) Embeddings

Generate embeddings from cleaned chunks.

Store with metadata:

- post_id
- comment_id(s)
- subreddit
- created_utc
- score
- permalink
- source range/file

This enables hybrid ranking and strict filters later.

## 6) Vector Store

Yes, use a vector database.

Examples:

- pgvector
- Qdrant
- Weaviate
- Pinecone
- Milvus

Best practice:

- Prefer hybrid retrieval (vector + lexical/BM25) over vector-only.
- Keep metadata filters enabled.

## 7) Retrieval and Ranking

Recommended serving flow:

1. Retrieve top-K (for example 30 to 80).
2. Apply metadata filters (date, subreddit, etc.).
3. Rerank to final top-N (for example 8 to 15).
4. Build grounded prompt context from top-N chunks.

## 8) Evaluation

Create a small benchmark set of real user questions and expected evidence.

Track:

- Hit@K
- MRR/NDCG
- Answer grounding quality
- Hallucination rate

Iterate on:

- filters
- chunk sizing/overlap
- reranker settings
- K/N values

## Practical Operating Modes

## Mode A: Quick RAG

- Use recent live retrieval only.
- Around ~1000 posts/comments can already provide useful content for many focused use cases.

## Mode B: Full Historical RAG

- Use historical dump import + merge + full indexing.
- Higher quality, broader recall, slower ingest.

## Suggested Workflow for This Repository

1. Collect recent data via `src/main.py`.
2. Backfill history via Arctic Shift exports.
3. Import yearly files via `src/import_archive_batch.py`.
4. Merge via `src/merge_imported_corpus.py`.
5. Build RAG-ready chunks/embeddings.
6. Index and evaluate before production rollout.
