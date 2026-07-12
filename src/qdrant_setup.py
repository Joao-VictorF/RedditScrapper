from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests


def _infer_vector_size(
    explicit_size: int,
    summary_path: Path,
    embeddings_path: Path,
) -> int:
    if explicit_size > 0:
        return explicit_size

    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            size = int(summary.get("stats", {}).get("vector_dim", 0))
            if size > 0:
                return size
        except (ValueError, TypeError, json.JSONDecodeError):
            pass

    if embeddings_path.exists():
        with embeddings_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                vector = row.get("embedding")
                if isinstance(vector, list) and vector:
                    return len(vector)

    return 0


def _collection_exists(base_url: str, collection: str, timeout_sec: int) -> bool:
    url = f"{base_url.rstrip('/')}/collections/{collection}"
    response = requests.get(url, timeout=timeout_sec)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or validate Qdrant collection for embeddings")
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
        "--distance",
        default="cosine",
        choices=["cosine", "dot", "euclid"],
        help="Distance metric",
    )
    parser.add_argument(
        "--vector-size",
        type=int,
        default=0,
        help="Embedding vector size. If omitted, inferred from summary or embeddings file",
    )
    parser.add_argument(
        "--embeddings-file",
        default="results/rag/embeddings_plantedtank.jsonl",
        help="Embeddings JSONL path used for vector size inference",
    )
    parser.add_argument(
        "--embeddings-summary-file",
        default="results/rag/embeddings_plantedtank_summary.json",
        help="Embeddings summary JSON path used for vector size inference",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete existing collection and create it again",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=30,
        help="HTTP timeout for Qdrant requests",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    embeddings_path = Path(args.embeddings_file)
    summary_path = Path(args.embeddings_summary_file)

    vector_size = _infer_vector_size(
        explicit_size=args.vector_size,
        summary_path=summary_path,
        embeddings_path=embeddings_path,
    )
    if vector_size <= 0:
        raise ValueError(
            "Could not infer vector size. Provide --vector-size or run embeddings first."
        )

    base_url = args.qdrant_url.rstrip("/")
    collection_url = f"{base_url}/collections/{args.collection}"

    exists = _collection_exists(base_url, args.collection, args.timeout_sec)
    if exists and args.recreate:
        delete_response = requests.delete(collection_url, timeout=args.timeout_sec)
        delete_response.raise_for_status()
        exists = False
        print(f"[qdrant-setup] deleted collection={args.collection}")

    if not exists:
        payload: dict[str, Any] = {
            "vectors": {
                "size": vector_size,
                "distance": args.distance.upper(),
            }
        }
        create_response = requests.put(collection_url, json=payload, timeout=args.timeout_sec)
        create_response.raise_for_status()
        print(
            f"[qdrant-setup] created collection={args.collection} "
            f"vector_size={vector_size} distance={args.distance}"
        )
    else:
        print(f"[qdrant-setup] collection already exists={args.collection}")

    info_response = requests.get(collection_url, timeout=args.timeout_sec)
    info_response.raise_for_status()
    print("[qdrant-setup] collection info:")
    print(json.dumps(info_response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
