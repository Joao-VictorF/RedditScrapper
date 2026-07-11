from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", (text or "").strip())


def _is_deleted_or_removed(text: str) -> bool:
    lowered = text.lower()
    return lowered in {"[deleted]", "[removed]"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _token_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


def _build_chunk_text(
    title: str,
    selftext: str,
    comments: list[dict[str, Any]],
) -> str:
    title_clean = _normalize_text(title)
    selftext_clean = _normalize_text(selftext)

    sections: list[str] = []
    if title_clean:
        sections.append(f"Post title: {title_clean}")
    if selftext_clean:
        sections.append(f"Post body: {selftext_clean}")

    comment_lines: list[str] = []
    for comment in comments:
        author = _normalize_text(str(comment.get("author", ""))) or "unknown"
        body = _normalize_text(str(comment.get("body", "")))
        score = _to_int(comment.get("score"), 0)
        comment_lines.append(f"- ({author}, score={score}) {body}")

    if comment_lines:
        sections.append("Comments:\n" + "\n".join(comment_lines))

    return "\n\n".join(sections).strip()


def _clean_comments(
    comments: list[dict[str, Any]],
    min_comment_chars: int,
    min_comment_score: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    kept: list[dict[str, Any]] = []
    stats = {
        "input_comments": len(comments),
        "removed_empty": 0,
        "removed_deleted_removed": 0,
        "removed_too_short": 0,
        "removed_low_score": 0,
        "kept": 0,
    }

    seen_ids: set[str] = set()
    for comment in comments:
        cid = str(comment.get("id", "")).strip()
        if cid:
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

        body = _normalize_text(str(comment.get("body", "")))
        if not body:
            stats["removed_empty"] += 1
            continue

        if _is_deleted_or_removed(body):
            stats["removed_deleted_removed"] += 1
            continue

        if len(body) < min_comment_chars:
            stats["removed_too_short"] += 1
            continue

        score = _to_int(comment.get("score"), 0)
        if min_comment_score is not None and score < min_comment_score:
            stats["removed_low_score"] += 1
            continue

        cleaned_comment = {
            **comment,
            "body": body,
            "author": _normalize_text(str(comment.get("author", ""))),
            "score": score,
        }
        kept.append(cleaned_comment)

    kept.sort(key=lambda c: (_to_int(c.get("created_utc"), 0), str(c.get("id", ""))))
    stats["kept"] = len(kept)
    return kept, stats


def _chunk_comments_by_tokens(
    comments: list[dict[str, Any]],
    title: str,
    selftext: str,
    max_chunk_tokens: int,
    overlap_comments: int,
) -> list[dict[str, Any]]:
    if not comments:
        return []

    chunks: list[dict[str, Any]] = []
    start = 0

    while start < len(comments):
        current: list[dict[str, Any]] = []
        current_tokens = 0
        idx = start

        while idx < len(comments):
            candidate = comments[idx]
            line = f"- ({candidate.get('author','unknown')}, score={candidate.get('score',0)}) {candidate.get('body','')}"
            line_tokens = _token_count(line)

            # Always allow at least one comment in each chunk.
            if current and (current_tokens + line_tokens) > max_chunk_tokens:
                break

            current.append(candidate)
            current_tokens += line_tokens
            idx += 1

        text = _build_chunk_text(title=title, selftext=selftext, comments=current)
        comment_ids = [str(c.get("id", "")) for c in current if str(c.get("id", "")).strip()]
        chunks.append(
            {
                "text": text,
                "comment_ids": comment_ids,
                "comment_count": len(current),
                "token_estimate": _token_count(text),
            }
        )

        if idx >= len(comments):
            break

        next_start = max(start + 1, idx - max(overlap_comments, 0))
        if next_start <= start:
            next_start = start + 1
        start = next_start

    return chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build cleaned RAG chunks from merged Reddit corpus")
    parser.add_argument(
        "--input",
        default="results/imported/corpus_plantedtank_merged.jsonl",
        help="Input merged corpus JSONL path",
    )
    parser.add_argument(
        "--output",
        default="results/rag/chunks_plantedtank.jsonl",
        help="Output chunk JSONL path",
    )
    parser.add_argument(
        "--summary-file",
        default="results/rag/chunks_plantedtank_summary.json",
        help="Summary JSON path",
    )
    parser.add_argument(
        "--min-comment-chars",
        type=int,
        default=20,
        help="Minimum comment body length after normalization",
    )
    parser.add_argument(
        "--min-comment-score",
        type=int,
        default=None,
        help="Optional minimum comment score. If omitted, score is not used as a filter",
    )
    parser.add_argument(
        "--max-chunk-tokens",
        type=int,
        default=700,
        help="Approximate maximum tokens per chunk",
    )
    parser.add_argument(
        "--overlap-comments",
        type=int,
        default=2,
        help="Number of comments to overlap between consecutive chunks",
    )
    parser.add_argument(
        "--max-comments-per-post",
        type=int,
        default=300,
        help="Safety cap for comments per post after cleaning",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=0,
        help="Optional cap of processed posts (0 means all)",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=5000,
        help="Progress log interval for scanned posts",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    summary_path = Path(args.summary_file)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = int(time.time())

    scanned_posts = 0
    processed_posts = 0
    skipped_posts_no_comments = 0
    total_chunks = 0

    total_input_comments = 0
    total_kept_comments = 0
    total_removed_empty = 0
    total_removed_deleted_removed = 0
    total_removed_too_short = 0
    total_removed_low_score = 0

    with input_path.open("r", encoding="utf-8") as in_fh, output_path.open("w", encoding="utf-8") as out_fh:
        for line in in_fh:
            raw = line.strip()
            if not raw:
                continue

            scanned_posts += 1
            if args.print_every > 0 and scanned_posts % args.print_every == 0:
                print(f"[chunks] scanned_posts={scanned_posts} processed_posts={processed_posts} chunks={total_chunks}")

            if args.max_posts > 0 and processed_posts >= args.max_posts:
                break

            doc = json.loads(raw)
            comments = doc.get("comments", [])
            if not isinstance(comments, list) or not comments:
                skipped_posts_no_comments += 1
                continue

            cleaned_comments, cstats = _clean_comments(
                comments=comments,
                min_comment_chars=args.min_comment_chars,
                min_comment_score=args.min_comment_score,
            )

            total_input_comments += cstats["input_comments"]
            total_kept_comments += cstats["kept"]
            total_removed_empty += cstats["removed_empty"]
            total_removed_deleted_removed += cstats["removed_deleted_removed"]
            total_removed_too_short += cstats["removed_too_short"]
            total_removed_low_score += cstats["removed_low_score"]

            if not cleaned_comments:
                skipped_posts_no_comments += 1
                continue

            capped_comments = cleaned_comments[: max(args.max_comments_per_post, 1)]
            chunks = _chunk_comments_by_tokens(
                comments=capped_comments,
                title=str(doc.get("title", "")),
                selftext=str(doc.get("selftext", "")),
                max_chunk_tokens=max(args.max_chunk_tokens, 100),
                overlap_comments=max(args.overlap_comments, 0),
            )

            if not chunks:
                skipped_posts_no_comments += 1
                continue

            processed_posts += 1
            for idx, chunk in enumerate(chunks):
                chunk_doc = {
                    "chunk_id": f"{doc.get('id')}_c{idx:04d}",
                    "post_id": doc.get("id"),
                    "subreddit": doc.get("subreddit"),
                    "created_utc": doc.get("created_utc"),
                    "post_title": doc.get("title", ""),
                    "post_author": doc.get("author"),
                    "post_score": _to_int(doc.get("score"), 0),
                    "post_url": doc.get("url"),
                    "permalink": doc.get("permalink"),
                    "chunk_index": idx,
                    "chunk_total": len(chunks),
                    "comment_count": chunk["comment_count"],
                    "comment_ids": chunk["comment_ids"],
                    "token_estimate": chunk["token_estimate"],
                    "text": chunk["text"],
                    "source": doc.get("source", "unknown"),
                }
                out_fh.write(json.dumps(chunk_doc, ensure_ascii=False) + "\n")
                total_chunks += 1

    ended_at = int(time.time())
    summary = {
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": ended_at - started_at,
        "config": {
            "input": str(input_path),
            "output": str(output_path),
            "min_comment_chars": args.min_comment_chars,
            "min_comment_score": args.min_comment_score,
            "max_chunk_tokens": args.max_chunk_tokens,
            "overlap_comments": args.overlap_comments,
            "max_comments_per_post": args.max_comments_per_post,
            "max_posts": args.max_posts,
        },
        "stats": {
            "scanned_posts": scanned_posts,
            "processed_posts": processed_posts,
            "skipped_posts_no_comments": skipped_posts_no_comments,
            "chunks_written": total_chunks,
            "input_comments": total_input_comments,
            "kept_comments": total_kept_comments,
            "removed_empty": total_removed_empty,
            "removed_deleted_removed": total_removed_deleted_removed,
            "removed_too_short": total_removed_too_short,
            "removed_low_score": total_removed_low_score,
            "comment_keep_ratio": round((total_kept_comments / total_input_comments), 4)
            if total_input_comments > 0
            else 0.0,
        },
    }

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "[chunks] done "
        f"processed_posts={processed_posts} chunks_written={total_chunks} "
        f"comment_keep_ratio={summary['stats']['comment_keep_ratio']}"
    )
    print(f"[chunks] output={output_path}")
    print(f"[chunks] summary={summary_path}")


if __name__ == "__main__":
    main()
