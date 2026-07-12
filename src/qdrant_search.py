from __future__ import annotations

import argparse
import json

import requests


def _request_ollama_embedding(base_url: str, model: str, text: str, timeout_sec: int) -> list[float]:
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
    parser = argparse.ArgumentParser(description="Run semantic search in Qdrant using Ollama query embedding")
    parser.add_argument(
        "--query",
        required=True,
        help="User query text",
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
        "--top-k",
        type=int,
        default=5,
        help="Top results",
    )
    parser.add_argument(
        "--subreddit",
        default="",
        help="Optional subreddit payload filter",
    )
    parser.add_argument(
        "--model",
        default="nomic-embed-text",
        help="Ollama embedding model",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://localhost:11434",
        help="Ollama base URL",
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

    query_vector = _request_ollama_embedding(
        base_url=args.ollama_url,
        model=args.model,
        text=args.query,
        timeout_sec=args.timeout_sec,
    )

    search_url = f"{args.qdrant_url.rstrip('/')}/collections/{args.collection}/points/search"
    payload: dict[str, object] = {
        "vector": query_vector,
        "limit": args.top_k,
        "with_payload": True,
        "with_vector": False,
    }
    if args.subreddit.strip():
        payload["filter"] = {
            "must": [
                {
                    "key": "subreddit",
                    "match": {"value": args.subreddit.strip()},
                }
            ]
        }

    response = requests.post(search_url, json=payload, timeout=args.timeout_sec)
    response.raise_for_status()
    data = response.json()
    results = data.get("result", [])

    print(f"[qdrant-search] query={args.query}")
    print(f"[qdrant-search] matches={len(results)}")
    for idx, item in enumerate(results, start=1):
        score = item.get("score")
        payload_row = item.get("payload", {})
        text = str(payload_row.get("text", ""))
        excerpt = " ".join(text.split())[:260]
        print(
            f"[{idx}] score={score} chunk_id={payload_row.get('chunk_id')} "
            f"post_id={payload_row.get('post_id')} subreddit={payload_row.get('subreddit')}"
        )
        print(f"     {excerpt}")

    print("[qdrant-search] raw response json:")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
