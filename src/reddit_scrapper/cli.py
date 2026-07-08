from __future__ import annotations

import argparse
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .io_utils import append_jsonl, copy_if_exists, read_links_file, write_json
from .reddit_api import (
    build_session,
    end_date_to_exclusive_epoch,
    fetch_post_document,
    iter_subreddit_permalinks_by_date,
    normalize_post_url,
)
from .state import load_checkpoint, new_state_template, save_checkpoint
from .summary import build_run_summary

DEFAULT_USER_AGENT = "script:reddit-rag-scraper:v1.0 (by u/your_username)"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date_utc(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def to_epoch_seconds(dt: datetime) -> int:
    return int(dt.timestamp())


def slugify(value: str | None, default: str) -> str:
    if not value:
        return default

    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-._")
    return cleaned or default


def resolve_run_paths(args: argparse.Namespace, run_id: str) -> dict[str, Path]:
    subreddit_slug = slugify(args.subreddit, "links-only")

    if args.subreddit and args.start_date and args.end_date:
        period_slug = f"{args.start_date}_to_{args.end_date}"
    else:
        period_slug = "manual-links"

    results_root = Path(args.results_root)
    run_dir = results_root / f"subreddit={subreddit_slug}" / f"period={period_slug}" / f"run={run_id}"

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = run_dir / output_path

    pending_comments_path = Path(args.pending_comments_file)
    if not pending_comments_path.is_absolute():
        pending_comments_path = run_dir / pending_comments_path

    checkpoint_path = Path(args.checkpoint_file)
    if not checkpoint_path.is_absolute():
        checkpoint_path = run_dir / checkpoint_path

    summary_path = run_dir / "summary.json"
    inputs_dir = run_dir / "inputs"

    return {
        "run_dir": run_dir,
        "inputs_dir": inputs_dir,
        "output_path": output_path,
        "pending_comments_path": pending_comments_path,
        "checkpoint_path": checkpoint_path,
        "summary_path": summary_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reddit public JSON scraper for RAG corpus generation")
    parser.add_argument("--subreddit", help="Subreddit name without r/")
    parser.add_argument(
        "--start-date",
        help="Start date inclusive (YYYY-MM-DD). Required when using --subreddit.",
    )
    parser.add_argument(
        "--end-date",
        default=now_utc().strftime("%Y-%m-%d"),
        help="End date inclusive (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=0,
        help="Safety cap for subreddit posts in the date window (0 = no cap)",
    )
    parser.add_argument(
        "--links-file",
        default=os.getenv("REDDIT_LINKS_FILE", "links.txt"),
        help="File with one post URL per line",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("REDDIT_OUTPUT", "corpus.jsonl"),
        help="Output JSONL path",
    )
    parser.add_argument(
        "--pending-comments-file",
        default=os.getenv("REDDIT_PENDING_COMMENTS_FILE", "pending_comments.jsonl"),
        help="JSONL file with pending comment ids discovered from more blocks",
    )
    parser.add_argument(
        "--results-root",
        default=os.getenv("REDDIT_RESULTS_ROOT", "results/runs"),
        help="Root directory for organized run artifacts",
    )
    parser.add_argument(
        "--summary-dir",
        default=os.getenv("REDDIT_SUMMARY_DIR", "run_summaries"),
        help="Deprecated: kept for backward compatibility; summary is saved inside run directory",
    )
    parser.add_argument(
        "--requests-per-minute",
        type=float,
        default=float(os.getenv("REDDIT_REQUESTS_PER_MINUTE", "10")),
        help="Global request rate",
    )
    parser.add_argument(
        "--comment-sort",
        default="top",
        choices=["confidence", "top", "new", "controversial", "old", "qa"],
        help="Comment sort",
    )
    parser.add_argument("--comment-limit", type=int, default=500, help="Comment listing limit")
    parser.add_argument("--comment-depth", type=int, default=10, help="Comment tree depth")
    parser.add_argument(
        "--user-agent",
        default=os.getenv("REDDIT_USER_AGENT", DEFAULT_USER_AGENT),
        help="Reddit User-Agent",
    )
    parser.add_argument(
        "--checkpoint-file",
        default=os.getenv("REDDIT_CHECKPOINT_FILE", "checkpoint.json"),
        help="Checkpoint path used to resume progress",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore checkpoint progress and process links from scratch",
    )
    return parser.parse_args()


def validate_date_args(args: argparse.Namespace) -> tuple[datetime | None, datetime | None]:
    if not args.subreddit:
        return None, None

    if not args.start_date:
        raise SystemExit("--start-date is required when using --subreddit")

    start_date = parse_date_utc(args.start_date)
    end_date = parse_date_utc(args.end_date)
    if end_date < start_date:
        raise SystemExit("--end-date must be >= --start-date")

    return start_date, end_date


def main() -> None:
    args = parse_args()
    start_date, end_date = validate_date_args(args)

    min_delay_sec = 60.0 / max(args.requests_per_minute, 1.0)
    session = build_session(args.user_agent)

    links_path = Path(args.links_file)

    run_started_at = int(time.time())
    run_id = datetime.fromtimestamp(run_started_at, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_paths = resolve_run_paths(args, run_id)
    run_dir = run_paths["run_dir"]
    inputs_dir = run_paths["inputs_dir"]
    output_path = run_paths["output_path"]
    pending_comments_path = run_paths["pending_comments_path"]
    checkpoint_path = run_paths["checkpoint_path"]
    summary_path = run_paths["summary_path"]

    run_dir.mkdir(parents=True, exist_ok=True)

    links_snapshot_exists = copy_if_exists(links_path, inputs_dir / "links.txt")

    if args.no_resume:
        state = new_state_template()
    else:
        state = load_checkpoint(checkpoint_path)

    explicit_links = read_links_file(links_path)
    subreddit_links: list[str] = []
    if args.subreddit and start_date and end_date:
        start_ts = to_epoch_seconds(start_date)
        end_exclusive_ts = end_date_to_exclusive_epoch(end_date)
        subreddit_links = iter_subreddit_permalinks_by_date(
            session=session,
            subreddit=args.subreddit,
            start_ts=start_ts,
            end_exclusive_ts=end_exclusive_ts,
            max_posts=args.max_posts,
            min_delay_sec=min_delay_sec,
        )

    all_links = explicit_links + subreddit_links
    seen: set[str] = set()
    kept_links: list[str] = []
    for link in all_links:
        normalized = normalize_post_url(link)
        if normalized and normalized not in seen:
            seen.add(normalized)
            kept_links.append(normalized)

    run_stats = {
        "candidate_links": len(kept_links),
        "skipped_already_processed": 0,
        "saved": 0,
        "failed": 0,
        "expected_comments": 0,
        "extracted_comments": 0,
        "more_placeholders": 0,
        "pending_comment_ids": 0,
        "posts_with_pending_comments": 0,
    }

    if not kept_links:
        print("No links to process. Provide --subreddit+dates and/or a non-empty links file.")
        return

    processed_set = set(state.get("processed_links", []))
    pending_links = [link for link in kept_links if link not in processed_set]
    run_stats["skipped_already_processed"] = len(kept_links) - len(pending_links)

    if not pending_links:
        print("No pending links. Everything is already processed in checkpoint.")
        run_ended_at = int(time.time())
        summary = build_run_summary(run_id, run_started_at, run_ended_at, args, run_stats)
        summary["run_artifacts"] = {
            "run_dir": str(run_dir),
            "output": str(output_path),
            "pending_comments": str(pending_comments_path),
            "checkpoint": str(checkpoint_path),
            "links_snapshot": str(inputs_dir / "links.txt") if links_snapshot_exists else None,
        }
        write_json(summary_path, summary)
        print(f"Run summary: {summary_path}")
        return

    for link in pending_links:
        try:
            doc = fetch_post_document(
                session=session,
                post_url=link,
                min_delay_sec=min_delay_sec,
                comment_sort=args.comment_sort,
                comment_limit=args.comment_limit,
                comment_depth=args.comment_depth,
            )
            append_jsonl(output_path, doc)

            post_stats = doc.get("stats", {})
            pending_ids = doc.get("pending_comment_ids", [])

            state["saved"] = int(state.get("saved", 0)) + 1
            state["expected_comments"] = int(state.get("expected_comments", 0)) + int(post_stats.get("expected_comments", 0) or 0)
            state["extracted_comments"] = int(state.get("extracted_comments", 0)) + int(post_stats.get("extracted_comments", 0) or 0)
            state["more_placeholders"] = int(state.get("more_placeholders", 0)) + int(post_stats.get("more_placeholders", 0) or 0)
            state["pending_comment_ids"] = int(state.get("pending_comment_ids", 0)) + int(post_stats.get("pending_comment_ids", 0) or 0)

            run_stats["saved"] += 1
            run_stats["expected_comments"] += int(post_stats.get("expected_comments", 0) or 0)
            run_stats["extracted_comments"] += int(post_stats.get("extracted_comments", 0) or 0)
            run_stats["more_placeholders"] += int(post_stats.get("more_placeholders", 0) or 0)
            run_stats["pending_comment_ids"] += int(post_stats.get("pending_comment_ids", 0) or 0)
            if pending_ids:
                run_stats["posts_with_pending_comments"] += 1
                pending_payload = {
                    "run_id": run_id,
                    "collected_at": int(time.time()),
                    "post_id": doc.get("id"),
                    "post_name": doc.get("name"),
                    "subreddit": doc.get("subreddit"),
                    "permalink": doc.get("permalink"),
                    "url": doc.get("url"),
                    "pending_comment_ids": pending_ids,
                    "pending_comment_ids_count": len(pending_ids),
                }
                append_jsonl(pending_comments_path, pending_payload)
        except Exception as exc:
            state["failed"] = int(state.get("failed", 0)) + 1
            run_stats["failed"] += 1
            print(f"[WARN] Failed to fetch post: {link} | {exc}")

        processed_set.add(link)
        state["processed_links"] = sorted(processed_set)
        save_checkpoint(checkpoint_path, state)

    run_ended_at = int(time.time())
    summary = build_run_summary(run_id, run_started_at, run_ended_at, args, run_stats)
    summary["run_artifacts"] = {
        "run_dir": str(run_dir),
        "output": str(output_path),
        "pending_comments": str(pending_comments_path),
        "checkpoint": str(checkpoint_path),
        "links_snapshot": str(inputs_dir / "links.txt") if links_snapshot_exists else None,
    }
    write_json(summary_path, summary)

    print(
        "Summary "
        f"Saved={run_stats['saved']} "
        f"Failed={run_stats['failed']} "
        f"ExpectedComments={run_stats['expected_comments']} "
        f"ExtractedComments={run_stats['extracted_comments']} "
        f"PendingCommentIds={run_stats['pending_comment_ids']}"
    )
    if run_stats["saved"] > 0:
        avg_extracted = run_stats["extracted_comments"] / run_stats["saved"]
        avg_pending = run_stats["pending_comment_ids"] / run_stats["saved"]
        print(f"Averages ExtractedPerPost={avg_extracted:.2f} PendingIdsPerPost={avg_pending:.2f}")
    print(f"Output={output_path}")
    print(f"PendingQueue={pending_comments_path}")
    print(f"Checkpoint={checkpoint_path}")
    print(f"RunSummary={summary_path}")
    print(f"RunDir={run_dir}")
