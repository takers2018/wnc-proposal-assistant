# app/services/citations.py
from typing import Dict, List, Tuple, Any

Source = Dict[str, Any]
Chunk = Dict[str, Any]

def _get(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def build_sources(chunks: List[Chunk]) -> Tuple[Dict[str, int], List[Source]]:
    """
    Dedup by document. Assigns the first time we see a doc_id -> [n].
    Returns:
      map_doc_to_n: {'doc_id': n}
      sources: [{'n', 'title', 'url', 'date', 'county', 'topics'}] in first-seen order
    """
    by_doc: Dict[str, int] = {}
    ordered: List[Source] = []
    n = 1
    for ch in chunks:
        doc = ch.get("doc_id") or _get(ch, "meta", "doc_id")
        if not doc:
            # Fall back to URL+title combo if doc_id missing
            doc = f"{_get(ch,'meta','url','') or ch.get('url','')}|{_get(ch,'meta','title','') or ch.get('title','Source')}"
        if doc not in by_doc:
            by_doc[doc] = n
            ordered.append({
                "n": n,
                "title": _get(ch, "meta", "title") or ch.get("title", "Source"),
                "url": _get(ch, "meta", "url") or ch.get("url", ""),
                "date": _get(ch, "meta", "date") or ch.get("date", ""),
                "county": _get(ch, "meta", "county") or ch.get("county", ""),
                "topics": _get(ch, "meta", "topics") or ch.get("topics", []),
            })
            n += 1
    return by_doc, ordered

def insert_markers_from_sequence(
    text_blocks: List[Tuple[str, Chunk]],
    doc_to_n: Dict[str, int],
) -> str:
    """
    Given [(text, chunk), ...], append [n] after each block.
    Dedupe adjacent markers if they reference the same doc consecutively.
    """
    out = []
    prev_n = None
    for txt, ch in text_blocks:
        doc = ch.get("doc_id") or ch.get("meta", {}).get("doc_id")
        if not doc:
            doc = f"{ch.get('meta', {}).get('url','') or ch.get('url','')}|{ch.get('meta', {}).get('title','') or ch.get('title','Source')}"
        n = doc_to_n[doc]
        # dedupe adjacent markers from the same source
        marker = "" if prev_n == n else f" [{n}]"
        out.append(f"{txt}{marker}")
        prev_n = n
    return "\n\n".join(out)
