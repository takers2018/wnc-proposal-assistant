# scripts/ingest.py
import os, re, json, argparse, time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse

import numpy as np
from dotenv import load_dotenv, dotenv_values
from openai import OpenAI

# Optional deps: we gracefully degrade if they're missing
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

# Optional FAISS
try:
    import faiss  # type: ignore
except Exception:
    faiss = None

# --- Config / OpenAI client ---
load_dotenv(override=True)
_cfg = dotenv_values()  # force reading .env
API_KEY = _cfg.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
MODEL_EMB = _cfg.get("EMBED_MODEL") or os.getenv("EMBED_MODEL", "text-embedding-3-small")
client = OpenAI(api_key=API_KEY)

# Safety cap for monster pages (post-clean)
MAX_DOC_CHARS = 200_000  # ~200 KB of text

# --- Utils ---
def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def clean_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\u00A0", " ").strip()
    # collapse excessive whitespace
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def chunk_text(text: str, target: int = 1200, overlap: int = 200) -> List[str]:
    """
    Fast char-based chunking:
    - aim for ~target chars
    - prefer newline/space boundaries near the end of the window
    - guaranteed forward progress (no regex on reversed slices)
    """
    text = text.strip()
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS]

    n = len(text)
    if n == 0:
        return []

    chunks: List[str] = []
    i = 0
    while i < n:
        end = min(i + target, n)
        cut = end

        # Try a newline boundary in the last ~200 chars of the window
        win_start = max(i, end - 200)
        nl = text.rfind("\n", win_start, end)
        if nl != -1 and nl > i:
            cut = nl
        else:
            # Fall back to last space near the end
            sp = text.rfind(" ", win_start, end)
            if sp != -1 and sp > i:
                cut = sp

        # Ensure progress
        if cut <= i:
            cut = end

        chunk = text[i:cut].strip()
        if chunk:
            chunks.append(chunk)

        if cut >= n:
            break
        i = max(0, cut - overlap)
    return chunks

# --- Loaders ---
def load_pdf(path: Path) -> str:
    if not fitz:
        raise RuntimeError("PyMuPDF not installed. pip install pymupdf")
    doc = fitz.open(path.as_posix())
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    return clean_text("\n".join(parts))

def load_docx(path: Path) -> str:
    if not docx:
        raise RuntimeError("python-docx not installed. pip install python-docx")
    d = docx.Document(path.as_posix())
    return clean_text("\n".join(p.text for p in d.paragraphs if p.text.strip()))

def load_url(url: str) -> Dict[str, str]:
    if not bs4:
        raise RuntimeError("beautifulsoup4/httpx not installed. pip install beautifulsoup4 httpx")

    import httpx
    from bs4 import BeautifulSoup

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

    # Use lxml if available, fall back to html.parser
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title = (soup.title.string.strip() if soup.title and soup.title.string else urlparse(url).netloc)

    # Simple readability: headings + paragraphs + list items
    texts = []
    for tag in soup.select("h1,h2,h3,p,li"):
        t = tag.get_text(" ", strip=True)
        if t and len(t) > 3:
            texts.append(t)

    body = clean_text("\n".join(texts))

    # Safety cap (same value as in chunk_text)
    if len(body) > MAX_DOC_CHARS:
        body = body[:MAX_DOC_CHARS]

    return {"title": title, "text": body}

# --- Embeddings ---
def embed_texts(texts: List[str], batch=96) -> np.ndarray:
    vecs = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i+batch]
        print(f"[EMB] {i+1}-{i+len(chunk)} / {len(texts)}", flush=True)
        resp = client.embeddings.create(model=MODEL_EMB, input=chunk)
        arr = np.array([item.embedding for item in resp.data], dtype=np.float32)
        vecs.append(arr)
    X = np.vstack(vecs)
    # L2-normalize for cosine/IP
    norms = np.linalg.norm(X, axis=1, keepdims=True) + 1e-12
    return X / norms

# --- Main ingest ---
def main():
    ap = argparse.ArgumentParser(description="Ingest files/urls -> chunks -> embeddings -> index")

    # Use 'extend' so repeated flags accumulate; supports:
    #   --url A B   and   --url A --url B
    ap.add_argument("--pdf",  action="extend", nargs="+", default=[], help="Paths to PDF files")
    ap.add_argument("--docx", action="extend", nargs="+", default=[], help="Paths to DOCX files")
    ap.add_argument("--url",  action="extend", nargs="+", default=[], help="Web URLs to ingest")

    ap.add_argument("--county", default="", help="County metadata (e.g., Haywood)")
    ap.add_argument("--topic", action="extend", nargs="+", default=[], help="Topic tags (repeatable)")
    ap.add_argument("--date",  default="", help="ISO date for source (e.g., 2025-09-01)")
    ap.add_argument("--outdir", default="data/processed", help="Output directory")
    args = ap.parse_args()

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
            "url": path.as_uri(),
            "date": args.date,
            "county": args.county,
            "topics": args.topic,
            "text": text,
            "source_type": "pdf"
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
            "url": path.as_uri(),
            "date": args.date,
            "county": args.county,
            "topics": args.topic,
            "text": text,
            "source_type": "docx"
        })

    # URLs
    for u in args.url:
        try:
            print(f"[FETCH] {u}", flush=True)
            obj = load_url(u)
            print(f"[OK] {u} ({len(obj['text'])} chars)", flush=True)
        except Exception as e:
            print(f"[WARN] fetch failed for {u}: {e}")
            continue
        documents.append({
            "id": f"url::{u}",
            "title": obj["title"],
            "url": u,
            "date": args.date,
            "county": args.county,
            "topics": args.topic,
            "text": obj["text"],
            "source_type": "url"
        })

    if not documents:
        print("No sources provided. Nothing to ingest.")
        return

    # Chunk and collect
    chunks: List[Dict] = []
    for doc in documents:
        print(f"[CHUNK] {doc.get('title') or doc.get('url') or 'Document'} "
              f"({len(doc['text'])} chars)...", flush=True)
        parts = chunk_text(doc["text"])
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

    print(f"Ingested {len(documents)} documents → {len(chunks)} chunks")

    # Embed
    texts = [c["text"] for c in chunks]
    vecs = embed_texts(texts)
    assert vecs.shape[0] == len(chunks), "embedding shape mismatch"

    # Save metadata
    chunks_path = outdir / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # Save vectors (normalized)
    vecs_path = outdir / "embeddings.npy"
    np.save(vecs_path.as_posix(), vecs)

    # FAISS index (if available)
    faiss_path = outdir / "index.faiss"
    if faiss:
        dim = vecs.shape[1]
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
        "count": len(chunks),
        "dim": int(vecs.shape[1]),
        "embed_model": MODEL_EMB,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"[DONE] Saved to: {outdir.as_posix()}")
    print(f"       Chunks: {chunks_path.name} | Embeddings: {vecs_path.name}")

if __name__ == "__main__":
    main()
