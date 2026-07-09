import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scrapper.state import load_checkpoint, new_state_template, save_checkpoint


class TestState(TestCase):
    def test_new_state_template(self):
        state = new_state_template()
        self.assertEqual(state["processed_links"], [])
        self.assertEqual(state["saved"], 0)
        self.assertEqual(state["failed"], 0)

    def test_save_and_load_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "checkpoint.json"
            state = new_state_template()
            state["processed_links"] = ["https://www.reddit.com/r/x/comments/1/a"]
            state["saved"] = 1
            save_checkpoint(cp, state)

            loaded = load_checkpoint(cp)
            self.assertEqual(loaded["processed_links"], state["processed_links"])
            self.assertEqual(loaded["saved"], 1)

            raw = json.loads(cp.read_text(encoding="utf-8"))
            self.assertIn("updated_at", raw)
