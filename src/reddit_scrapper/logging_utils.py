from __future__ import annotations

import json
import time
from typing import Any


class StructuredLogger:
    def __init__(self, run_id: str, enabled: bool = True) -> None:
        self.run_id = run_id
        self.enabled = enabled

    def log(self, phase: str, event: str, **fields: Any) -> None:
        if not self.enabled:
            return

        payload = {
            "ts": int(time.time()),
            "run_id": self.run_id,
            "phase": phase,
            "event": event,
        }
        payload.update(fields)
        print(json.dumps(payload, ensure_ascii=False))
