from dotenv import load_dotenv; load_dotenv()
import streamlit as st
import httpx, os
import re 
import streamlit.components.v1 as components
import requests
from datetime import date

def _escape_streamlit_math(md: str) -> str:
    if not md:
        return md
    # Escape $ not already escaped, and guard against \(...\) / \[...\]
    md = re.sub(r'(?<!\\)\$', r'\\$', md)   # $ -> \$
    md = re.sub(r'(?<!\\)\\\(', r'\\\(', md)  # \( stays literal
    md = re.sub(r'(?<!\\)\\\)', r'\\\)', md)
    md = re.sub(r'(?<!\\)\\\[', r'\\\[', md)
    md = re.sub(r'(?<!\\)\\\]', r'\\\]', md)
    return md

def copy_button(label: str, text: str, key: str):
    if not text:
        return
    # Keep the payload safe inside a JS template literal
    safe = (
        text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("</", "<\\/")  # avoid early tag close
    )
    html = f"""
    <button id="{key}" style="margin:.25rem 0;padding:.4rem .7rem;cursor:pointer"
      onclick="navigator.clipboard.writeText(`{safe}`); 
               const b=this; const old=b.innerText; b.innerText='Copied!';
               setTimeout(()=>b.innerText=old,1200);">
      {label}
    </button>
    """
    components.html(html, height=45)

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="WNC Proposal Assistant", layout="wide")
st.markdown("""
<style>
/* Keep paragraphs tidy and consistent */
.narrative p { margin: 0 0 .6rem; line-height: 1.5; }

/* Make any accidental headings inside the narrative look like body text */
.narrative h1, .narrative h2, .narrative h3 { 
  font-size: 1rem; font-weight: 600; margin: .6rem 0 .25rem;
}

/* Prevent long tokens from overflowing */
[data-testid="stMarkdown"] p { overflow-wrap: anywhere; }
</style>
""", unsafe_allow_html=True)
st.title("WNC Proposal Assistant — PoC")
st.markdown("""
<style>
/* Prevent long tokens from overflowing and normalize paragraph/list typography */
[data-testid="stMarkdown"] p, [data-testid="stMarkdown"] li {
  overflow-wrap: anywhere;
  line-height: 1.55;
  font-size: 1rem;
}
</style>
""", unsafe_allow_html=True)
st.markdown("""
<style>
/* Make inline citation markers tidy */
a.cit { text-decoration: none; font-size: 0.85em; vertical-align: super; padding-left: 2px; }
.sources h4 { margin: 0.75rem 0 0.25rem; font-weight: 600; font-size: 1rem; }
.sources ol { margin: 0.25rem 0 0.75rem 1.25rem; padding: 0; }
.sources li { margin: 0.15rem 0; }
.src-domain { color: var(--secondary-text, #666); font-size: 0.9em; margin-left: .25rem; }
/* Prevent long tokens from overflowing */
[data-testid="stMarkdown"] p { overflow-wrap: anywhere; }
</style>
""", unsafe_allow_html=True)

import re, html, urllib.parse

# --- Citations helpers ---
def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or ""
    except Exception:
        return ""

def render_sources_ol(sources):
    """Return an HTML <ol> of sources with anchors (#src-n) plus optional county/topic badges."""
    if not sources:
        return ""
    items = []
    for i, src in enumerate(sources, start=1):
        title = src.get("title") or src.get("label") or f"Source {i}"
        url = (src.get("url") or "").strip()
        county = (src.get("county") or "").strip()
        topics = src.get("topics") or []

        title_html = html.escape(title)
        url_attr = html.escape(url)

        # domain hint
        dom = _domain(url)
        dom_html = f' <span class="src-domain">({html.escape(dom)})</span>' if dom else ""

        # badges
        badges = []
        if county:
            badges.append(f'<span class="badge" style="margin-left:.35rem;border:1px solid #ddd;border-radius:6px;padding:0 .35rem;font-size:.85em;">{html.escape(county)}</span>')
        for t in topics:
            if str(t).strip():
                badges.append(f'<span class="badge" style="margin-left:.25rem;border:1px solid #eee;border-radius:6px;padding:0 .35rem;font-size:.82em;">{html.escape(str(t))}</span>')
        badges_html = "".join(badges)

        items.append(
            f'<li id="src-{i}">'
            f'<a href="{url_attr}" target="_blank" rel="noopener">{title_html}</a>'
            f'{dom_html}{badges_html}'
            f'</li>'
        )
    return '<div class="sources"><h4>Sources</h4><ol>' + "".join(items) + "</ol></div>"

def linkify_markers(md: str, sources):
    """Replace [n] with anchors that jump to the footnote #src-n. Leaves other brackets alone."""
    if not md or not sources:
        return md
    max_n = len(sources)
    def _repl(m):
        n = int(m.group(1))
        if 1 <= n <= max_n:
            # Optional tooltip with source label/domain
            src = sources[n-1]
            tip = html.escape((src.get("label") or "") + (" — " + _domain(src.get("url") or "") if src.get("url") else ""))
            return f'<a href="#src-{n}" class="cit" title="{tip}">[{n}]</a>'
        return m.group(0)
    # Only match square-bracketed integers (avoid markdown links like [text](url))
    return re.sub(r'(?<!\()\\?\[(\d+)]', _repl, md)

with st.sidebar:
    st.header("Settings")
    audience = st.selectbox("Audience", ["major_donor", "foundation", "corporate"])
    ask = st.text_input("Ask (optional)", "Raise $250,000 in microgrants")
    deadline = st.text_input("Deadline (optional)", "October 15, 2025")
    length = st.selectbox(
        "Length",
        options=["brief", "standard", "long"],
        index=1,
        help="Controls target word count for Email & Narrative."
    )
    tone = st.selectbox("Tone", ["urgent", "compassionate", "data-led", "hopeful"])
    api_url = st.text_input("API URL", API_URL)
        # --- Retrieval Settings ---
    st.sidebar.subheader("Retrieval Settings")
    k = st.sidebar.slider("Top-K", min_value=1, max_value=20, value=8)

    topic_options = ["small_business", "housing", "infrastructure", "education", "health"]
    county_options = ["Haywood", "Buncombe", "Jackson", "Transylvania", "Madison"]

    sel_topics   = st.sidebar.multiselect("Topics", topic_options, default=[])
    sel_counties = st.sidebar.multiselect("Counties", county_options, default=[])

    col1, col2 = st.sidebar.columns(2)
    with col1:
        date_from = st.date_input("From", value=None)
    with col2:
        date_to = st.date_input("To", value=None)

    filters = {
        "topics": sel_topics or None,
        "counties": sel_counties or None,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
    }
    filters = {kk: vv for kk, vv in filters.items() if vv}  # drop empties

    # Show active filters
    active = []
    if sel_topics: active.append(f"Topics: {', '.join(sel_topics)}")
    if sel_counties: active.append(f"Counties: {', '.join(sel_counties)}")
    if filters.get("date_from"): active.append(f"From: {filters['date_from']}")
    if filters.get("date_to"):   active.append(f"To: {filters['date_to']}")
    if active:
        st.write(" ".join([f"`{a}`" for a in active]))

def export_docx(title: str, content: str, api_url: str):
    r = httpx.post(
        f"{api_url}/export/docx",
        json={"title": title, "content": content},
        timeout=120,
    )
    r.raise_for_status()
    return r.content  # raw .docx bytes

st.subheader("Organization Brief")
org_brief = st.text_area("Paste 2–5 sentences about the org", height=120, value="We are a 501(c)(3) serving flood-impacted small businesses across Western North Carolina...")

st.subheader("Campaign Brief")
campaign_brief = st.text_area("What, who, where, how much", height=120, value="Provide $6–10k microgrants to 40 affected businesses in Haywood County to replace equipment and restart operations.")

col1, col2 = st.columns(2)
with col1:
    if st.button("Generate Donor Email", use_container_width=True):
        with st.spinner("Drafting email..."):
            payload = {
                "org_brief": org_brief,
                "campaign_brief": campaign_brief,
                "audience": audience,
                "ask": ask,
                "deadline": deadline,
                "length": length,
                "tone": tone,
                "k": k,
                "filters": filters,
            }
            r = httpx.post(f"{api_url}/generate/email", json=payload, timeout=120)
            r.raise_for_status()
            resp = r.json()
            email = resp.get("email") or {}

            # Tolerate both new typed and legacy keys
            email_md = resp.get("email_md") or email.get("body_md", "")
            email_sources = resp.get("email_sources") or email.get("citations", [])
            email_subjects = (email.get("subjects") or resp.get("subjects") or [])

            # Main fields → session
            st.session_state["email_md"] = email_md
            st.session_state["email_subjects"] = email_subjects
            st.session_state["email_sources"] = email_sources

            # Optional: keep last raw API response for debugging
            st.session_state["resp_email_raw"] = resp

            # Store k for the Top-K badge (prefer backend, else local UI value, else 8)
            st.session_state["k"] = resp.get("k") or locals().get("k") or st.session_state.get("k") or 8

            # Optional: if backend returns chunks for debug, keep them
            if "chunks" in resp:
                st.session_state["returned_chunks"] = resp["chunks"]

with col2:
    if st.button("Generate 1–2 Page Narrative", use_container_width=True):
        with st.spinner("Drafting narrative..."):
            payload = {
                "org_brief": org_brief,
                "campaign_brief": campaign_brief,
                "audience": audience,
                "ask": ask,
                "deadline": deadline,
                "length": length,
                "tone": tone,
                "k": k,
                "filters": filters,
            }
            r = httpx.post(f"{api_url}/generate/narrative", json=payload, timeout=180)
            r.raise_for_status()
            resp = r.json()
            narrative = resp.get("narrative") or {}

            # Tolerate both new typed and legacy keys
            narr_md = resp.get("narrative_md") or narrative.get("body_md", "")
            narr_sources = resp.get("narrative_sources") or narrative.get("citations", [])

            # Session
            st.session_state["narrative_md"] = narr_md
            st.session_state["narrative_sources"] = narr_sources

            # Optional: keep last raw API response for debugging
            st.session_state["resp_narr_raw"] = resp

            # Keep k in session (prefer backend, else local UI value, else prior/default)
            st.session_state["k"] = resp.get("k") or locals().get("k") or st.session_state.get("k") or 8

            # Optional: if backend returns chunks for debug, keep them
            if "chunks" in resp:
                st.session_state["returned_chunks"] = resp["chunks"]

st.divider()

# --- Tiny debug: show Top-K last used (fallback to 8) ---
k_val = st.session_state.get("k") or 8
st.caption(f"Top-K: {k_val}")

colA, colB = st.columns(2)
with colA:

    st.subheader("Subject Options")
    subs = st.session_state.get("email_subjects", [])
    if subs:
        for s in subs:
            st.write(f"- {s}")

    st.subheader("Donor Email (Markdown)")
    email_md = st.session_state.get("email_md", "")
    email_sources = st.session_state.get("email_sources", [])

    if email_md:
        # NEW: escape $ so Streamlit won't invoke KaTeX
        email_md_safe = _escape_streamlit_math(email_md)                  # <-- NEW
        email_html = linkify_markers(email_md_safe, email_sources)        # <-- CHANGED: pass _safe
        st.markdown(email_html, unsafe_allow_html=True)
        st.markdown(render_sources_ol(email_sources), unsafe_allow_html=True)

        # ⬇️ Copy button (copies the raw markdown, not HTML)
        copy_button("Copy Email", email_md, key="copy_email")

    else:
        st.markdown("_No email yet_")

    # Tiny toast if no citations
    if st.session_state.get("email_md") and not st.session_state.get("email_sources"):
        st.info("No citations returned for this run.")

    if st.session_state.get("email_md"):
        if st.button("Export Email to Docx", key="btn_export_email"):
            data = export_docx("Donor_Email", st.session_state["email_md"], api_url)
            st.download_button(
                "Download Email.docx",
                data=data,
                file_name="Donor_Email.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="dl_email",
            )

    with st.expander("Sources", expanded=False):
        sources = st.session_state.get("email_sources", [])
        for i, src in enumerate(sources, start=1):
            label = src.get("label") or f"Source {i}"
            url = src.get("url") or ""
            st.write(f"[{i}] {label} — {url}")

    with st.expander("Debug: show raw email markdown", expanded=False):
        st.code(st.session_state.get("email_md", ""), language="markdown")

    with st.expander("Response (debug)"):
        st.json({
            "email_md": st.session_state.get("email_md", ""),
            "email_sources": st.session_state.get("email_sources", []),
            # If you kept the raw API response:
            "raw": st.session_state.get("resp_email_raw", {}),
        })

with colB:
    st.subheader("Grant Narrative (Markdown)")
    narr_md = st.session_state.get("narrative_md", "")
    narr_sources = st.session_state.get("narrative_sources", [])

    if narr_md:
        st.markdown('<div class="narrative">', unsafe_allow_html=True)
        # NEW: escape $ to disable KaTeX
        narr_md_safe = _escape_streamlit_math(narr_md)                    # <-- NEW
        narr_html = linkify_markers(narr_md_safe, narr_sources)           # <-- CHANGED: pass _safe
        st.markdown(narr_html, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        # ⬇️ Copy button (copies the raw markdown, not HTML)
        copy_button("Copy Narrative", narr_md, key="copy_narr")

        st.markdown(render_sources_ol(narr_sources), unsafe_allow_html=True)
    
    else:
        st.markdown("_No narrative yet_")

    # Tiny toast if no citations
    if st.session_state.get("narrative_md") and not st.session_state.get("narrative_sources"):
        st.info("No citations returned for this run.")

    if st.session_state.get("narrative_md"):
        if st.button("Export Narrative to Docx", key="btn_export_narrative"):
            data = export_docx("Grant_Narrative", st.session_state["narrative_md"], api_url)
            st.download_button(
                "Download Narrative.docx",
                data=data,
                file_name="Grant_Narrative.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="dl_narr",
            )

    with st.expander("Narrative Sources", expanded=False):
        for i, src in enumerate(st.session_state.get("narrative_sources", []), start=1):
            label = src.get("label", f"Source {i}")
            url = src.get("url", "")
            st.write(f"[{i}] {label} — {url}")

    with st.expander("Debug: show raw narrative markdown", expanded=False):
        st.code(st.session_state.get("narrative_md", ""), language="markdown")

    with st.expander("Response (debug)"):
        st.json({
            "narrative_md": st.session_state.get("narrative_md", ""),
            "narrative_sources": st.session_state.get("narrative_sources", []),
            # If you kept the raw API response:
            "raw": st.session_state.get("resp_narr_raw", {}),
        })

    # --- Optional debug: list titles/URLs of returned chunks if backend includes them ---
    with st.expander("Debug: returned chunks (titles/URLs)", expanded=False):
        for ch in st.session_state.get("returned_chunks", []):
            title = ch.get('title') or ch.get('meta', {}).get('title') or 'Source'
            # Prefer 'source' (what the retriever returns), fall back to any 'url' field
            url = ch.get('source') or ch.get('url') or ch.get('meta', {}).get('url') or ''
            st.write(f"- {title} — {url}")

