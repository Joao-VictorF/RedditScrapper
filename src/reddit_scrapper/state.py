from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


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
