import os, textwrap, json, re
from typing import List, Dict, Any, Tuple
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI
from app.services.citations import build_sources, insert_markers_from_sequence
from app.services.postprocess import rebuild_sources_in_marker_order, sanitize_on_no_sources

MODEL = os.environ.get("MODEL", "gpt-4o-mini")
client = OpenAI()

# ----------------- helpers -----------------

def _parse_iso_or_none(s: str | None):
    try:
        return datetime.fromisoformat(s) if s else None
    except Exception:
        return None

def _ctx_matches_filters(c: Dict[str, Any], f: Dict[str, Any]) -> bool:
    if not f:
        return True

    # counties
    if f.get("counties"):
        cty = (c.get("county") or "").strip()
        if not cty or cty not in f["counties"]:
            return False

    # topics (any overlap)
    if f.get("topics"):
        mt = c.get("topics") or []
        if not any(t in mt for t in f["topics"]):
            return False

    # strict date window: undated chunks FAIL when a date filter is present
    df = f.get("date_from")
    dt = f.get("date_to")
    if df or dt:
        d  = _parse_iso_or_none(c.get("date"))
        d1 = _parse_iso_or_none(df) if df else None
        d2 = _parse_iso_or_none(dt) if dt else None
        if d is None:
            return False
        if d1 and d < d1:
            return False
        if d2 and d > d2:
            return False

    return True

def _filter_ctx(ctx: List[Dict[str, Any]], retrieve_filters: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not retrieve_filters:
        return ctx
    return [c for c in ctx if _ctx_matches_filters(c, retrieve_filters)]

def _norm_rf(rf: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    Normalize either schema to {date_from,date_to,counties,topics}.
    Accepts:
      - {"date":{"from","to"},"county":[...],"topic":[...]}
      - {"date_from","date_to","counties","topics"}
    """
    out = {"date_from": None, "date_to": None, "counties": None, "topics": None}
    if not rf:
        return out
    rf = dict(rf)
    if "date" in rf and isinstance(rf["date"], dict):
        out["date_from"] = rf["date"].get("from")
        out["date_to"]   = rf["date"].get("to")
    else:
        out["date_from"] = rf.get("date_from")
        out["date_to"]   = rf.get("date_to")
    out["counties"] = rf.get("counties") or rf.get("county")
    out["topics"]   = rf.get("topics") or rf.get("topic")
    return out

def _chat(messages, json_mode: bool = False):
    kwargs = {"model": MODEL, "messages": messages, "temperature": 0.4}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        return client.chat.completions.create(**kwargs).choices[0].message.content
    except Exception:
        if json_mode:
            return client.chat.completions.create(model=MODEL, messages=messages, temperature=0.4).choices[0].message.content
        raise

_URL_RE = re.compile(r"(https?://[^\s)>\]]+)", re.IGNORECASE)

def _sanitize_preserve_urls(md: str) -> str:
    if not md:
        return md
    urls = []
    def _stash(m):
        urls.append(m.group(1))
        return f"__URL_{len(urls)-1}__"
    md = _URL_RE.sub(_stash, md)
    md = md.replace("\u00A0", " ")
    for i, u in enumerate(urls):
        md = md.replace(f"__URL_{i}__", u)
    return md

def _strip_model_sources(md: str) -> str:
    if not md:
        return md
    md = re.sub(r"(?is)\n+#+\s*(sources|references)\b.*$", "", md)
    md = re.sub(r"(?im)^\s*(sources|references)\s*:\s*$.*", "", md)
    return md

def _paragraph_blocks(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

SYSTEM_PROMPT_BASE = """
You are a nonprofit fundraising writer serving Western North Carolina disaster recovery.
Ground every factual claim in the provided context; if the context lacks a fact, be transparent rather than inventing numbers.
Write clearly, respectfully, and avoid making promises. Use a community-first tone.
When you cite facts, use bracketed numeric markers like [1], [2], then include a 'Sources' list with titles and links.
Do not insert hard line breaks inside a sentence or a number.
"""

EMAIL_SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + """
Return ONLY valid JSON with keys:
- subjects (array of exactly 3 short strings)
- body_md (string; 150–220 words, includes [n] markers; **do not include a P.S. here**)
- ps (string; one sentence; **do not prefix with 'P.S.'**)
- sources (array of {label,url})
No extra prose, no markdown fences.
"""

NARRATIVE_SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + """
Output ONLY plain Markdown (no JSON). Do not use headings (#, ##, ###!).
Write six paragraphs in this exact order, each starting with a bold label followed by a period:

**Need/Problem.** …
**Program/Intervention.** …
**Budget Summary & Unit Economics.** …
**Outcomes & Reporting Plan.** …
**Equity & Community Context.** …
**Organizational Capacity.** …

End with a 'Sources' list that maps [n] → title and URL.
Keep normal paragraph wrapping; never insert a hard line break inside a sentence or a number.
"""

def _sanitize_markdown(md: str) -> str:
    s = md.replace("\r\n", "\n")
    s = s.replace("\u00A0", " ")
    s = re.sub(r"[\u2000-\u200A\u202F\u205F\u3000]", " ", s)
    s = re.sub(r"[\u200B-\u200D\u2060\uFEFF]", "", s)
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\$\s+(?=\d)", "$", s)
    s = re.sub(r"(?<=\d),(?:\s|\n)+(?=\d)", ",", s)
    s = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", s)
    s = re.sub(
        r"(?i)\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kmb])\b",
        lambda m: f"${m.group(1)}{m.group(2).lower()}"
        if m.group(0).lstrip().startswith("$")
        else f"{m.group(1)}{m.group(2).lower()}",
        s,
    )
    s = re.sub(r"(?<=\d),(?=\d{4}\b)", ", ", s)
    s = re.sub(r"(?i)(\d[\d,]*(?:\.\d+)?[kmb])(?=to\b)", r"\1 ", s)
    s = re.sub(r"(\w)-\n(\w)", r"\1\2", s)
    s = re.sub(r"(?<=\w)\n(?=\w)", " ", s)
    s = re.sub(r"([^\n])\n(?!\n|[#\-\*]|$)", r"\1 ", s)
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",(?!\s|\d)", ", ", s)
    s = re.sub(r"([.;:!?])(?=\S)", r"\1 ", s)
    s = re.sub(r"(\])(?=\w)", r"\1 ", s)
    s = re.sub(r"(?<=\d)(?=[A-Za-z])(?!k\b|m\b|b\b|st\b|nd\b|rd\b|th\b)", " ", s)
    s = re.sub(r'\*\*(.+?)\.\s*\*\*', r'**\1.**', s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return "\n".join(_fix_url_line(ln) for ln in s.splitlines()).strip()

def _fix_url_line(line: str) -> str:
    if "http" not in line:
        return line
    line = re.sub(r'\b(https?)\s*:\s*/\s*/', r'\1://', line, flags=re.I)
    line = re.sub(r'(?i)(https?://)\s+', r'\1', line)
    for _ in range(3):
        line = re.sub(r'(?i)(https?://[^\n]*?)\s*\.\s*', r'\1.', line)
        line = re.sub(r'(?i)(https?://[^\n]*?)\s*/\s*', r'\1/', line)
        line = re.sub(r'(?i)(https?://[^\n]*?)\s*([?#&=])\s*', r'\1\2', line)
    return line

def _sanitize_inline_text(x: str) -> str:
    if not x:
        return x
    s = _sanitize_markdown(x)
    s = re.sub(r"\s*\n\s*", " ", s)
    return s.strip()

def _format_context_blocks(ctx: List[Dict[str, Any]]) -> str:
    blocks = []
    for i, d in enumerate(ctx, start=1):
        title = d.get("title") or d.get("source", "") or f"Source {i}"
        date = d.get("date") or ""
        snippet = d.get("text", "")[:600]
        blocks.append(f"[{i}] {title} ({date})\n{snippet}\nURL: {d.get('source','')}")
    return "\n\n".join(blocks)

def finalize_output(body_md: str, sources: list[dict]) -> tuple[str, list[dict]]:
    body_md, sources = rebuild_sources_in_marker_order(body_md, sources)
    return body_md, sources

# ----------------- email -----------------

def generate_email(payload: Dict[str, Any], ctx: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Defensive: re-filter ctx (supports either schema from UI/API)
    rf_raw = payload.get("retrieve_filters") or {}
    RF = _norm_rf(rf_raw)
    if any([RF["date_from"], RF["date_to"], RF["counties"], RF["topics"]]):
        ctx2 = _filter_ctx(ctx, RF)
        if len(ctx2) == 0:
            ctx = []
            context_blocks = "No context available."
        else:
            ctx = ctx2
            context_blocks = _format_context_blocks(ctx)
    else:
        context_blocks = _format_context_blocks(ctx) if ctx else "No context available."

    print(f"[DBG] gen_email rf={RF} len(ctx)_after_refilter={len(ctx)}")

    # sanitize inbound user fields before building the prompt
    payload = {
        **payload,
        "org_brief": _sanitize_inline_text(payload.get("org_brief", "")),
        "campaign_brief": _sanitize_inline_text(payload.get("campaign_brief", "")),
        "ask": _sanitize_inline_text(payload.get("ask", "")),
        "deadline": _sanitize_inline_text(payload.get("deadline", "")),
    }
    user_prompt = f"""
    Return ONLY valid JSON with these keys:
    - subjects: list of exactly 3 concise subject lines
    - body_md: the email body (150–220 words)
    - ps: a one-sentence P.S. with a concrete next step

    Audience: {payload.get('audience')}
    Tone: {payload.get('tone')}
    Ask amount or range: {payload.get('ask')}
    Deadline/urgency note: {payload.get('deadline')}

    ORG BRIEF
    ---
    {payload.get('org_brief')}

    CAMPAIGN BRIEF
    ---
    {payload.get('campaign_brief')}

    RETRIEVED CONTEXT
    ---
    {context_blocks}
    """
    if not ctx:
        user_prompt += "\n\nNo citations available. Do not include bracketed citations or a Sources section."

    messages = [
        {"role": "system", "content": EMAIL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw = _chat(messages, json_mode=True)  # try JSON mode first

    # Parse JSON or fall back gracefully
    subjects, ps_raw, sources = [], "", []
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{[\s\S]*\}$", raw.strip())
        obj = json.loads(m.group(0)) if m else {}
    if not obj:
        lines = [ln.strip("-• ").strip() for ln in raw.splitlines() if ln.strip()]
        subjects = lines[:3]
        body_raw = "\n".join(lines[3:]) if len(lines) > 3 else raw
    else:
        subjects = (obj.get("subjects") or [])[:3]
        ps_raw = (obj.get("ps") or "").strip()
        sources = obj.get("sources") or []
        body_raw = obj.get("body_md") or ""

    # ---- SANITIZE (preserve URLs), strip model-written Sources ----
    body = _sanitize_preserve_urls(body_raw)
    body = _strip_model_sources(body)

    # ---- Build document map + sources from ctx ----
    doc_to_n, sources_built = build_sources(ctx)

    # ---- If we have sources, insert markers; otherwise skip safely ----
    if sources_built:
        paras = _paragraph_blocks(body)
        blocks: List[Tuple[str, Dict[str, Any]]] = []
        for i, p in enumerate(paras):
            ch = ctx[min(i, len(ctx)-1)] if ctx else {}
            blocks.append((p, ch))
        body = insert_markers_from_sequence(blocks, doc_to_n)

    # ---- DEDUPE / append P.S. exactly once ----
    ps = re.sub(r'^\s*P\.?\s*S\.?\s*:?\s*', '', ps_raw, flags=re.I).strip()
    if ps and not re.search(r'^\s*P\.?\s*S\.?\s*[:.]', body, flags=re.I | re.M):
        body = f"{body}\n\nP.S. {ps}"

    # ---- Grounded citations ----
    citations = sources_built if sources_built else []

    # ---- Strong guard: if no context (or no sources), strip stray [n] and trailing Sources ----
    print(f"[DBG] gen_email: len(ctx)={len(ctx)} before_sanitize markers?={bool(re.search(r'\\[\\d+\\]', body))}")
    if not ctx or not citations:
        body, citations = sanitize_on_no_sources(body, [])
    print(f"[DBG] gen_email: after_sanitize markers?={bool(re.search(r'\\[\\d+\\]', body))} len(citations)={len(citations)}")

    # ---- Finalize once: renumber [n] and reorder sources ----
    body, citations = finalize_output(body, citations)

    print(f"[DBG] gen_email: len(ctx)={len(ctx)}  before_sanitize markers?={bool(re.search(r'\\[\\d+\\]', body))}")

    return {"subjects": subjects, "body_md": body, "citations": citations}

# ----------------- narrative -----------------

def generate_narrative(payload: Dict[str, Any], ctx: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Defensive: re-filter ctx (supports either schema from UI/API)
    rf_raw = payload.get("retrieve_filters") or {}
    RF = _norm_rf(rf_raw)
    if any([RF["date_from"], RF["date_to"], RF["counties"], RF["topics"]]):
        ctx2 = _filter_ctx(ctx, RF)
        if len(ctx2) == 0:
            ctx = []
            context_blocks = "No context available."
        else:
            ctx = ctx2
            context_blocks = _format_context_blocks(ctx)
    else:
        context_blocks = _format_context_blocks(ctx) if ctx else "No context available."

    print(f"[DBG] gen_narr rf={RF} len(ctx)_after_refilter={len(ctx)}")

    payload = {
        **payload,
        "org_brief": _sanitize_inline_text(payload.get("org_brief", "")),
        "campaign_brief": _sanitize_inline_text(payload.get("campaign_brief", "")),
        "ask": _sanitize_inline_text(payload.get("ask", "")),
        "deadline": _sanitize_inline_text(payload.get("deadline", "")),
    }
    user_prompt = f"""
    Write a grant-style narrative (350–650 words) with the exact section headings specified in the system prompt.
    Ground your writing in the retrieved context. Do not add a sources section.

    ORG BRIEF
    ---
    {payload.get('org_brief')}

    CAMPAIGN BRIEF
    ---
    {payload.get('campaign_brief')}

    RETRIEVED CONTEXT
    ---
    {context_blocks}
    """
    if not ctx:
        user_prompt += "\n\nNo citations available. Do not include bracketed citations or a Sources section."

    messages = [
        {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.4)
    content = resp.choices[0].message.content

    # If model accidentally returns JSON, unwrap it
    if content.strip().startswith("{"):
        try:
            obj = json.loads(content)
            content = obj.get("body_md", content)
        except Exception:
            pass

    # ---- SANITIZE (preserve URLs), strip model-written Sources ----
    content = _sanitize_preserve_urls(content)
    content = _strip_model_sources(content)

    # ---- Build document map + sources from ctx ----
    doc_to_n, sources_built = build_sources(ctx)

    # ---- If we have sources, insert markers; otherwise skip safely ----
    if sources_built:
        paras = _paragraph_blocks(content)
        blocks: List[Tuple[str, Dict[str, Any]]] = []
        for i, p in enumerate(paras):
            ch = ctx[min(i, len(ctx)-1)] if ctx else {}
            blocks.append((p, ch))
        content = insert_markers_from_sequence(blocks, doc_to_n)

    # ---- Grounded citations ----
    citations = sources_built if sources_built else []

    # ---- Strong guard: if no context (or no sources), strip stray [n] and trailing Sources ----
    print(f"[DBG] gen_narr: len(ctx)={len(ctx)} before_sanitize markers?={bool(re.search(r'\\[\\d+\\]', content))}")
    if not ctx or not citations:
        content, citations = sanitize_on_no_sources(content, [])
    print(f"[DBG] gen_narr: after_sanitize markers?={bool(re.search(r'\\[\\d+\\]', content))} len(citations)={len(citations)}")

    # ---- Finalize once: renumber [n] and reorder sources ----
    content, citations = finalize_output(content, citations)
    print(f"[DBG] gen_narr: len(ctx)={len(ctx)}  before_sanitize markers?={bool(re.search(r'\\[\\d+\\]', content))}")

    return {"body_md": content, "citations": citations}