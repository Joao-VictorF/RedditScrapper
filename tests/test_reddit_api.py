import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scrapper import reddit_api


class TestRedditApi(TestCase):
    def test_parse_cookie_header_basic(self):
        raw = "loid=abc; session_tracker=x.y.z; token_v2=a=b=c"
        parsed = reddit_api.parse_cookie_header(raw)
        self.assertEqual(parsed["loid"], "abc")
        self.assertEqual(parsed["session_tracker"], "x.y.z")
        self.assertEqual(parsed["token_v2"], "a=b=c")

    def test_parse_cookie_header_with_cookie_prefix(self):
        raw = "Cookie: loid=abc; csv=2"
        parsed = reddit_api.parse_cookie_header(raw)
        self.assertEqual(parsed, {"loid": "abc", "csv": "2"})

    def test_build_session_populates_cookie_jar_from_raw_header(self):
        session = reddit_api.build_session(
            user_agent="test-agent",
            cookie="Cookie: loid=abc; token_v2=a=b=c",
        )
        self.assertEqual(session.cookies.get("loid"), "abc")
        self.assertEqual(session.cookies.get("token_v2"), "a=b=c")

    def test_normalize_post_url_full(self):
        url = "https://www.reddit.com/r/AskReddit/comments/abc123/test-post/"
        self.assertEqual(
            reddit_api.normalize_post_url(url),
            "https://www.reddit.com/r/AskReddit/comments/abc123/test-post",
        )

    def test_normalize_post_url_relative(self):
        self.assertEqual(
            reddit_api.normalize_post_url("/r/AskReddit/comments/abc123/test-post/"),
            "https://www.reddit.com/r/AskReddit/comments/abc123/test-post",
        )

    def test_normalize_post_url_json_suffix(self):
        self.assertEqual(
            reddit_api.normalize_post_url("https://www.reddit.com/r/AskReddit/comments/abc123/test-post/.json"),
            "https://www.reddit.com/r/AskReddit/comments/abc123/test-post",
        )

    def test_iter_subreddit_permalinks_by_date_filters_window(self):
        payload = {
            "data": {
                "after": None,
                "children": [
                    {"data": {"created_utc": 200, "permalink": "/r/a/comments/new1/x/"}},
                    {"data": {"created_utc": 150, "permalink": "/r/a/comments/in1/x/"}},
                    {"data": {"created_utc": 90, "permalink": "/r/a/comments/old1/x/"}},
                ],
            }
        }

        with patch("reddit_scrapper.reddit_api.request_json", return_value=payload):
            links = reddit_api.iter_subreddit_permalinks_by_date(
                session=None,
                subreddit="a",
                start_ts=100,
                end_exclusive_ts=180,
                max_posts=0,
                min_delay_sec=0.0,
            )

        self.assertEqual(links, ["https://www.reddit.com/r/a/comments/in1/x/"])
