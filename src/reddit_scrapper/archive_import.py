from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

from .io_utils import write_json

DEFAULT_SOURCE_LABEL = "arctic_shift_dump"
DEFAULT_OUTPUT = "results/imported/corpus_from_dump.jsonl"
DEFAULT_SUMMARY = "results/imported/import_summary.json"
BASE_URL = "https://www.reddit.com"


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            yield json.loads(raw)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_post_url(permalink: str | None, raw_url: str | None) -> str:
    if raw_url:
        return raw_url
    if permalink:
        return f"{BASE_URL}{permalink}"
    return ""


def _extract_post_id(link_id: Any) -> str:
    if not isinstance(link_id, str):
        return ""
    value = link_id.strip()
    if value.startswith("t3_"):
        return value[3:]
    return value


def _normalize_comment(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": comment.get("id"),
        "parent_id": comment.get("parent_id"),
        "author": comment.get("author"),
        "body": comment.get("body", ""),
        "score": _to_int(comment.get("score"), 0),
        "created_utc": comment.get("created_utc"),
        "depth": 0,
        "is_submitter": bool(comment.get("is_submitter", False)),
        "controversiality": _to_int(comment.get("controversiality"), 0),
    }


def _is_useful_comment_text(text: str, min_chars: int) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in {"[deleted]", "[removed]"}:
        return False
    return len(cleaned) >= min_chars


def _build_rag_payload(
    post_title: str,
    post_selftext: str,
    comments: list[dict[str, Any]],
    min_comment_chars: int,
    max_comments: int,
) -> dict[str, Any]:
    title = (post_title or "").strip()
    selftext = (post_selftext or "").strip()
    post_text = title
    if selftext:
        post_text = f"{title}\n\n{selftext}".strip()

    comment_texts: list[str] = []
    filtered = 0
    for comment in comments:
        body = str(comment.get("body", ""))
        if not _is_useful_comment_text(body, min_comment_chars):
            filtered += 1
            continue
        comment_texts.append(body.strip())
        if len(comment_texts) >= max_comments:
            break

    parts = [post_text] if post_text else []
    if comment_texts:
        parts.append("\n\n".join(comment_texts))

    return {
        "post_text": post_text,
        "comment_texts": comment_texts,
        "combined_text": "\n\n".join(parts),
        "stats": {
            "comments_kept": len(comment_texts),
            "comments_filtered": filtered,
        },
    }


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("CREATE TABLE IF NOT EXISTS comments (post_id TEXT NOT NULL, payload TEXT NOT NULL)")
    conn.execute("DELETE FROM comments")
    conn.commit()
    return conn


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import yearly Reddit dump JSONL files into corpus format")
    parser.add_argument("--posts-file", required=True, help="Path to posts JSONL file")
    parser.add_argument("--comments-file", required=True, help="Path to comments JSONL file")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output corpus JSONL path")
    parser.add_argument("--summary-file", default=DEFAULT_SUMMARY, help="Import summary JSON path")
    parser.add_argument("--subreddit", default="", help="Optional subreddit filter")
    parser.add_argument("--source-label", default=DEFAULT_SOURCE_LABEL, help="Source label added to each output record")
    parser.add_argument("--tmp-db", default=".tmp/archive_import.sqlite3", help="Temporary SQLite database path")
    parser.add_argument("--rag-min-comment-chars", type=int, default=20, help="Min chars to keep comment in rag payload")
    parser.add_argument("--rag-max-comments", type=int, default=200, help="Max comments in rag payload")
    parser.add_argument("--print-every", type=int, default=50000, help="Progress log interval for file reads")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    posts_file = Path(args.posts_file)
    comments_file = Path(args.comments_file)
    output_file = Path(args.output)
    summary_file = Path(args.summary_file)
    tmp_db = Path(args.tmp_db)

    subreddit_filter = args.subreddit.strip().lower()
    started_at = int(time.time())

    posts_by_id: dict[str, dict[str, Any]] = {}
    post_order: list[str] = []

    scanned_posts = 0
    skipped_posts = 0
    print(f"[import] reading posts from {posts_file}")
    for row in _iter_jsonl(posts_file):
        scanned_posts += 1
        if args.print_every > 0 and scanned_posts % args.print_every == 0:
            print(f"[import] posts scanned={scanned_posts} selected={len(posts_by_id)}")

        subreddit = str(row.get("subreddit", "")).lower()
        if subreddit_filter and subreddit != subreddit_filter:
            skipped_posts += 1
            continue

        post_id = str(row.get("id", "")).strip()
        if not post_id:
            skipped_posts += 1
            continue

        permalink = row.get("permalink")
        post_doc = {
            "source": args.source_label,
            "fetched_at": int(time.time()),
            "id": post_id,
            "name": row.get("name") or f"t3_{post_id}",
            "subreddit": row.get("subreddit"),
            "subreddit_id": row.get("subreddit_id"),
            "title": row.get("title", ""),
            "selftext": row.get("selftext", ""),
            "author": row.get("author"),
            "score": _to_int(row.get("score"), 0),
            "upvote_ratio": row.get("upvote_ratio"),
            "num_comments": _to_int(row.get("num_comments"), 0),
            "created_utc": row.get("created_utc"),
            "over_18": bool(row.get("over_18", False)),
            "spoiler": bool(row.get("spoiler", False)),
            "locked": bool(row.get("locked", False)),
            "stickied": bool(row.get("stickied", False)),
            "link_flair_text": row.get("link_flair_text"),
            "url": _normalize_post_url(permalink, row.get("url")),
            "permalink": permalink,
            "domain": row.get("domain"),
            "is_self": bool(row.get("is_self", False)),
        }

        if post_id not in posts_by_id:
            post_order.append(post_id)
        posts_by_id[post_id] = post_doc

    if not posts_by_id:
        print("[import] no posts selected after filters")
        summary = {
            "started_at": started_at,
            "ended_at": int(time.time()),
            "duration_seconds": int(time.time()) - started_at,
            "input": {
                "posts_file": str(posts_file),
                "comments_file": str(comments_file),
                "subreddit": args.subreddit or None,
            },
            "stats": {
                "posts_scanned": scanned_posts,
                "posts_selected": 0,
                "posts_skipped": skipped_posts,
                "comments_scanned": 0,
                "comments_selected": 0,
                "documents_written": 0,
            },
            "output": {
                "corpus_jsonl": str(output_file),
            },
        }
        write_json(summary_file, summary)
        print(f"[import] summary written to {summary_file}")
        return

    print(f"[import] selected posts={len(posts_by_id)} | scanning comments from {comments_file}")
    conn = _open_db(tmp_db)
    cursor = conn.cursor()

    scanned_comments = 0
    selected_comments = 0
    insert_batch: list[tuple[str, str]] = []

    for row in _iter_jsonl(comments_file):
        scanned_comments += 1
        if args.print_every > 0 and scanned_comments % args.print_every == 0:
            print(f"[import] comments scanned={scanned_comments} selected={selected_comments}")

        subreddit = str(row.get("subreddit", "")).lower()
        if subreddit_filter and subreddit != subreddit_filter:
            continue

        post_id = _extract_post_id(row.get("link_id"))
        if not post_id or post_id not in posts_by_id:
            continue

        normalized_comment = _normalize_comment(row)
        insert_batch.append((post_id, json.dumps(normalized_comment, ensure_ascii=False)))
        selected_comments += 1

        if len(insert_batch) >= 5000:
            cursor.executemany("INSERT INTO comments(post_id, payload) VALUES (?, ?)", insert_batch)
            conn.commit()
            insert_batch = []

    if insert_batch:
        cursor.executemany("INSERT INTO comments(post_id, payload) VALUES (?, ?)", insert_batch)
        conn.commit()

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id)")
    conn.commit()

    output_file.parent.mkdir(parents=True, exist_ok=True)
    documents_written = 0
    total_expected_comments = 0
    total_extracted_comments = 0

    print(f"[import] building output corpus at {output_file}")
    with output_file.open("w", encoding="utf-8") as out:
        for post_id in post_order:
            post_doc = posts_by_id[post_id]
            rows = conn.execute("SELECT payload FROM comments WHERE post_id = ?", (post_id,)).fetchall()
            comments = [json.loads(row[0]) for row in rows]

            expected_comments = _to_int(post_doc.get("num_comments"), 0)
            extracted_comments = len(comments)
            coverage_ratio = (extracted_comments / expected_comments) if expected_comments > 0 else 1.0

            rag_payload = _build_rag_payload(
                post_title=str(post_doc.get("title", "")),
                post_selftext=str(post_doc.get("selftext", "")),
                comments=comments,
                min_comment_chars=args.rag_min_comment_chars,
                max_comments=args.rag_max_comments,
            )
            rag_stats = rag_payload["stats"]

            doc = {
                **post_doc,
                "stats": {
                    "expected_comments": expected_comments,
                    "extracted_comments": extracted_comments,
                    "extracted_comments_before_expansion": extracted_comments,
                    "extracted_comments_after_expansion": extracted_comments,
                    "more_placeholders": 0,
                    "pending_comment_ids_before_expansion": 0,
                    "pending_comment_ids_resolved": 0,
                    "pending_comment_ids_after_expansion": 0,
                    "pending_resolution_rate": 1.0,
                    "coverage_ratio": round(coverage_ratio, 4),
                    "rag_comments_kept": rag_stats["comments_kept"],
                    "rag_comments_filtered": rag_stats["comments_filtered"],
                },
                "pending_comment_ids": [],
                "rag": {
                    "post_text": rag_payload["post_text"],
                    "comment_texts": rag_payload["comment_texts"],
                    "combined_text": rag_payload["combined_text"],
                },
                "comments": comments,
            }

            out.write(json.dumps(doc, ensure_ascii=False) + "\n")
            documents_written += 1
            total_expected_comments += expected_comments
            total_extracted_comments += extracted_comments

    conn.close()

    ended_at = int(time.time())
    summary = {
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": ended_at - started_at,
        "input": {
            "posts_file": str(posts_file),
            "comments_file": str(comments_file),
            "subreddit": args.subreddit or None,
            "source_label": args.source_label,
        },
        "stats": {
            "posts_scanned": scanned_posts,
            "posts_selected": len(posts_by_id),
            "posts_skipped": skipped_posts,
            "comments_scanned": scanned_comments,
            "comments_selected": selected_comments,
            "documents_written": documents_written,
            "expected_comments_total": total_expected_comments,
            "extracted_comments_total": total_extracted_comments,
            "coverage_ratio": round(
                (total_extracted_comments / total_expected_comments) if total_expected_comments > 0 else 1.0,
                4,
            ),
        },
        "output": {
            "corpus_jsonl": str(output_file),
            "summary_json": str(summary_file),
            "tmp_db": str(tmp_db),
        },
    }

    write_json(summary_file, summary)
    print(
        "[import] done "
        f"posts={len(posts_by_id)} comments={selected_comments} "
        f"coverage={summary['stats']['coverage_ratio']}"
    )
    print(f"[import] summary written to {summary_file}")


if __name__ == "__main__":
    main()
