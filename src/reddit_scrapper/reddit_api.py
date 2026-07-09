from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

BASE_URL = "https://www.reddit.com"


class RedditBlockedError(RuntimeError):
    """Raised when Reddit blocks requests from this runtime environment."""


class RedditInvalidCookieError(ValueError):
    """Raised when the provided cookie cannot be encoded as an HTTP header."""


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
    post_title = (post_title or "").strip()
    post_selftext = (post_selftext or "").strip()
    post_text = post_title
    if post_selftext:
        post_text = f"{post_title}\n\n{post_selftext}".strip()

    comment_texts: list[str] = []
    filtered_out = 0
    for comment in comments:
        if comment.get("kind") == "more":
            continue
        body = comment.get("body", "")
        if not _is_useful_comment_text(body, min_comment_chars):
            filtered_out += 1
            continue

        comment_texts.append(body.strip())
        if len(comment_texts) >= max_comments:
            break

    combined_parts = [post_text] if post_text else []
    if comment_texts:
        combined_parts.append("\n\n".join(comment_texts))

    combined_text = "\n\n".join(part for part in combined_parts if part)
    return {
        "post_text": post_text,
        "comment_texts": comment_texts,
        "combined_text": combined_text,
        "stats": {
            "comments_kept": len(comment_texts),
            "comments_filtered": filtered_out,
        },
    }


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
        path = path[:-5].rstrip("/")

    return f"{BASE_URL}{path}"


def build_session(user_agent: str, cookie: str | None = None) -> requests.Session:
    session = requests.Session()
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }
    if cookie:
        try:
            cookie.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise RedditInvalidCookieError(
                "Cookie contains non-latin1 characters (often caused by copied ellipsis '…' or truncated value)."
            ) from exc
        headers["Cookie"] = cookie
    session.headers.update(headers)
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
        except UnicodeEncodeError as exc:
            raise RedditInvalidCookieError(
                "Cookie header is invalid for HTTP transport. Copy the raw Cookie header as a single line, without ellipsis or truncation."
            ) from exc
        except requests.RequestException:
            sleep_for = min(60, (2**attempt) + random.random())
            time.sleep(max(min_delay_sec, sleep_for))
            continue

        content_type = (response.headers.get("content-type") or "").lower()
        body_prefix = response.text[:200].lower() if response.text else ""

        if response.status_code == 403 and "text/html" in content_type:
            if "blocked" in body_prefix or "theme-beta" in body_prefix:
                raise RedditBlockedError(
                    "Reddit returned 403 Blocked HTML for this request. "
                    "This environment appears blocked for direct API scraping."
                )

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
    rag_min_comment_chars: int,
    rag_max_comments: int,
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
    rag_payload = _build_rag_payload(
        post_title=post.get("title", ""),
        post_selftext=post.get("selftext", ""),
        comments=comments,
        min_comment_chars=rag_min_comment_chars,
        max_comments=rag_max_comments,
    )
    rag_stats = rag_payload["stats"]

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
            "extracted_comments_before_expansion": extracted_comments,
            "extracted_comments_after_expansion": extracted_comments,
            "more_placeholders": more_placeholders,
            "pending_comment_ids_before_expansion": pending_comment_ids_count,
            "pending_comment_ids_resolved": 0,
            "pending_comment_ids_after_expansion": pending_comment_ids_count,
            "pending_resolution_rate": 0.0 if pending_comment_ids_count > 0 else 1.0,
            "coverage_ratio": round(coverage_ratio, 4),
            "rag_comments_kept": rag_stats["comments_kept"],
            "rag_comments_filtered": rag_stats["comments_filtered"],
        },
        "pending_comment_ids": pending_comment_ids,
        "rag": {
            "post_text": rag_payload["post_text"],
            "comment_texts": rag_payload["comment_texts"],
            "combined_text": rag_payload["combined_text"],
        },
        "comments": comments,
    }


def iter_subreddit_permalinks_by_date(
    session: requests.Session,
    subreddit: str,
    start_ts: int,
    end_exclusive_ts: int,
    max_posts: int,
    min_delay_sec: float,
) -> list[str]:
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


def end_date_to_exclusive_epoch(end_date: datetime) -> int:
    return int((end_date + timedelta(days=1)).timestamp())
