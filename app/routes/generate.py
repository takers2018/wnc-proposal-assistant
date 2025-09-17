from fastapi import APIRouter
from app.models.schemas import GenerateRequest, GenerateEmailResponse, GenerateNarrativeResponse
from app.services.retriever import retrieve
from app.services.generator import generate_email, generate_narrative
from app.services.citations import build_sources
import os

router = APIRouter()
KB_PATH = os.environ.get("KB_PATH", "data/processed")

@router.post("/email")
def post_generate_email(req: GenerateRequest):
    query = f"{req.campaign_brief}\n{req.org_brief}"
    filters = req.filters.model_dump(exclude_none=True) if req.filters else None
    try:
        ctx = retrieve(query=query, kb_path=KB_PATH, k=req.k, filters=filters)
    except FileNotFoundError:
        # Empty-store friendly response that still conforms to your response_model
        return {"email": {"subjects": [], "body_md": "", "citations": []}}

    # Build deduped, ordered sources from retrieved chunks
    _, sources = build_sources(ctx)
    email = generate_email(payload=req.model_dump(), ctx=ctx)

    # Ensure grounded citations go back to the UI
    email["citations"] = sources

    # Return flat keys expected by the UI
    return {
        "email_md": email.get("body_md", ""),
        "email_sources": email.get("citations", []),
        "subjects": email.get("subjects", []),
        # (optional) include these only if your UI reads them; otherwise omit:
        # "k": req.k,
        # "applied_filters": filters or {},
        # "chunks": ctx,
    }

@router.post("/narrative")
def post_generate_narrative(req: GenerateRequest):      
    query = f"{req.campaign_brief}\n{req.org_brief}"
    filters = req.filters.model_dump(exclude_none=True) if req.filters else None
    try:
        ctx = retrieve(query=query, kb_path=KB_PATH, k=req.k, filters=filters)
    except FileNotFoundError:
        return {"narrative": {"body_md": "", "citations": []}}

    _, sources = build_sources(ctx)
    narrative = generate_narrative(payload=req.model_dump(), ctx=ctx)
    narrative["citations"] = sources

    return {
        "narrative_md": narrative.get("body_md", ""),
        "narrative_sources": narrative.get("citations", []),
        # (optional) only if your UI reads them:
        # "k": req.k,
        # "applied_filters": filters or {},
        # "chunks": ctx,
    }
