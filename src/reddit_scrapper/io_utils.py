from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def copy_if_exists(source: Path, target: Path) -> bool:
    if not source.exists() or not source.is_file():
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True
