import os, textwrap, json, re
from dotenv import load_dotenv
load_dotenv()
from typing import List, Dict, Any, Tuple
from openai import OpenAI
from app.services.citations import build_sources, insert_markers_from_sequence

MODEL = os.environ.get("MODEL", "gpt-4o-mini")
client = OpenAI()

def _chat(messages, json_mode: bool = False):
    kwargs = {"model": MODEL, "messages": messages, "temperature": 0.4}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        return client.chat.completions.create(**kwargs).choices[0].message.content
    except Exception:
        # Retry once without JSON mode (models/SDKs that don't support response_format)
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

    # ✅ Keep this section minimal; do NOT insert spaces around '.' or inside tokens.
    # If you previously added any rules like "add spaces around dots", delete them.
    # Example of safe normalizations:
    md = md.replace("\u00A0", " ")

    for i, u in enumerate(urls):
        md = md.replace(f"__URL_{i}__", u)
    return md

def _strip_model_sources(md: str) -> str:
    """Remove any model-written 'Sources' / 'References' sections."""
    if not md:
        return md
    # Kill trailing 'Sources' headings and content
    md = re.sub(r"(?is)\n+#+\s*(sources|references)\b.*$", "", md)
    # Kill standalone lines like "Sources:" followed by anything
    md = re.sub(r"(?im)^\s*(sources|references)\s*:\s*$.*", "", md)
    return md

def _paragraph_blocks(text: str) -> List[str]:
    """Split by blank lines, keep non-empty paragraphs."""
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
Output ONLY plain Markdown (no JSON). Do not use headings (#, ##, ###).
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

def _fix_url_line(line: str) -> str:
    if "http" not in line:
        return line

    # 1) Collapse 'https: //' -> 'https://'
    line = re.sub(r'\b(https?)\s*:\s*/\s*/', r'\1://', line, flags=re.I)

    # 2) Remove spaces immediately after protocol: 'https://  example' -> 'https://example'
    line = re.sub(r'(?i)(https?://)\s+', r'\1', line)

    # 3) Iteratively remove spaces around dots, slashes, and query separators
    #    anywhere AFTER the protocol (handles 'example. org', 'path / to', '? q=foo')
    for _ in range(3):  # a few passes to catch cascades
        line = re.sub(r'(?i)(https?://[^\n]*?)\s*\.\s*', r'\1.', line)
        line = re.sub(r'(?i)(https?://[^\n]*?)\s*/\s*', r'\1/', line)
        line = re.sub(r'(?i)(https?://[^\n]*?)\s*([?#&=])\s*', r'\1\2', line)

    return line

def _sanitize_markdown(md: str) -> str:
    s = md.replace("\r\n", "\n")

    # --- Normalize all funky spaces first (incl. NBSP) ---
    s = s.replace("\u00A0", " ")  # NBSP
    s = re.sub(r"[\u2000-\u200A\u202F\u205F\u3000]", " ", s)  # thin/figure/narrow/ideographic, etc.
    # Strip zero-width chars
    s = re.sub(r"[\u200B-\u200D\u2060\uFEFF]", "", s)

    # Normalize dashes to hyphen
    s = s.replace("–", "-").replace("—", "-")

    # Remove space after $ before digits: "$ 250,000" -> "$250,000"
    s = re.sub(r"\$\s+(?=\d)", "$", s)

    # Fix '**Label. **' -> '**Label.**'
    s = re.sub(r'^\s*\*\*(.+?)\.\s*\*\*\s*$', r'**\1.**', s, flags=re.MULTILINE)

    # --- Numeric & money formatting ---
    # 250,\n000 or 6, 000 -> 250,000 / 6,000
    s = re.sub(r"(?<=\d),(?:\s|\n)+(?=\d)", ",", s)
    # Tighten numeric ranges like 6 - 10 -> 6-10
    s = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", s)
    # Tighten unit "k/m/b" with or without $: "10 k" -> "10k", "$ 250 k" -> "$250k"
    s = re.sub(
        r"(?i)\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kmb])\b",
        lambda m: f"${m.group(1)}{m.group(2).lower()}"
        if m.group(0).lstrip().startswith("$")
        else f"{m.group(1)}{m.group(2).lower()}",
        s,
    )
    # Add space after comma before a 4-digit year: "October 15,2025" -> "October 15, 2025"
    s = re.sub(r"(?<=\d),(?=\d{4}\b)", ", ", s)
    # If "10kto" appears, ensure a space before "to"
    s = re.sub(r"(?i)(\d[\d,]*(?:\.\d+)?[kmb])(?=to\b)", r"\1 ", s)

    # Words split by linebreaks / hyphenation
    s = re.sub(r"(\w)-\n(\w)", r"\1\2", s)          # micro-\n grants -> microgrants
    s = re.sub(r"(?<=\w)\n(?=\w)", " ", s)          # micro\ngrants -> micro grants
    s = re.sub(r"([^\n])\n(?!\n|[#\-\*]|$)", r"\1 ", s)  # single newline inside paragraph -> space

    # Spacing around punctuation/citations
    s = re.sub(r"\s+,", ",", s)                               # " ," -> ","
    s = re.sub(r",(?!\s|\d)", ", ", s)                        # add space after comma only if next isn't space or digit
    s = re.sub(r"([.;:!?])(?=\S)", r"\1 ", s)                 # add space after . ; : ! ? when glued
    s = re.sub(r"(\])(?=\w)", r"\1 ", s)                      # "]Your" -> "] Your"

    # Digit→letter glue (avoid splitting units and ordinals)
    s = re.sub(r"(?<=\d)(?=[A-Za-z])(?!k\b|m\b|b\b|st\b|nd\b|rd\b|th\b)", " ", s)

    # Fix '**Label. **' (with an extra space) -> '**Label.**'
    s = re.sub(r'\*\*(.+?)\.\s*\*\*', r'**\1.**', s)

    # (Intentionally NOT splitting letter→digit to avoid breaking H2O/COVID-19/etc.)

    # Collapse extras
    s = re.sub(r"[ \t]{2,}", " ", s)

    # Normalize any URLs that may contain spaces (in Sources lines, etc.)
    s = "\n".join(_fix_url_line(ln) for ln in s.splitlines())

    return s.strip()

def _sanitize_narrative_markdown(md: str) -> str:
    s = _sanitize_markdown(md)

    # Enforce six inline bold labels (no big headings, no stray asterisks)
    labels = [
        "Need/Problem",
        "Program/Intervention",
        "Budget Summary & Unit Economics",
        "Outcomes & Reporting Plan",
        "Equity & Community Context",
        "Organizational Capacity",
    ]

    # 1) Convert heading or label lines into "**Label.** " at start of a paragraph
    for lab in labels:
        # Demote headings like "### Need/Problem"
        s = re.sub(rf"(?m)^\s*#{1,6}\s*{re.escape(lab)}\s*$", f"**{lab}.** ", s)
        # Convert plain label lines like "Need/Problem:" or "Need/Problem."
        s = re.sub(rf"(?m)^\s*{re.escape(lab)}\s*[:\.]\s*$", f"**{lab}.** ", s)
        # Normalize any "**Label. **" or "** Label **" variants
        s = re.sub(rf"(?m)^\s*\*\*\s*{re.escape(lab)}\s*[\.:]?\s*\*\*\s*$", f"**{lab}.** ", s)

    # 2) If a label ended up on its own line, join it with the next line as one paragraph
    s = re.sub(r"(?m)^\s*(\*\*[^*]+?\.\*\*)\s*\n(?!\n)", r"\1 ", s)

    return s.strip()

def _sanitize_inline_text(x: str) -> str:
    """Pre-clean short user inputs so odd Unicode/linebreaks don't propagate into prompts or outputs."""
    if not x:
        return x
    s = _sanitize_markdown(x)
    # Force single-line to avoid accidental headings/blocks from user pastes
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

def generate_email(payload: Dict[str, Any], ctx: List[Dict[str, Any]]) -> Dict[str, Any]:
    context_blocks = _format_context_blocks(ctx) if ctx else "No context available."
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
    messages = [
        {"role": "system", "content": EMAIL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw = _chat(messages, json_mode=True)  # try JSON mode first

    # Parse JSON or fall back gracefully
    subjects, ps, sources = [], "", []
    try:
        obj = json.loads(raw)
    except Exception:
        # Try to salvage a JSON object if wrapped in prose
        m = re.search(r"\{[\s\S]*\}$", raw.strip())
        obj = json.loads(m.group(0)) if m else {}

    if not obj:
        # Last-resort heuristic: split lines; take first 3 as subjects, rest as body
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

    # ---- Pair paragraphs to chunks and insert [n] markers ----
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

    # ---- Grounded citations (override any model sources) ----
    citations = sources_built

    return {
        "subjects": subjects,
        "body_md": body,
        "citations": citations,
    }

def generate_narrative(payload: Dict[str, Any], ctx: List[Dict[str, Any]]) -> Dict[str, Any]:
    context_blocks = _format_context_blocks(ctx) if ctx else "No context available."
        # sanitize inbound user fields before building the prompt
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
    messages = [
        {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.4)
    content = resp.choices[0].message.content

    # Resilience: if the model accidentally returns JSON, parse body_md
    if content.strip().startswith("{"):
        try:
            obj = json.loads(content)
            content = obj.get("body_md", content)
        except Exception:
            pass

    # ---- SANITIZE (preserve URLs), strip any model-written Sources ----
    content = _sanitize_preserve_urls(content)
    content = _strip_model_sources(content)

    # ---- Build document map + sources from ctx ----
    doc_to_n, sources_built = build_sources(ctx)

    # ---- Pair paragraphs to chunks and insert [n] markers ----
    paras = _paragraph_blocks(content)
    blocks: List[Tuple[str, Dict[str, Any]]] = []
    for i, p in enumerate(paras):
        ch = ctx[min(i, len(ctx)-1)] if ctx else {}
        blocks.append((p, ch))
    content = insert_markers_from_sequence(blocks, doc_to_n)

    # ---- Grounded citations ----
    citations = sources_built

    return {"body_md": content, "citations": citations}

