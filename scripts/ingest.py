# scripts/ingest.py (memory-safe, resilient URL fetch, fast chunker with fallback)
import os, re, json, argparse, time, gc
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse

import numpy as np
from dotenv import load_dotenv, dotenv_values
from openai import OpenAI

import hashlib

# Optional deps (graceful if missing)
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None
try:
    import docx  # python-docx
except Exception:
    docx = None
try:
    import bs4  # beautifulsoup4
    from bs4 import BeautifulSoup
    import httpx
except Exception:
    bs4 = None

try:
    import faiss  # type: ignore
except Exception:
    faiss = None

# --- Config / OpenAI ---
load_dotenv(override=True)
_cfg = dotenv_values()
API_KEY = _cfg.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
MODEL_EMB = _cfg.get("EMBED_MODEL") or os.getenv("EMBED_MODEL", "text-embedding-3-small")
client = OpenAI(api_key=API_KEY)

# --- Defaults ---
DEFAULT_TARGET = 1200
DEFAULT_OVERLAP = 200
DEFAULT_MAX_DOC_CHARS = 200_000  # cap per document AFTER cleaning

# --- Helpers ---
def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def clean_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\u00A0", " ").strip()
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def file_uri(path: Path) -> str:
    return path.resolve().as_uri()

def chunk_text(text: str, target: int, overlap: int, max_doc_chars: int) -> List[str]:
    """Linear-time chunker with safe fallbacks and progress guarantee."""
    text = text.strip()
    if len(text) > max_doc_chars:
        text = text[:max_doc_chars]

    n = len(text)
    if n == 0:
        return []

    chunks: List[str] = []
    i = 0
    try:
        while i < n:
            end = min(i + target, n)
            cut = end
            win_start = max(i, end - 200)
            nl = text.rfind("\n", win_start, end)
            if nl != -1 and nl > i:
                cut = nl
            else:
                sp = text.rfind(" ", win_start, end)
                if sp != -1 and sp > i:
                    cut = sp
            if cut <= i:
                cut = end
            chunk = text[i:cut].strip()
            if chunk:
                chunks.append(chunk)
            if cut >= n:
                break
            i = cut - overlap
            if i < 0:
                i = 0
    except MemoryError:
        # Fallback: smaller slices, zero-overlap, avoids list growth spikes
        chunks = []
        step = max(256, target // 2)
        i = 0
        while i < n:
            end = min(i + step, n)
            chunks.append(text[i:end])
            i = end
    return chunks


# --- Loaders ---
def load_pdf(path: Path) -> str:
    if not fitz:
        raise RuntimeError("PyMuPDF not installed. pip install pymupdf")
    doc = fitz.open(path.as_posix())
    try:
        parts = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    return clean_text("\n".join(parts))


def load_docx(path: Path) -> str:
    if not docx:
        raise RuntimeError("python-docx not installed. pip install python-docx")
    d = docx.Document(path.as_posix())
    return clean_text("\n".join(p.text for p in d.paragraphs if p.text.strip()))


def load_url(url: str, max_doc_chars: int) -> Dict[str, str]:
    if not bs4:
        raise RuntimeError("beautifulsoup4/httpx not installed. pip install beautifulsoup4 httpx")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)

    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as s:
        r = s.get(url)
        r.raise_for_status()
        html = r.text

    # Prefer lxml, fall back to builtin
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title = (soup.title.string.strip() if soup.title and soup.title.string else urlparse(url).netloc)

    # Collect readable text
    texts = []
    for tag in soup.select("h1,h2,h3,p,li"):
        t = tag.get_text(" ", strip=True)
        if t and len(t) > 3:
            texts.append(t)

    # Free memory early
    del html
    body = clean_text("\n".join(texts))
    del texts, soup
    gc.collect()

    if len(body) > max_doc_chars:
        body = body[:max_doc_chars]

    return {"title": title, "text": body}


# --- Embeddings (batched) ---
def embed_texts(texts: List[str], model: str, batch: int = 96) -> np.ndarray:
    vecs = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i+batch]
        print(f"[EMB] {i+1}-{i+len(chunk)} / {len(texts)}", flush=True)
        try:
            resp = client.embeddings.create(model=model, input=chunk)
        except Exception as e:
            # transient network issues
            print(f"[EMB][WARN] retrying batch due to: {e}")
            time.sleep(1.0)
            resp = client.embeddings.create(model=model, input=chunk)
        arr = np.array([item.embedding for item in resp.data], dtype=np.float32)
        # L2 normalize batch
        norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
        arr = arr / norms
        vecs.append(arr)
        # allow GC on response
        del resp, arr
        gc.collect()
    X = np.vstack(vecs)
    del vecs
    gc.collect()
    return X


# --- CLI / Main ---
def main():
    ap = argparse.ArgumentParser(description="Ingest files/urls -> chunks -> embeddings -> index")

    ap.add_argument("--pdf", action="extend", nargs="+", default=[], help="Paths to PDF files")
    ap.add_argument("--docx", action="extend", nargs="+", default=[], help="Paths to DOCX files")
    ap.add_argument("--url", action="extend", nargs="+", default=[], help="Web URLs to ingest")

    ap.add_argument("--county", default="", help="County metadata (e.g., Haywood)")
    ap.add_argument("--topic", action="extend", nargs="+", default=[], help="Topic tags (repeatable)")
    ap.add_argument("--date", default="", help="ISO date for source (e.g., 2025-09-01)")
    ap.add_argument("--outdir", default="data/processed", help="Output directory")

    # Tuning knobs
    ap.add_argument("--target", type=int, default=DEFAULT_TARGET, help="Chunk target size (chars)")
    ap.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP, help="Chunk overlap (chars)")
    ap.add_argument("--max-doc-chars", type=int, default=DEFAULT_MAX_DOC_CHARS, help="Max chars per doc after cleaning")

    args = ap.parse_args()
    iso_date = args.date or None
    county = args.county or None
    topics = args.topic or []

    if not API_KEY:
        raise SystemExit("OPENAI_API_KEY not found. Put it in .env or environment.")

    outdir = Path(args.outdir)
    ensure_dir(outdir)

    documents: List[Dict] = []

    # PDFs
    for p in args.pdf:
        path = Path(p)
        if not path.exists():
            print(f"[WARN] missing PDF: {path}")
            continue
        print(f"[READ] PDF {path.name}", flush=True)
        text = load_pdf(path)
        documents.append({
            "id": f"pdf::{path.name}",
            "title": path.stem,
            "url": file_uri(path),
            "date": iso_date,
            "county": county,
            "topics": topics,
            "text": text,
            "source_type": "pdf",
        })

    # DOCX
    for p in args.docx:
        path = Path(p)
        if not path.exists():
            print(f"[WARN] missing DOCX: {path}")
            continue
        print(f"[READ] DOCX {path.name}", flush=True)
        text = load_docx(path)
        documents.append({
            "id": f"docx::{path.name}",
            "title": path.stem,
            "url": file_uri(path),
            "date": iso_date,
            "county": county,
            "topics": topics,
            "text": text,
            "source_type": "docx",
        })

    # URLs
    for u in args.url:
        try:
            print(f"[FETCH] {u}", flush=True)
            obj = load_url(u, args.max_doc_chars)
            print(f"[OK] {u} ({len(obj['text'])} chars)", flush=True)
        except Exception as e:
            print(f"[WARN] fetch failed for {u}: {e}")
            continue
        doc_id = "doc::" + hashlib.sha1(u.encode()).hexdigest()[:10]
        documents.append({
            "id": doc_id,
            "title": obj["title"],
            "url": u,
            "date": iso_date,
            "county": county,
            "topics": topics,
            "text": obj["text"],
            "source_type": "url",
        })

    if not documents:
        print("No sources provided. Nothing to ingest.")
        return

    # Chunk documents
    chunks: List[Dict] = []
    for doc in documents:
        print(f"[CHUNK] {doc.get('title') or doc.get('url') or 'Document'} ({len(doc['text'])} chars)...", flush=True)
        try:
            parts = chunk_text(doc["text"], target=args.target, overlap=args.overlap, max_doc_chars=args.max_doc_chars)
        except MemoryError:
            print("[CHUNK][WARN] MemoryError; retrying with smaller slices and zero overlap")
            parts = chunk_text(doc["text"], target=max(512, args.target // 2), overlap=0, max_doc_chars=args.max_doc_chars)
        print(f"[CHUNK] -> {len(parts)} chunks", flush=True)
        for i, tx in enumerate(parts):
            chunks.append({
                "doc_id": doc["id"],
                "chunk_id": f"{doc['id']}::chunk{i}",
                "title": doc["title"],
                "url": doc["url"],
                "date": doc["date"],
                "county": doc["county"],
                "topics": doc["topics"],
                "text": tx,
            })
        # free per-doc intermediates early
        del parts
        gc.collect()

    print(f"Ingested {len(documents)} documents → {len(chunks)} chunks")

    # Embeddings (batched)
    texts = [c["text"] for c in chunks]
    vecs = embed_texts(texts, model=MODEL_EMB, batch=96)
    if vecs.shape[0] != len(chunks):
        raise RuntimeError("Embedding count mismatch")

    # Save metadata JSONL
    chunks_path = outdir / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # Save vectors as .npy (already normalized)
    vecs_path = outdir / "embeddings.npy"
    np.save(vecs_path.as_posix(), vecs)

    # Optional FAISS index
    faiss_path = outdir / "index.faiss"
    if faiss:
        dim = int(vecs.shape[1])
        index = faiss.IndexFlatIP(dim)
        index.add(vecs)
        faiss.write_index(index, faiss_path.as_posix())
        print(f"[OK] wrote FAISS index: {faiss_path}")
    else:
        print("[INFO] faiss not installed; using numpy fallback at runtime.")

    # Manifest
    manifest = {
        "created": int(time.time()),
        "chunks_file": chunks_path.name,
        "embeddings_file": vecs_path.name,
        "faiss_file": faiss_path.name if faiss else None,
        "count": int(vecs.shape[0]),
        "dim": int(vecs.shape[1]),
        "embed_model": MODEL_EMB,
        "target": args.target,
        "overlap": args.overlap,
        "max_doc_chars": args.max_doc_chars,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"[DONE] Saved to: {outdir.as_posix()}")
    print(f"       Chunks: {chunks_path.name} | Embeddings: {vecs_path.name}")


if __name__ == "__main__":
    main()
