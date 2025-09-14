from fastapi import APIRouter
from app.models.schemas import GenerateRequest, GenerateEmailResponse, GenerateNarrativeResponse
from app.services.retriever import retrieve
from app.services.generator import generate_email, generate_narrative
import os

router = APIRouter()
KB_PATH = os.environ.get("KB_PATH", "data/processed/context.jsonl")

@router.post("/email", response_model=GenerateEmailResponse)
def post_generate_email(req: GenerateRequest):
    query = f"{req.campaign_brief}\n{req.org_brief}"
    ctx = retrieve(query=query, kb_path=KB_PATH, k=8, filters=None)
    email = generate_email(payload=req.model_dump(), ctx=ctx)
    return {"email": email}

@router.post("/narrative", response_model=GenerateNarrativeResponse)
def post_generate_narrative(req: GenerateRequest):
    query = f"{req.campaign_brief}\n{req.org_brief}"
    ctx = retrieve(query=query, kb_path=KB_PATH, k=10, filters=None)
    narrative = generate_narrative(payload=req.model_dump(), ctx=ctx)
    return {"narrative": narrative}
