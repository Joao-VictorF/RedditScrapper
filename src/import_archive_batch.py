from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _extract_range_key(path: Path) -> tuple[str, str] | None:
    matches = DATE_RE.findall(path.name)
    if len(matches) < 2:
        return None
    return matches[0], matches[1]


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", text.strip().lower()).strip("._-") or "default"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch runner for annual archive imports")
    parser.add_argument(
        "--input-dir",
        default="raw-contents",
        help="Directory containing posts_*.jsonl and comments_*.jsonl",
    )
    parser.add_argument("--subreddit", default="", help="Optional subreddit filter")
    parser.add_argument("--output-dir", default="results/imported", help="Directory for corpus/summary outputs")
    parser.add_argument("--tmp-db-dir", default=".tmp", help="Directory for temporary sqlite files")
    parser.add_argument("--source-label", default="arctic_shift_dump", help="Source label in generated corpus")
    parser.add_argument("--print-every", type=int, default=200000, help="Progress log interval passed to importer")
    parser.add_argument("--skip-existing", action="store_true", help="Skip ranges when summary file already exists")
    parser.add_argument("--dry-run", action="store_true", help="Only print commands without running")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir)
    tmp_db_dir = Path(args.tmp_db_dir)

    posts_by_range: dict[tuple[str, str], Path] = {}
    comments_by_range: dict[tuple[str, str], Path] = {}

    for post_file in sorted(input_dir.glob("posts_*.jsonl")):
        key = _extract_range_key(post_file)
        if key:
            posts_by_range[key] = post_file

    for comment_file in sorted(input_dir.glob("comments_*.jsonl")):
        key = _extract_range_key(comment_file)
        if key:
            comments_by_range[key] = comment_file

    common_ranges = sorted(set(posts_by_range).intersection(comments_by_range))
    if not common_ranges:
        print("[batch] no matching post/comment ranges found")
        return

    sub_slug = _slug(args.subreddit) if args.subreddit else "all"
    importer_script = Path(__file__).resolve().parent / "import_archive.py"

    print(f"[batch] matched ranges={len(common_ranges)}")

    for start_date, end_date in common_ranges:
        posts_file = posts_by_range[(start_date, end_date)]
        comments_file = comments_by_range[(start_date, end_date)]

        corpus_file = output_dir / f"corpus_{sub_slug}_{start_date}_{end_date}.jsonl"
        summary_file = output_dir / f"summary_{sub_slug}_{start_date}_{end_date}.json"
        tmp_db = tmp_db_dir / f"import_{sub_slug}_{start_date}_{end_date}.sqlite3"

        if args.skip_existing and summary_file.exists():
            print(f"[batch] skip existing {start_date}..{end_date} -> {summary_file}")
            continue

        command = [
            sys.executable,
            str(importer_script),
            "--posts-file",
            str(posts_file),
            "--comments-file",
            str(comments_file),
            "--output",
            str(corpus_file),
            "--summary-file",
            str(summary_file),
            "--tmp-db",
            str(tmp_db),
            "--source-label",
            args.source_label,
            "--print-every",
            str(args.print_every),
        ]

        if args.subreddit:
            command.extend(["--subreddit", args.subreddit])

        print(f"\n[batch] range {start_date}..{end_date}")
        print("[batch] command:")
        print(" ".join(command))

        if args.dry_run:
            continue

        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
