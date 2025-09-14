# WNC Proposal Assistant (PoC)

A one-week PoC that turns a short campaign brief + org facts into a polished donor email and a 1–2 page grant-style narrative, grounded in local citations via lightweight RAG.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # add your OPENAI_API_KEY
make seed             # create minimal context.jsonl
make api              # starts FastAPI on :8000
make ui               # starts Streamlit on :8501
```

Open http://localhost:8501

## Layout
- `app/` FastAPI service (retrieval + generation + export stubs)
- `ui/` Streamlit MVP
- `data/processed/context.jsonl` lightweight knowledge base (citations)
- `scripts/seed_minimal.py` writes a starter `context.jsonl`

## Notes
- This PoC computes embeddings at request-time for simplicity. For larger corpora, swap to a persistent vector store (Chroma/FAISS).
- Do not put PII into `context.jsonl`. Keep sources and dates for transparency.
