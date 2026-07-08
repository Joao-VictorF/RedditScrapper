from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

BASE_URL = "https://www.reddit.com"
DEFAULT_USER_AGENT = "script:reddit-rag-scraper:v1.0 (by u/your_username)"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date_utc(date_str: str) -> datetime:
    # Input format: YYYY-MM-DD
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def to_epoch_seconds(dt: datetime) -> int:
    return int(dt.timestamp())


def new_state_template() -> dict[str, Any]:
    ts = int(time.time())
    return {
        "processed_links": [],
        "saved": 0,
        "failed": 0,
        "expected_comments": 0,
        "extracted_comments": 0,
        "more_placeholders": 0,
        "pending_comment_ids": 0,
        "started_at": ts,
        "updated_at": ts,
    }


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return new_state_template()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid checkpoint format")
    except Exception:
        backup = path.with_suffix(path.suffix + ".corrupt")
        path.rename(backup)
        return new_state_template()

    defaults = new_state_template()
    for key, value in defaults.items():
        data.setdefault(key, value)

    if not isinstance(data.get("processed_links"), list):
        data["processed_links"] = []

    return data


def save_checkpoint(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = int(time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_post_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        return ""

    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"{BASE_URL}{url if url.startswith('/') else '/' + url}"
        parsed = urlparse(url)

    path = parsed.path.rstrip("/")
    if path.endswith(".json"):
        path = path[:-5]

    return f"{BASE_URL}{path}"


def build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


def request_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None,
    min_delay_sec: float,
    max_retries: int = 5,
) -> Any:
    for attempt in range(max_retries):
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            sleep_for = min(60, (2**attempt) + random.random())
            time.sleep(max(min_delay_sec, sleep_for))
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            retry_seconds = float(retry_after) if retry_after else min(60, (2**attempt) + random.random())
            time.sleep(max(min_delay_sec, retry_seconds))
            continue

        if 500 <= response.status_code < 600:
            sleep_for = min(60, (2**attempt) + random.random())
            time.sleep(max(min_delay_sec, sleep_for))
            continue

        response.raise_for_status()
        time.sleep(min_delay_sec)
        return response.json()

    raise RuntimeError(f"Failed to fetch URL after retries: {url}")


def extract_comments(children: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for child in children:
        kind = child.get("kind")
        data = child.get("data", {})

        if kind == "t1":
            item = {
                "id": data.get("id"),
                "parent_id": data.get("parent_id"),
                "author": data.get("author"),
                "body": data.get("body", ""),
                "score": data.get("score", 0),
                "created_utc": data.get("created_utc"),
                "depth": depth,
                "is_submitter": data.get("is_submitter", False),
                "controversiality": data.get("controversiality", 0),
            }
            out.append(item)

            replies = data.get("replies")
            if isinstance(replies, dict):
                nested_children = replies.get("data", {}).get("children", [])
                out.extend(extract_comments(nested_children, depth + 1))

        elif kind == "more":
            out.append(
                {
                    "id": data.get("id"),
                    "parent_id": data.get("parent_id"),
                    "kind": "more",
                    "count": data.get("count", 0),
                    "children_ids": data.get("children", []),
                    "depth": depth,
                }
            )

    return out


def summarize_comments(comments: list[dict[str, Any]]) -> tuple[int, int, int]:
    extracted = 0
    more_placeholders = 0
    pending_ids = 0

    for comment in comments:
        if comment.get("kind") == "more":
            more_placeholders += 1
            pending_ids += len(comment.get("children_ids", []))
        else:
            extracted += 1

    return extracted, more_placeholders, pending_ids


def collect_pending_comment_ids(comments: list[dict[str, Any]]) -> list[str]:
    pending: set[str] = set()
    for comment in comments:
        if comment.get("kind") == "more":
            for child_id in comment.get("children_ids", []):
                if isinstance(child_id, str) and child_id:
                    pending.add(child_id)
    return sorted(pending)


def fetch_post_document(
    session: requests.Session,
    post_url: str,
    min_delay_sec: float,
    comment_sort: str,
    comment_limit: int,
    comment_depth: int,
) -> dict[str, Any]:
    normalized = normalize_post_url(post_url)
    json_url = f"{normalized}.json"
    params = {
        "raw_json": 1,
        "sort": comment_sort,
        "limit": comment_limit,
        "depth": comment_depth,
    }
    data = request_json(session, json_url, params=params, min_delay_sec=min_delay_sec)

    post = data[0]["data"]["children"][0]["data"]
    comments_root = data[1]["data"]["children"]
    comments = extract_comments(comments_root)

    extracted_comments, more_placeholders, pending_comment_ids_count = summarize_comments(comments)
    expected_comments = int(post.get("num_comments", 0) or 0)
    coverage_ratio = (extracted_comments / expected_comments) if expected_comments > 0 else 1.0
    pending_comment_ids = collect_pending_comment_ids(comments)

    return {
        "source": "reddit_public_json",
        "fetched_at": int(time.time()),
        "id": post.get("id"),
        "name": post.get("name"),
        "subreddit": post.get("subreddit"),
        "subreddit_id": post.get("subreddit_id"),
        "title": post.get("title", ""),
        "selftext": post.get("selftext", ""),
        "author": post.get("author"),
        "score": post.get("score", 0),
        "upvote_ratio": post.get("upvote_ratio"),
        "num_comments": expected_comments,
        "created_utc": post.get("created_utc"),
        "over_18": post.get("over_18", False),
        "spoiler": post.get("spoiler", False),
        "locked": post.get("locked", False),
        "stickied": post.get("stickied", False),
        "link_flair_text": post.get("link_flair_text"),
        "url": f"{BASE_URL}{post.get('permalink', '')}",
        "permalink": post.get("permalink"),
        "domain": post.get("domain"),
        "is_self": post.get("is_self", False),
        "stats": {
            "expected_comments": expected_comments,
            "extracted_comments": extracted_comments,
            "more_placeholders": more_placeholders,
            "pending_comment_ids": pending_comment_ids_count,
            "coverage_ratio": round(coverage_ratio, 4),
        },
        "pending_comment_ids": pending_comment_ids,
        "comments": comments,
    }


def iter_subreddit_permalinks_by_date(
    session: requests.Session,
    subreddit: str,
    start_date: datetime,
    end_date: datetime,
    max_posts: int,
    min_delay_sec: float,
) -> list[str]:
    # end_date is inclusive in CLI, convert to end-exclusive boundary.
    start_ts = to_epoch_seconds(start_date)
    end_exclusive_ts = to_epoch_seconds(end_date + timedelta(days=1))

    out: list[str] = []
    after = None

    while True:
        if max_posts > 0 and len(out) >= max_posts:
            break

        batch_limit = 100
        if max_posts > 0:
            batch_limit = min(100, max_posts - len(out))
            if batch_limit <= 0:
                break

        params: dict[str, Any] = {
            "limit": batch_limit,
            "raw_json": 1,
            "after": after,
        }

        url = f"{BASE_URL}/r/{subreddit}/new.json"
        payload = request_json(session, url, params=params, min_delay_sec=min_delay_sec)
        data = payload.get("data", {})
        children = data.get("children", [])

        if not children:
            break

        reached_older_than_start = False
        for child in children:
            post_data = child.get("data", {})
            created_utc = int(post_data.get("created_utc", 0) or 0)

            if created_utc < start_ts:
                reached_older_than_start = True
                break

            if created_utc >= end_exclusive_ts:
                continue

            permalink = post_data.get("permalink")
            if permalink:
                out.append(f"{BASE_URL}{permalink}")

                if max_posts > 0 and len(out) >= max_posts:
                    break

        if reached_older_than_start:
            break

        if max_posts > 0 and len(out) >= max_posts:
            break

        after = data.get("after")
        if not after:
            break

    return out


def read_links_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    links: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        links.append(raw)
    return links


def append_jsonl(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")


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
        "--summary-dir",
        default=os.getenv("REDDIT_SUMMARY_DIR", "run_summaries"),
        help="Directory where per-run summary JSON files are written",
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


def build_run_summary(
    run_id: str,
    run_started_at: int,
    run_ended_at: int,
    args: argparse.Namespace,
    run_stats: dict[str, int],
) -> dict[str, Any]:
    processed_success = run_stats["saved"]
    expected_comments = run_stats["expected_comments"]
    extracted_comments = run_stats["extracted_comments"]
    coverage_ratio = (extracted_comments / expected_comments) if expected_comments > 0 else 1.0

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
            "comment_sort": args.comment_sort,
            "comment_limit": args.comment_limit,
            "comment_depth": args.comment_depth,
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
            "more_placeholders": run_stats["more_placeholders"],
            "pending_comment_ids": run_stats["pending_comment_ids"],
            "posts_with_pending_comments": run_stats["posts_with_pending_comments"],
            "avg_extracted_comments_per_saved_post": round(avg_extracted, 4),
            "avg_pending_comment_ids_per_saved_post": round(avg_pending, 4),
        },
    }


def main() -> None:
    args = parse_args()
    start_date, end_date = validate_date_args(args)

    min_delay_sec = 60.0 / max(args.requests_per_minute, 1.0)
    session = build_session(args.user_agent)

    output_path = Path(args.output)
    links_path = Path(args.links_file)
    checkpoint_path = Path(args.checkpoint_file)
    pending_comments_path = Path(args.pending_comments_file)
    summary_dir = Path(args.summary_dir)

    run_started_at = int(time.time())
    run_id = datetime.fromtimestamp(run_started_at, timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if args.no_resume:
        state = new_state_template()
    else:
        state = load_checkpoint(checkpoint_path)

    explicit_links = read_links_file(links_path)
    subreddit_links: list[str] = []
    if args.subreddit and start_date and end_date:
        subreddit_links = iter_subreddit_permalinks_by_date(
            session=session,
            subreddit=args.subreddit,
            start_date=start_date,
            end_date=end_date,
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
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / f"summary_{run_id}.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
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
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / f"summary_{run_id}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

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


if __name__ == "__main__":
    main()
