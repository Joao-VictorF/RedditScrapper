from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

import requests


def _point_id_from_chunk_id(chunk_id: str) -> str:
    if not chunk_id:
        chunk_id = str(uuid.uuid4())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _to_points(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in batch:
        chunk_id = str(row.get("chunk_id", "")).strip()
        vector = row.get("embedding")
        if not isinstance(vector, list) or not vector:
            continue

        payload = {
            "chunk_id": chunk_id,
            "post_id": row.get("post_id"),
            "subreddit": row.get("subreddit"),
            "created_utc": row.get("created_utc"),
            "chunk_index": row.get("chunk_index"),
            "chunk_total": row.get("chunk_total"),
            "comment_count": row.get("comment_count"),
            "comment_ids": row.get("comment_ids"),
            "token_estimate": row.get("token_estimate"),
            "source": row.get("source"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "text": row.get("text"),
        }
        points.append(
            {
                "id": _point_id_from_chunk_id(chunk_id),
                "vector": [float(v) for v in vector],
                "payload": payload,
            }
        )
    return points


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest embeddings JSONL into Qdrant")
    parser.add_argument(
        "--embeddings-file",
        default="results/rag/embeddings_plantedtank.jsonl",
        help="Embeddings JSONL path",
    )
    parser.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
        help="Qdrant base URL",
    )
    parser.add_argument(
        "--collection",
        default="plantedtank_chunks",
        help="Qdrant collection name",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="Upsert batch size",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for each upsert to be persisted before returning",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Optional cap of processed embeddings (0 means all)",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=1000,
        help="Progress log interval",
    )
    parser.add_argument(
        "--summary-file",
        default="results/rag/qdrant_ingest_summary.json",
        help="Summary JSON output path",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=60,
        help="HTTP timeout",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    embeddings_path = Path(args.embeddings_file)
    summary_path = Path(args.summary_file)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = int(time.time())

    scanned = 0
    valid_vectors = 0
    upserted = 0
    failed = 0

    buffer: list[dict[str, Any]] = []
    upsert_url = (
        f"{args.qdrant_url.rstrip('/')}/collections/{args.collection}/points"
        f"?wait={'true' if args.wait else 'false'}"
    )

    def flush() -> None:
        nonlocal upserted, failed
        if not buffer:
            return
        points = _to_points(buffer)
        if not points:
            buffer.clear()
            return

        payload = {"points": points}
        try:
            response = requests.put(upsert_url, json=payload, timeout=args.timeout_sec)
            response.raise_for_status()
            upserted += len(points)
        except requests.RequestException as exc:
            failed += len(points)
            print(f"[qdrant-ingest] upsert batch failed size={len(points)} error={exc}")
        finally:
            buffer.clear()

    with embeddings_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue

            scanned += 1
            if args.max_items > 0 and scanned > args.max_items:
                break

            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                failed += 1
                continue

            vector = row.get("embedding")
            if not isinstance(vector, list) or not vector:
                failed += 1
                continue

            valid_vectors += 1
            buffer.append(row)

            if len(buffer) >= args.batch_size:
                flush()

            if args.print_every > 0 and scanned % args.print_every == 0:
                print(
                    f"[qdrant-ingest] scanned={scanned} valid_vectors={valid_vectors} "
                    f"upserted={upserted} failed={failed}"
                )

    flush()

    ended_at = int(time.time())
    summary = {
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": ended_at - started_at,
        "config": {
            "embeddings_file": str(embeddings_path),
            "qdrant_url": args.qdrant_url,
            "collection": args.collection,
            "batch_size": args.batch_size,
            "wait": args.wait,
            "max_items": args.max_items,
        },
        "stats": {
            "scanned_rows": scanned,
            "valid_vectors": valid_vectors,
            "upserted_points": upserted,
            "failed_rows_or_points": failed,
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "[qdrant-ingest] done "
        f"scanned={scanned} valid_vectors={valid_vectors} upserted={upserted} failed={failed}"
    )
    print(f"[qdrant-ingest] summary={summary_path}")


if __name__ == "__main__":
    main()
