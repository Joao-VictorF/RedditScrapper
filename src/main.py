from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

BASE_URL = "https://www.reddit.com"
DEFAULT_USER_AGENT = "script:reddit-rag-scraper:v1.0 (by u/your_username)"


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "processed_links": [],
            "saved": 0,
            "failed": 0,
            "expected_comments": 0,
            "extracted_comments": 0,
            "more_placeholders": 0,
            "started_at": int(time.time()),
            "updated_at": int(time.time()),
        }

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid checkpoint format")
    except Exception:
        backup = path.with_suffix(path.suffix + ".corrupt")
        path.rename(backup)
        return {
            "processed_links": [],
            "saved": 0,
            "failed": 0,
            "expected_comments": 0,
            "extracted_comments": 0,
            "more_placeholders": 0,
            "started_at": int(time.time()),
            "updated_at": int(time.time()),
        }

    data.setdefault("processed_links", [])
    data.setdefault("saved", 0)
    data.setdefault("failed", 0)
    data.setdefault("expected_comments", 0)
    data.setdefault("extracted_comments", 0)
    data.setdefault("more_placeholders", 0)
    data.setdefault("started_at", int(time.time()))
    data.setdefault("updated_at", int(time.time()))
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


def summarize_comments(comments: list[dict[str, Any]]) -> tuple[int, int]:
    extracted = 0
    more_placeholders = 0
    for comment in comments:
        if comment.get("kind") == "more":
            more_placeholders += 1
        else:
            extracted += 1
    return extracted, more_placeholders


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

    extracted_comments, more_placeholders = summarize_comments(comments)
    expected_comments = int(post.get("num_comments", 0) or 0)
    coverage_ratio = (extracted_comments / expected_comments) if expected_comments > 0 else 1.0

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
        "num_comments": post.get("num_comments", 0),
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
            "coverage_ratio": round(coverage_ratio, 4),
        },
        "comments": comments,
    }


def iter_subreddit_permalinks(
    session: requests.Session,
    subreddit: str,
    listing: str,
    time_filter: str,
    max_posts: int,
    min_delay_sec: float,
) -> list[str]:
    out: list[str] = []
    after = None

    while len(out) < max_posts:
        batch_limit = min(100, max_posts - len(out))
        params: dict[str, Any] = {
            "limit": batch_limit,
            "raw_json": 1,
            "after": after,
        }
        if listing in {"top", "controversial"}:
            params["t"] = time_filter

        url = f"{BASE_URL}/r/{subreddit}/{listing}.json"
        payload = request_json(session, url, params=params, min_delay_sec=min_delay_sec)
        data = payload.get("data", {})
        children = data.get("children", [])

        if not children:
            break

        for child in children:
            post_data = child.get("data", {})
            permalink = post_data.get("permalink")
            if permalink:
                out.append(f"{BASE_URL}{permalink}")

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
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reddit public JSON scraper for RAG corpus generation")
    parser.add_argument("--subreddit", help="Subreddit name without r/")
    parser.add_argument(
        "--listing",
        choices=["top", "new", "hot", "rising", "controversial"],
        default="top",
        help="Subreddit listing mode",
    )
    parser.add_argument(
        "--time-filter",
        choices=["hour", "day", "week", "month", "year", "all"],
        default="year",
        help="Time filter used for top/controversial",
    )
    parser.add_argument("--max-posts", type=int, default=200, help="Max posts from subreddit listing")
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
        "--requests-per-minute",
        type=float,
        default=float(os.getenv("REDDIT_REQUESTS_PER_MINUTE", "10")),
        help="Global request rate",
    )
    parser.add_argument("--comment-sort", default="top", choices=["confidence", "top", "new", "controversial", "old", "qa"], help="Comment sort")
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


def main() -> None:
    args = parse_args()
    min_delay_sec = 60.0 / max(args.requests_per_minute, 1.0)
    session = build_session(args.user_agent)

    output_path = Path(args.output)
    links_path = Path(args.links_file)
    checkpoint_path = Path(args.checkpoint_file)

    if args.no_resume:
        state = {
            "processed_links": [],
            "saved": 0,
            "failed": 0,
            "expected_comments": 0,
            "extracted_comments": 0,
            "more_placeholders": 0,
            "started_at": int(time.time()),
            "updated_at": int(time.time()),
        }
    else:
        state = load_checkpoint(checkpoint_path)

    explicit_links = read_links_file(links_path)
    subreddit_links: list[str] = []
    if args.subreddit:
        subreddit_links = iter_subreddit_permalinks(
            session=session,
            subreddit=args.subreddit,
            listing=args.listing,
            time_filter=args.time_filter,
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

    if not kept_links:
        print("No links to process. Provide --subreddit and/or a non-empty links file.")
        return

    processed_set = set(state.get("processed_links", []))
    pending_links = [link for link in kept_links if link not in processed_set]

    if not pending_links:
        print("No pending links. Everything is already processed in checkpoint.")
        print(
            "Summary "
            f"Saved={state.get('saved', 0)} "
            f"Failed={state.get('failed', 0)} "
            f"ExpectedComments={state.get('expected_comments', 0)} "
            f"ExtractedComments={state.get('extracted_comments', 0)}"
        )
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
            state["saved"] = int(state.get("saved", 0)) + 1
            state["expected_comments"] = int(state.get("expected_comments", 0)) + int(post_stats.get("expected_comments", 0) or 0)
            state["extracted_comments"] = int(state.get("extracted_comments", 0)) + int(post_stats.get("extracted_comments", 0) or 0)
            state["more_placeholders"] = int(state.get("more_placeholders", 0)) + int(post_stats.get("more_placeholders", 0) or 0)
        except Exception as exc:
            state["failed"] = int(state.get("failed", 0)) + 1
            print(f"[WARN] Failed to fetch post: {link} | {exc}")

        processed_set.add(link)
        state["processed_links"] = sorted(processed_set)
        save_checkpoint(checkpoint_path, state)

    expected_comments = int(state.get("expected_comments", 0))
    extracted_comments = int(state.get("extracted_comments", 0))
    coverage_ratio = (extracted_comments / expected_comments) if expected_comments > 0 else 1.0

    print(
        "Summary "
        f"Saved={state.get('saved', 0)} "
        f"Failed={state.get('failed', 0)} "
        f"ExpectedComments={expected_comments} "
        f"ExtractedComments={extracted_comments} "
        f"Coverage={coverage_ratio:.2%} "
        f"MorePlaceholders={state.get('more_placeholders', 0)}"
    )
    print(f"Output={output_path} Checkpoint={checkpoint_path}")


if __name__ == "__main__":
    main()
