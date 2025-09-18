# app/routes/generate.py

from fastapi import APIRouter
from typing import List, Dict, Any, Optional
import os

from app.models.schemas import (
    GenerateRequest,
    SourceItem,
    EmailPiece,
    NarrativePiece,
    GenerateEmailResponseCompat,
    GenerateNarrativeResponseCompat,
)
from app.services.retriever import retrieve
from app.services.generator import generate_email, generate_narrative

# If app/main.py already mounts this with prefix="/generate",
# keep router = APIRouter(). Otherwise you can set: APIRouter(prefix="/generate", tags=["generate"])
router = APIRouter()

KB_PATH = os.environ.get("KB_PATH", "data/processed")


import re, logging, hashlib
logger = logging.getLogger(__name__)

def _map_citations(raw_list):
    norm = []
    for s in (raw_list or []):
        d = dict(s)  # shallow copy

        # title: accept 'label' fallback
        if not d.get("title"):
            d["title"] = d.get("label") or "Source"

        # marker: accept legacy 'n'
        if d.get("n") is not None and d.get("marker") is None:
            d["marker"] = d["n"]

        # url: empty string → None for Optional[HttpUrl]
        url = (d.get("url") or "").strip()
        d["url"] = url or None

        # date: must be YYYY-MM-DD or None
        dv = d.get("date")
        if not dv or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(dv)):
            d["date"] = None

        # topics: normalize to list[str]
        tv = d.get("topics")
        if tv is None:
            d["topics"] = []
        elif not isinstance(tv, list):
            d["topics"] = [str(tv)]
        else:
            d["topics"] = [str(x) for x in tv if str(x).strip()]

        # ---- ensure doc_id (required) ----
        if not d.get("doc_id"):
            if url:
                d["doc_id"] = f"url::{url}"
            else:
                # stable-ish fallback from title (+ marker if present)
                seed = (d.get("title") or "") + "|" + str(d.get("marker") or "")
                d["doc_id"] = "doc::" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]

        try:
            norm.append(SourceItem(**d))
        except Exception:
            logger.exception("Bad citation payload after normalization: %r", d)
            # Skip malformed entries instead of 500-ing the whole response
    return norm

@router.post("/email", response_model=GenerateEmailResponseCompat)
def post_generate_email(req: GenerateRequest):
    query = f"{req.campaign_brief}\n{req.org_brief}"
    filters = req.filters.model_dump(exclude_none=True) if req.filters else None

    try:
        ctx = retrieve(query=query, kb_path=KB_PATH, k=req.k, filters=filters)
    except FileNotFoundError:
        # Empty-store friendly: return BOTH typed object and legacy keys.
        empty_email = EmailPiece(subjects=[], body_md="", citations=[])
        return {
            "email": empty_email,
            "email_md": empty_email.body_md,         # legacy
            "email_sources": [],                     # legacy
        }

    # Let the generator do grounding + markers + finalization
    out = generate_email(payload=req.model_dump(), ctx=ctx)

    # Construct the typed piece
    email = EmailPiece(
        subjects=out.get("subjects", []),
        body_md=out.get("body_md", out.get("email_md", "")),
        citations=_map_citations(out.get("citations")),
    )

    # Return typed + legacy keys
    return {
        "email": email,
        "email_md": email.body_md,                              # legacy
        "email_sources": [c.model_dump() for c in email.citations],  # legacy
        # NOTE: We intentionally do NOT return top-level "subjects" anymore.
        # The UI should read subjects from resp["email"]["subjects"].
    }


@router.post("/narrative", response_model=GenerateNarrativeResponseCompat)
def post_generate_narrative(req: GenerateRequest):
    query = f"{req.campaign_brief}\n{req.org_brief}"
    filters = req.filters.model_dump(exclude_none=True) if req.filters else None

    try:
        ctx = retrieve(query=query, kb_path=KB_PATH, k=req.k, filters=filters)
    except FileNotFoundError:
        empty_narr = NarrativePiece(body_md="", citations=[])
        return {
            "narrative": empty_narr,
            "narrative_md": empty_narr.body_md,     # legacy
            "narrative_sources": [],                # legacy
        }

    out = generate_narrative(payload=req.model_dump(), ctx=ctx)

    narrative = NarrativePiece(
        body_md=out.get("body_md", out.get("narrative_md", "")),
        citations=_map_citations(out.get("citations")),
    )

    return {
        "narrative": narrative,
        "narrative_md": narrative.body_md,                         # legacy
        "narrative_sources": [c.model_dump() for c in narrative.citations],  # legacy
    }
