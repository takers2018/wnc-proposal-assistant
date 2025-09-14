import json, os, math
from typing import List, Dict, Any
import numpy as np
from openai import OpenAI

EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
client = OpenAI()

def _embed(texts: List[str]) -> np.ndarray:
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    vecs = [np.array(d.embedding, dtype=np.float32) for d in resp.data]
    # Normalize for cosine similarity
    return np.vstack([v / (np.linalg.norm(v) + 1e-9) for v in vecs])

def load_context(path: str) -> List[Dict[str, Any]]:
    items = []
    if not os.path.exists(path):
        return items
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            items.append(json.loads(line))
    return items

def retrieve(query: str, kb_path: str, k: int = 8, filters: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    docs = load_context(kb_path)
    if not docs:
        return []

    texts = [d.get("text", "") for d in docs]
    doc_vecs = _embed(texts)
    q_vec = _embed([query])[0]

    sims = doc_vecs @ q_vec
    idxs = np.argsort(-sims)[: k * 3]  # wider pool
    candidates = [docs[i] | {"score": float(sims[i])} for i in idxs]

    # simple filter example
    if filters:
        def ok(d):
            for key, val in filters.items():
                if val is None:
                    continue
                if d.get(key) != val:
                    return False
            return True
        candidates = [d for d in candidates if ok(d)]

    # Take top-k after filter
    candidates = sorted(candidates, key=lambda d: d["score"], reverse=True)[:k]
    return candidates
