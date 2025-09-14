from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.services.exporter import markdownish_to_docx_bytes

router = APIRouter()

class ExportRequest(BaseModel):
    title: str
    content: str

@router.post("/docx")
def export_docx(req: ExportRequest):
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="No content to export.")
    data = markdownish_to_docx_bytes(req.title, req.content)
    fname = (req.title or "export").replace(" ", "_") + ".docx"
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )
