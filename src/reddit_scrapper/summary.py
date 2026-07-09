from __future__ import annotations

from typing import Any


def build_run_summary(
    run_id: str,
    run_started_at: int,
    run_ended_at: int,
    args: Any,
    run_stats: dict[str, int],
) -> dict[str, Any]:
    processed_success = run_stats["saved"]
    expected_comments = run_stats["expected_comments"]
    extracted_comments = run_stats["extracted_comments"]
    coverage_ratio = (extracted_comments / expected_comments) if expected_comments > 0 else 1.0

    extracted_before = run_stats.get("extracted_comments_before_expansion", extracted_comments)
    extracted_after = run_stats.get("extracted_comments_after_expansion", extracted_comments)
    pending_before = run_stats.get("pending_comment_ids_before_expansion", run_stats["pending_comment_ids"])
    pending_resolved = run_stats.get("pending_comment_ids_resolved", 0)
    pending_after = run_stats.get("pending_comment_ids_after_expansion", run_stats["pending_comment_ids"])
    pending_resolution_rate = (pending_resolved / pending_before) if pending_before > 0 else 1.0

    avg_extracted = (extracted_comments / processed_success) if processed_success > 0 else 0.0
    avg_pending = (run_stats["pending_comment_ids"] / processed_success) if processed_success > 0 else 0.0

    return {
        "run_id": run_id,
        "started_at": run_started_at,
        "ended_at": run_ended_at,
        "duration_seconds": max(0, run_ended_at - run_started_at),
        "config": {
            "subreddit": args.subreddit,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "max_posts": args.max_posts,
            "links_file": args.links_file,
            "output": args.output,
            "pending_comments_file": args.pending_comments_file,
            "checkpoint_file": args.checkpoint_file,
            "requests_per_minute": args.requests_per_minute,
            "structured_logs": bool(getattr(args, "structured_logs", False)),
            "comment_sort": args.comment_sort,
            "comment_limit": args.comment_limit,
            "comment_depth": args.comment_depth,
            "rag_min_comment_chars": getattr(args, "rag_min_comment_chars", None),
            "rag_max_comments": getattr(args, "rag_max_comments", None),
            "resume_enabled": not args.no_resume,
        },
        "run_stats": {
            "candidate_links": run_stats["candidate_links"],
            "skipped_already_processed": run_stats["skipped_already_processed"],
            "saved": run_stats["saved"],
            "failed": run_stats["failed"],
            "expected_comments": expected_comments,
            "extracted_comments": extracted_comments,
            "coverage_ratio": round(coverage_ratio, 6),
            "coverage_pct": round(coverage_ratio * 100.0, 2),
            "extracted_comments_before_expansion": extracted_before,
            "extracted_comments_after_expansion": extracted_after,
            "pending_comment_ids_before_expansion": pending_before,
            "pending_comment_ids_resolved": pending_resolved,
            "pending_comment_ids_after_expansion": pending_after,
            "pending_resolution_rate": round(pending_resolution_rate, 6),
            "more_placeholders": run_stats["more_placeholders"],
            "pending_comment_ids": run_stats["pending_comment_ids"],
            "posts_with_pending_comments": run_stats["posts_with_pending_comments"],
            "avg_extracted_comments_per_saved_post": round(avg_extracted, 4),
            "avg_pending_comment_ids_per_saved_post": round(avg_pending, 4),
            "rag_comments_kept": run_stats.get("rag_comments_kept", 0),
            "rag_comments_filtered": run_stats.get("rag_comments_filtered", 0),
        },
        "coverage_by_subreddit": run_stats.get("coverage_by_subreddit", {}),
    }
