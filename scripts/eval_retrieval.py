# scripts/eval_retrieval.py
#!/usr/bin/env python
import argparse
from app.services import retriever as rtr

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--topics", nargs="*", default=None)
    ap.add_argument("--counties", nargs="*", default=None)
    ap.add_argument("--date-from", dest="date_from", default=None)
    ap.add_argument("--date-to", dest="date_to", default=None)
    args = ap.parse_args()

    filters = {
        "topics": args.topics,
        "counties": args.counties,
        "date_from": args.date_from,
        "date_to": args.date_to,
    }
    filters = {k: v for k, v in filters.items() if v}

    chunks = rtr.retrieve(args.query, k=args.k, filters=filters)
    print(f"Top-{args.k} for: {args.query}")
    seen = set()
    rank = 1
    for ch in chunks:
        doc_id = ch.get("doc_id") or f"{ch.get('source','')}|{ch.get('title','Source')}"
        if doc_id in seen:
            continue
        seen.add(doc_id)
        title = ch.get("title") or "Source"
        url = ch.get("source") or ""
        print(f"{rank:>2}. {title}  —  {url}")
        rank += 1


if __name__ == "__main__":
    main()
