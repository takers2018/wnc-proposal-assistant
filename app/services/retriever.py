# app/services/retriever.py
import os, json
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import numpy as np
from dotenv import load_dotenv, dotenv_values
from openai import OpenAI

# Optional FAISS
try:
    import faiss  # type: ignore
except Exception:
    faiss = None

# --- Config / OpenAI client ---
load_dotenv(override=True)
_cfg = dotenv_values()
API_KEY = _cfg.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
EMBED_MODEL = _cfg.get("EMBED_MODEL") or os.getenv("EMBED_MODEL", "text-embedding-3-small")
client = OpenAI(api_key=API_KEY)

# --- Globals (lazy) ---
_LOADED = False
_BASEDIR: Optional[Path] = None
_VECS: Optional[np.ndarray] = None     # normalized vectors [N, D]
_CHUNKS: Optional[List[Dict]] = None   # list of chunk dicts
_FAISS: Optional["faiss.Index"] = None

def _resolve_basedir(kb_path: str) -> Path:
    p = Path(kb_path)
    if p.is_dir():
        return p
    # if it's a file path (legacy), use its directory
    return p.parent

def _load_index(base_dir: Path):
    global _LOADED, _BASEDIR, _VECS, _CHUNKS, _FAISS
    if _LOADED and _BASEDIR == base_dir:
        return
    chunks_path = base_dir / "chunks.jsonl"
    vecs_path   = base_dir / "embeddings.npy"
    faiss_path  = base_dir / "index.faiss"

    if not chunks_path.exists() or not vecs_path.exists():
        raise FileNotFoundError(f"Missing index files in {base_dir}. Run scripts/ingest.py first.")

    # load metadata
    chunks: List[Dict] = []
    with chunks_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))
    # load vectors (already normalized at ingest)
    vecs = np.load(vecs_path.as_posix()).astype(np.float32)

    # faiss (optional)
    index = None
    if faiss and faiss_path.exists():
        index = faiss.read_index(faiss_path.as_posix())

    _BASEDIR = base_dir
    _CHUNKS = chunks
    _VECS = vecs
    _FAISS = index
    _LOADED = True

def _embed_query(text: str) -> np.ndarray:
    resp = client.embeddings.create(model=EMBED_MODEL, input=[text])
    v = np.array(resp.data[0].embedding, dtype=np.float32)
    v = v / (np.linalg.norm(v) + 1e-12)
    return v.reshape(1, -1)

# Accept plural/singular keys and flat dates → nested
def _normalize_filters(f: Optional[Dict]) -> Optional[Dict]:
    if not f:
        return f
    f = dict(f)  # shallow copy

    # plural -> singular keys used internally by _apply_filters()
    if "topics" in f and "topic" not in f:
        f["topic"] = f.pop("topics")
    if "counties" in f and "county" not in f:
        f["county"] = f.pop("counties")

    # flat date_from/date_to -> nested
    if "date_from" in f or "date_to" in f:
        f["date"] = {
            "from": f.pop("date_from", None),
            "to":   f.pop("date_to", None),
        }

    # strip empties
    if "topic"  in f and not f["topic"]:  f.pop("topic")
    if "county" in f and not f["county"]: f.pop("county")
    if "date"   in f and not any((f["date"] or {}).values()): f.pop("date")
    return f

def _apply_filters(mask_idx: np.ndarray, filters: Optional[Dict]) -> np.ndarray:
    if not filters:
        return mask_idx
    # filters: { "county": ["Haywood", ...], "topic": ["small_business", ...], "date": {"from":"YYYY-MM-DD","to":"YYYY-MM-DD"} }
    selected = []
    from_dt = to_dt = None
    if "date" in filters and isinstance(filters["date"], dict):
        from_dt = filters["date"].get("from") or ""
        to_dt   = filters["date"].get("to") or ""

    counties = set([c.strip().lower() for c in filters.get("county", []) if c.strip()]) if "county" in filters else None
    topics   = set([t.strip().lower() for t in filters.get("topic", []) if t.strip()])   if "topic" in filters else None

    for i in mask_idx:
        c = _CHUNKS[i]
        ok = True
        if counties:
            ok = ok and (str(c.get("county", "")).lower() in counties)
        if topics:
            c_topics = [str(t).lower() for t in c.get("topics", [])]
            ok = ok and bool(set(c_topics) & topics)
        if (from_dt or to_dt):
            d = (c.get("date") or "")
            if from_dt and (not d or d < from_dt): ok = False
            if to_dt   and (not d or d > to_dt):   ok = False
        if ok:
            selected.append(i)
    return np.array(selected, dtype=np.int64) if selected else np.array([], dtype=np.int64)

def _topk_numpy(q: np.ndarray, idxs: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]:
    # cosine/IP because vectors are normalized
    X = _VECS[idxs]
    sims = (X @ q.T).ravel()
    if sims.size == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float32)
    k = min(k, sims.size)
    top = np.argpartition(-sims, k-1)[:k]
    order = top[np.argsort(-sims[top])]
    return idxs[order], sims[order]

def retrieve(query: str, kb_path: str = "data/processed", k: int = 8, filters: Optional[Dict] = None) -> List[Dict]:
    """
    Returns a list of context dicts with keys: title, source (url), date, text.
    kb_path can be a directory (preferred) or a legacy file path; we resolve to the directory.
    """
    base_dir = _resolve_basedir(kb_path)
    _load_index(base_dir)
    q = _embed_query(query)
    filters = _normalize_filters(filters)

    # start with all indices
    all_idx = np.arange(len(_CHUNKS), dtype=np.int64)
    # apply filters
    filt_idx = _apply_filters(all_idx, filters) if filters else all_idx

    # choose engine
    if filt_idx.size == len(_CHUNKS) and _FAISS is not None:
        # unrestricted: use faiss for speed
        D, I = _FAISS.search(q, min(k, len(_CHUNKS)))
        idxs = I.ravel()
        sims = D.ravel()
    else:
        # filtered (or no faiss): numpy fallback
        idxs, sims = _topk_numpy(q, filt_idx, k)


    out = []
    for i in idxs:
        c = _CHUNKS[int(i)]
        out.append({
            "doc_id": c.get("doc_id") or "",
            "title": c.get("title") or c.get("doc_id") or "Source",
            # provide both 'url' (preferred) and 'source' (BC for older UI)
            "url": c.get("url") or "",
            "source": c.get("url") or "",   # keep for backward compatibility
            "date": c.get("date") or "",
            "county": c.get("county") or "",
            "topics": c.get("topics", []),
            "text": c.get("text") or "",
        })
    return out

