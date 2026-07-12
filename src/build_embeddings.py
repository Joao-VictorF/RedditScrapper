from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests


def _request_ollama_embedding(base_url: str, model: str, text: str, timeout_sec: int) -> list[float]:
    # Prefer newer /api/embed endpoint with batched input.
    embed_url = f"{base_url.rstrip('/')}/api/embed"
    payload = {"model": model, "input": [text]}
    try:
        response = requests.post(embed_url, json=payload, timeout=timeout_sec)
        if response.status_code == 200:
            data = response.json()
            embeddings = data.get("embeddings", [])
            if embeddings and isinstance(embeddings[0], list):
                return [float(x) for x in embeddings[0]]
    except requests.RequestException:
        pass

    # Backward-compatible fallback.
    legacy_url = f"{base_url.rstrip('/')}/api/embeddings"
    legacy_payload = {"model": model, "prompt": text}
    response = requests.post(legacy_url, json=legacy_payload, timeout=timeout_sec)
    response.raise_for_status()
    data = response.json()
    vector = data.get("embedding")
    if not isinstance(vector, list):
        raise ValueError("Invalid embedding response from Ollama")
    return [float(x) for x in vector]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate embeddings for RAG chunks JSONL")
    parser.add_argument(
        "--input",
        default="results/rag/chunks_plantedtank.jsonl",
        help="Input chunks JSONL",
    )
    parser.add_argument(
        "--output",
        default="results/rag/embeddings_plantedtank.jsonl",
        help="Output embeddings JSONL",
    )
    parser.add_argument(
        "--summary-file",
        default="results/rag/embeddings_plantedtank_summary.json",
        help="Output summary JSON",
    )
    parser.add_argument(
        "--provider",
        default="ollama",
        choices=["ollama"],
        help="Embedding provider",
    )
    parser.add_argument(
        "--model",
        default="nomic-embed-text",
        help="Embedding model name",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://localhost:11434",
        help="Ollama base URL",
    )
    parser.add_argument(
        "--text-field",
        default="text",
        help="Field from chunk JSONL used as embedding input",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Optional cap of processed chunks (0 = all)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append mode and skip chunk_ids already written",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=120,
        help="HTTP timeout per embedding request",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=1000,
        help="Progress log interval",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    summary_path = Path(args.summary_file)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    existing_ids: set[str] = set()
    if args.resume and output_path.exists():
        with output_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                chunk_id = str(row.get("chunk_id", "")).strip()
                if chunk_id:
                    existing_ids.add(chunk_id)
        print(f"[embed] resume mode: loaded existing chunk_ids={len(existing_ids)}")

    started_at = int(time.time())

    scanned = 0
    skipped_existing = 0
    embedded = 0
    failed = 0
    vector_dim: int | None = None

    mode = "a" if args.resume and output_path.exists() else "w"
    with input_path.open("r", encoding="utf-8") as in_fh, output_path.open(mode, encoding="utf-8") as out_fh:
        for line in in_fh:
            raw = line.strip()
            if not raw:
                continue

            scanned += 1
            if args.max_items > 0 and embedded >= args.max_items:
                break

            chunk = json.loads(raw)
            chunk_id = str(chunk.get("chunk_id", "")).strip()
            if args.resume and chunk_id and chunk_id in existing_ids:
                skipped_existing += 1
                continue

            text = str(chunk.get(args.text_field, "")).strip()
            if not text:
                failed += 1
                continue

            try:
                if args.provider == "ollama":
                    vector = _request_ollama_embedding(
                        base_url=args.ollama_url,
                        model=args.model,
                        text=text,
                        timeout_sec=args.timeout_sec,
                    )
                else:
                    raise ValueError(f"Unsupported provider: {args.provider}")

                if vector_dim is None:
                    vector_dim = len(vector)

                out_doc = {
                    "chunk_id": chunk_id,
                    "post_id": chunk.get("post_id"),
                    "subreddit": chunk.get("subreddit"),
                    "created_utc": chunk.get("created_utc"),
                    "chunk_index": chunk.get("chunk_index"),
                    "chunk_total": chunk.get("chunk_total"),
                    "comment_count": chunk.get("comment_count"),
                    "comment_ids": chunk.get("comment_ids"),
                    "token_estimate": chunk.get("token_estimate"),
                    "source": chunk.get("source"),
                    "model": args.model,
                    "provider": args.provider,
                    "text": text,
                    "embedding": vector,
                }
                out_fh.write(json.dumps(out_doc, ensure_ascii=False) + "\n")
                embedded += 1
            except Exception as exc:
                failed += 1
                if args.print_every > 0:
                    print(f"[embed] warning chunk_id={chunk_id} error={exc}")

            if args.print_every > 0 and scanned % args.print_every == 0:
                print(
                    f"[embed] scanned={scanned} embedded={embedded} "
                    f"failed={failed} skipped_existing={skipped_existing}"
                )

    ended_at = int(time.time())
    summary = {
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": ended_at - started_at,
        "config": {
            "input": str(input_path),
            "output": str(output_path),
            "provider": args.provider,
            "model": args.model,
            "text_field": args.text_field,
            "resume": args.resume,
            "max_items": args.max_items,
        },
        "stats": {
            "scanned_chunks": scanned,
            "embedded_chunks": embedded,
            "failed_chunks": failed,
            "skipped_existing": skipped_existing,
            "vector_dim": vector_dim,
        },
    }

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "[embed] done "
        f"embedded={embedded} failed={failed} skipped_existing={skipped_existing} "
        f"vector_dim={vector_dim}"
    )
    print(f"[embed] output={output_path}")
    print(f"[embed] summary={summary_path}")


if __name__ == "__main__":
    main()
