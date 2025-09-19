from fastapi import APIRouter, Request
from typing import Any, Dict, Optional
import os, logging, re, hashlib

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

router = APIRouter()
KB_PATH = os.environ.get("KB_PATH", "data/processed")
logger = logging.getLogger(__name__)

def _normalize_rf(rf_in: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if rf_in is None:
        return {"date_from": None, "date_to": None, "counties": None, "topics": None}
    rf = dict(rf_in)
    out = {"date_from": None, "date_to": None, "counties": None, "topics": None}
    if isinstance(rf.get("date"), dict):
        out["date_from"] = rf["date"].get("from")
        out["date_to"]   = rf["date"].get("to")
    else:
        out["date_from"] = rf.get("date_from")
        out["date_to"]   = rf.get("date_to")
    out["counties"] = rf.get("counties") or rf.get("county")
    out["topics"]   = rf.get("topics")   or rf.get("topic")
    return out

def _map_citations(raw_list):
    norm = []
    for s in (raw_list or []):
        d = dict(s)
        if not d.get("title"): d["title"] = d.get("label") or "Source"
        if d.get("n") is not None and d.get("marker") is None: d["marker"] = d["n"]
        url = (d.get("url") or "").strip(); d["url"] = url or None
        dv = d.get("date"); d["date"] = dv if dv and re.match(r"^\d{4}-\d{2}-\d{2}$", str(dv)) else None
        tv = d.get("topics"); d["topics"] = [str(x) for x in (tv if isinstance(tv, list) else [tv] if tv else []) if str(x).strip()]
        if not d.get("doc_id"):
            d["doc_id"] = f"url::{url}" if url else "doc::" + hashlib.sha1(((d.get("title") or "") + "|" + str(d.get("marker") or "")).encode()).hexdigest()[:12]
        try:
            norm.append(SourceItem(**d))
        except Exception:
            logger.exception("Bad citation payload after normalization: %r", d)
    return norm

@router.post("/email", response_model=GenerateEmailResponseCompat)
async def post_generate_email(req: GenerateRequest, request: Request):
    print("[DBG] route: app/routes/generate.py -> post_generate_email invoked")

    # 1) Read the full raw JSON so unknown fields aren't dropped by Pydantic
    raw = await request.json()
    # One-run debug to verify what keys the harness sends:
    print(f"[DBG] raw keys: {list(raw.keys())}")

    # 2) Accept multiple places/casings for filters
    rf_raw = (
        raw.get("retrieve_filters")
        or raw.get("retrieveFilters")
        or raw.get("filters")
        or (raw.get("options", {}) or {}).get("retrieve_filters")
        or (raw.get("options", {}) or {}).get("retrieveFilters")
        or None
    )

    # 3) Normalize nested/flat → {date_from,date_to,counties,topics}
    RF = _normalize_rf(rf_raw)
    # Extra: if camelCase date keys were used, pick them up
    if RF["date_from"] is None and isinstance(rf_raw, dict) and "dateFrom" in rf_raw:
        RF["date_from"] = rf_raw.get("dateFrom")
    if RF["date_to"] is None and isinstance(rf_raw, dict) and "dateTo" in rf_raw:
        RF["date_to"] = rf_raw.get("dateTo")
    print(f"[DBG] route RF={RF!r}")

    # 4) Retrieval WITH filters (critical for no-match behavior)
    query = f"{req.campaign_brief}\n{req.org_brief}"
    k = req.k or 8
    ctx = retrieve(query=query, kb_path=KB_PATH, k=k, filters=RF)

    # 5) Generator payload includes the same filters (defensive re-filter downstream)
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    payload["retrieve_filters"] = RF

    out = generate_email(payload=payload, ctx=ctx)

    email = EmailPiece(
        subjects=out.get("subjects", []),
        body_md=out.get("body_md", out.get("email_md", "")),
        citations=_map_citations(out.get("citations")),
    )
    return {
        "email": email,
        "email_md": email.body_md,
        "email_sources": [c.model_dump() for c in email.citations],
    }

@router.post("/narrative", response_model=GenerateNarrativeResponseCompat)
async def post_generate_narrative(req: GenerateRequest, request: Request):
    raw = await request.json()
    rf_raw = (
        raw.get("retrieve_filters")
        or raw.get("retrieveFilters")
        or raw.get("filters")
        or (raw.get("options", {}) or {}).get("retrieve_filters")
        or (raw.get("options", {}) or {}).get("retrieveFilters")
        or None
    )

    RF = _normalize_rf(rf_raw)
    if RF["date_from"] is None and isinstance(rf_raw, dict) and "dateFrom" in rf_raw:
        RF["date_from"] = rf_raw.get("dateFrom")
    if RF["date_to"] is None and isinstance(rf_raw, dict) and "dateTo" in rf_raw:
        RF["date_to"] = rf_raw.get("dateTo")

    query = f"{req.campaign_brief}\n{req.org_brief}"
    k = req.k or 8
    ctx = retrieve(query=query, kb_path=KB_PATH, k=k, filters=RF)

    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    payload["retrieve_filters"] = RF

    out = generate_narrative(payload=payload, ctx=ctx)

    narrative = NarrativePiece(
        body_md=out.get("body_md", out.get("narrative_md", "")),
        citations=_map_citations(out.get("citations")),
    )
    return {
        "narrative": narrative,
        "narrative_md": narrative.body_md,
        "narrative_sources": [c.model_dump() for c in narrative.citations],
    }
