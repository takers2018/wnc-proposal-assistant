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
    for i, ch in enumerate(chunks, 1):
        title = (ch.get('title')
                 or ch.get('meta', {}).get('title')
                 or 'Source')
        url = (ch.get('url')
               or ch.get('meta', {}).get('url')
               or '')
        print(f"{i:>2}. {title}  —  {url}")

if __name__ == "__main__":
    main()
