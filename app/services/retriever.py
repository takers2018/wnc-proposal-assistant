# app/services/retriever.py
import os, json
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from typing import Any

def _dedupe_adjacent_by_doc(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out, prev_doc = [], None
    for h in hits:
        doc = h.get("doc_id")
        if doc and doc == prev_doc:
            continue
        out.append(h)
        prev_doc = doc
    return out

import numpy as np
from dotenv import load_dotenv, dotenv_values
from openai import OpenAI

from datetime import datetime

def _parse_iso_or_none(s: Optional[str]):
    try:
        return datetime.fromisoformat(s) if s else None
    except Exception:
        return None

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
        # strict date window: undated chunks FAIL when a date filter is present
        if (from_dt or to_dt):
            d  = _parse_iso_or_none(c.get("date"))
            df = _parse_iso_or_none(from_dt) if from_dt else None
            dt_ = _parse_iso_or_none(to_dt) if to_dt else None

            if d is None:
                ok = False
            else:
                if df and d < df:   ok = False
                if dt_ and d > dt_: ok = False

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

    # STRICT: if any filter was provided and nothing matched, do NOT fall back
    if filters and (getattr(filt_idx, "size", None) == 0 or len(getattr(filt_idx, "__array_interface__", None) and filt_idx or []) == 0):
        return []

    # STRICT: if any filter present and nothing matched, return no results (no fallback)
    if filters and filt_idx.size == 0:
        return []

    # compute how many to pull *before* dedupe (strict over the filtered pool)
    pool_size = int(filt_idx.size) if filters else len(_CHUNKS)
    request_k = min(int(k), pool_size)
    
    # choose engine
    if filt_idx.size == len(_CHUNKS) and _FAISS is not None:
        # unrestricted: use faiss for speed
        D, I = _FAISS.search(q, request_k)
        idxs = I.ravel()
        sims = D.ravel()
    else:
        # filtered (or no faiss): numpy fallback
        idxs, sims = _topk_numpy(q, filt_idx, request_k)

    out = []
    for i in idxs:
        c = _CHUNKS[int(i)]
        out.append({
            "doc_id": c.get("doc_id") or "",
            "title": c.get("title") or c.get("doc_id") or "Source",
            # provide both 'url' (preferred) and 'source' (BC for older UI)
            "url": c.get("url") or None,     # Optional[HttpUrl] plays nicer with None
            "source": c.get("url") or "",    # legacy BC
            "date": c.get("date") or None,   # IMPORTANT: None, not ""
            "county": c.get("county") or None,
            "topics": (c.get("topics") or []),
            "text": c.get("text") or "",
        })
    out = _dedupe_adjacent_by_doc(out)
    return out[:k]


