from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path
from typing import Any


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _comment_key(comment: dict[str, Any]) -> str:
    comment_id = str(comment.get("id", "")).strip()
    if comment_id:
        return f"id:{comment_id}"

    parent = str(comment.get("parent_id", ""))
    author = str(comment.get("author", ""))
    created = str(comment.get("created_utc", ""))
    body = str(comment.get("body", ""))
    digest = hashlib.md5(body.encode("utf-8")).hexdigest()[:12]
    return f"anon:{parent}:{author}:{created}:{digest}"


def _is_useful_comment_text(text: str, min_chars: int) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False

    lowered = cleaned.lower()
    if lowered in {"[deleted]", "[removed]"}:
        return False

    return len(cleaned) >= min_chars


def _build_rag_payload(
    title: str,
    selftext: str,
    comments: list[dict[str, Any]],
    min_comment_chars: int,
    max_comments: int,
) -> dict[str, Any]:
    post_title = (title or "").strip()
    post_selftext = (selftext or "").strip()

    post_text = post_title
    if post_selftext:
        post_text = f"{post_title}\n\n{post_selftext}".strip()

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

    chunks: list[str] = []
    if post_text:
        chunks.append(post_text)
    if comment_texts:
        chunks.append("\n\n".join(comment_texts))

    return {
        "post_text": post_text,
        "comment_texts": comment_texts,
        "combined_text": "\n\n".join(chunks),
        "stats": {
            "comments_kept": len(comment_texts),
            "comments_filtered": filtered,
        },
    }


def _merge_docs(
    base: dict[str, Any],
    incoming: dict[str, Any],
    rag_min_comment_chars: int,
    rag_max_comments: int,
) -> dict[str, Any]:
    merged = dict(base)

    comments_map: dict[str, dict[str, Any]] = {}
    for comment in base.get("comments", []):
        if isinstance(comment, dict):
            comments_map[_comment_key(comment)] = comment

    for comment in incoming.get("comments", []):
        if isinstance(comment, dict):
            comments_map[_comment_key(comment)] = comment

    merged_comments = list(comments_map.values())
    merged_comments.sort(key=lambda x: (_to_int(x.get("created_utc"), 0), str(x.get("id", ""))))

    base_expected = _to_int(base.get("num_comments"), 0)
    incoming_expected = _to_int(incoming.get("num_comments"), 0)
    stats_base_expected = _to_int(base.get("stats", {}).get("expected_comments"), 0)
    stats_incoming_expected = _to_int(incoming.get("stats", {}).get("expected_comments"), 0)

    expected_comments = max(base_expected, incoming_expected, stats_base_expected, stats_incoming_expected)
    extracted_comments = len(merged_comments)
    coverage_ratio = (extracted_comments / expected_comments) if expected_comments > 0 else 1.0

    rag_payload = _build_rag_payload(
        title=str(merged.get("title", "")),
        selftext=str(merged.get("selftext", "")),
        comments=merged_comments,
        min_comment_chars=rag_min_comment_chars,
        max_comments=rag_max_comments,
    )
    rag_stats = rag_payload["stats"]

    merged["score"] = max(_to_int(base.get("score"), 0), _to_int(incoming.get("score"), 0))
    merged["num_comments"] = expected_comments
    merged["comments"] = merged_comments
    merged["pending_comment_ids"] = []
    merged["rag"] = {
        "post_text": rag_payload["post_text"],
        "comment_texts": rag_payload["comment_texts"],
        "combined_text": rag_payload["combined_text"],
    }
    merged["stats"] = {
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
    }

    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge imported yearly corpus files into one deduplicated JSONL corpus"
    )
    parser.add_argument(
        "--input-glob",
        default="results/imported/corpus_*.jsonl",
        help="Glob pattern for input yearly corpus files",
    )
    parser.add_argument(
        "--output",
        default="results/imported/corpus_merged.jsonl",
        help="Output merged JSONL file",
    )
    parser.add_argument(
        "--summary-file",
        default="results/imported/merge_summary.json",
        help="Output summary JSON file",
    )
    parser.add_argument(
        "--rag-min-comment-chars",
        type=int,
        default=20,
        help="Minimum comment length for rag.comment_texts",
    )
    parser.add_argument(
        "--rag-max-comments",
        type=int,
        default=200,
        help="Maximum comments in rag.comment_texts per post",
    )
    parser.add_argument(
        "--sort-by-created-utc",
        action="store_true",
        help="Sort merged output by post created_utc ascending",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_paths = [Path(p) for p in sorted(glob.glob(args.input_glob))]
    if not input_paths:
        raise SystemExit(f"No files matched input glob: {args.input_glob}")

    print(f"[merge] files matched={len(input_paths)}")

    merged_by_post_id: dict[str, dict[str, Any]] = {}
    scanned_documents = 0
    duplicate_posts = 0

    for path in input_paths:
        print(f"[merge] reading {path}")
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue

                scanned_documents += 1
                doc = json.loads(raw)
                post_id = str(doc.get("id", "")).strip()
                if not post_id:
                    continue

                existing = merged_by_post_id.get(post_id)
                if existing is None:
                    merged_by_post_id[post_id] = doc
                    continue

                duplicate_posts += 1
                merged_by_post_id[post_id] = _merge_docs(
                    existing,
                    doc,
                    rag_min_comment_chars=args.rag_min_comment_chars,
                    rag_max_comments=args.rag_max_comments,
                )

    output_docs = list(merged_by_post_id.values())
    if args.sort_by_created_utc:
        output_docs.sort(key=lambda d: (_to_int(d.get("created_utc"), 0), str(d.get("id", ""))))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for doc in output_docs:
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")

    total_expected = 0
    total_extracted = 0
    for doc in output_docs:
        stats = doc.get("stats", {})
        total_expected += _to_int(stats.get("expected_comments"), _to_int(doc.get("num_comments"), 0))
        total_extracted += _to_int(stats.get("extracted_comments"), len(doc.get("comments", [])))

    coverage_ratio = (total_extracted / total_expected) if total_expected > 0 else 1.0
    summary = {
        "input_glob": args.input_glob,
        "input_files": [str(p) for p in input_paths],
        "stats": {
            "scanned_documents": scanned_documents,
            "unique_posts": len(output_docs),
            "duplicate_post_documents": duplicate_posts,
            "expected_comments_total": total_expected,
            "extracted_comments_total": total_extracted,
            "coverage_ratio": round(coverage_ratio, 4),
        },
        "output": {
            "corpus_jsonl": str(output_path),
            "summary_json": str(args.summary_file),
        },
    }

    summary_path = Path(args.summary_file)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "[merge] done "
        f"unique_posts={len(output_docs)} duplicate_docs={duplicate_posts} "
        f"coverage={summary['stats']['coverage_ratio']}"
    )
    print(f"[merge] output={output_path}")
    print(f"[merge] summary={summary_path}")


if __name__ == "__main__":
    main()
