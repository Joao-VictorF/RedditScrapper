import sys
from argparse import Namespace
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scrapper.cli import resolve_run_paths


class TestCliPaths(TestCase):
    def test_resolve_run_paths_with_label_and_subreddit_dates(self):
        args = Namespace(
            subreddit="AskReddit",
            start_date="2024-01-01",
            end_date="2024-01-31",
            run_label="Nightly Batch",
            results_root="results/runs",
            output="corpus.jsonl",
            pending_comments_file="pending_comments.jsonl",
            checkpoint_file="checkpoint.json",
        )

        paths = resolve_run_paths(args, run_id="20240131T000000Z")
        run_dir = str(paths["run_dir"])

        self.assertIn("subreddit=askreddit", run_dir)
        self.assertIn("period=2024-01-01_to_2024-01-31", run_dir)
        self.assertIn("label=nightly-batch", run_dir)

        self.assertTrue(str(paths["output_path"]).endswith("corpus.jsonl"))
        self.assertTrue(str(paths["pending_comments_path"]).endswith("pending_comments.jsonl"))
        self.assertTrue(str(paths["checkpoint_path"]).endswith("checkpoint.json"))
