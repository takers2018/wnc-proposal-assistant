from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class GenerateRequest(BaseModel):
    org_brief: str = Field(..., description="Short org boilerplate and capacity notes")
    campaign_brief: str = Field(..., description="Short description: what, who, where, how much")
    audience: str = Field("major_donor", description="major_donor|foundation|corporate")
    tone: str = Field("hopeful", description="urgent|compassionate|data-led|hopeful")
    ask: Optional[str] = Field(None, description="e.g., $250,000 for microgrants")
    deadline: Optional[str] = Field(None, description="e.g., Oct 15, 2025")
    length: Literal["brief", "standard", "long"] = "standard"

class EmailPiece(BaseModel):
    subjects: List[str]
    body_md: str
    citations: List[dict]

class NarrativePiece(BaseModel):
    body_md: str
    citations: List[dict]

class GenerateEmailResponse(BaseModel):
    email: EmailPiece

class GenerateNarrativeResponse(BaseModel):
    narrative: NarrativePiece
