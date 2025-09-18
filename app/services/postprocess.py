import re
from typing import List, Dict, Tuple

MARKER_RE = re.compile(r"\[(\d+)\]")

def _strip_model_sources_section(md: str) -> str:
    # Drop any trailing "Sources:" section the model may have added on its own.
    return re.sub(r"\n+#+?\s*Sources?:.*\Z", "", md, flags=re.IGNORECASE | re.DOTALL)

def rebuild_sources_in_marker_order(body_md: str, sources: List[Dict]) -> Tuple[str, List[Dict]]:
    body_md = _strip_model_sources_section(body_md)

    # Collect the order in which markers appear (first occurrence wins)
    used = [int(n) for n in MARKER_RE.findall(body_md)]
    order, seen = [], set()
    for n in used:
        if n not in seen:
            order.append(n); seen.add(n)

    # Reorder sources to match first-use order and renumber sequentially from 1..m
    by_n = {s.get("n"): s for s in sources}
    new_sources = []
    for i, old_n in enumerate(order, start=1):
        s = by_n.get(old_n)
        if s:
            s2 = {**s, "n": i}
            new_sources.append(s2)

    # Rewrite [old] -> [new] in the body
    def _renumber(m):
        old = int(m.group(1))
        new = order.index(old) + 1 if old in order else old
        return f"[{new}]"

    body_md = MARKER_RE.sub(_renumber, body_md)
    return body_md, new_sources

def sanitize_on_no_sources(body_md: str, sources: List[Dict]) -> Tuple[str, List[Dict]]:
    """
    If there are no sources, strip any stray [n] markers and any trailing 'Sources/References' section.
    """
    if sources and len(sources) > 0:
        return body_md, sources

    # remove bracket citations like [1], [12] (including optional leading whitespace)
    body_md = re.sub(r"\s*\[\d+\]", "", body_md)

    # remove a trailing Sources/References section (Markdown headers or plain)
    body_md = re.sub(
        r"\n+#{0,2}\s*(Sources|References)\s*:?.*$",
        "",
        body_md,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return body_md, []
