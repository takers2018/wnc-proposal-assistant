from pydantic import BaseModel, Field, HttpUrl, ConfigDict
from typing import List, Optional, Literal

class RetrieveFilters(BaseModel):
    topics: Optional[List[str]] = None
    counties: Optional[List[str]] = None
    date_from: Optional[str] = None # YYYY-MM-DD (ISO)
    date_to: Optional[str] = None # YYYY-MM-DD (ISO)

class GenerateRequest(BaseModel):
    org_brief: str = Field(..., description="Short org boilerplate and capacity notes")
    campaign_brief: str = Field(..., description="Short description: what, who, where, how much")
    audience: str = Field("major_donor", description="major_donor|foundation|corporate")
    tone: str = Field("hopeful", description="urgent|compassionate|data-led|hopeful")
    ask: Optional[str] = Field(None, description="e.g., $250,000 for microgrants")
    deadline: Optional[str] = Field(None, description="e.g., Oct 15, 2025")
    length: Literal["brief", "standard", "long"] = "standard"
    k: int = 8
    filters: Optional[RetrieveFilters] = None

class SourceItem(BaseModel):
    model_config = ConfigDict(extra='ignore')

    # Accept both new ('marker') and legacy ('n') keys.
    # Either is optional; downstream code can prefer 'marker' if present.
    marker: Optional[int] = None       # new, 1-based marker number
    n: Optional[int] = None            # legacy key
    doc_id: str
    title: str
    url: Optional[HttpUrl] = None      # allow None for legacy/local PDFs
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    county: Optional[str] = None
    topics: Optional[List[str]] = None

class EmailPiece(BaseModel):
    subjects: List[str]
    body_md: str
    citations: List[SourceItem]

class NarrativePiece(BaseModel):
    body_md: str
    citations: List[SourceItem]

class GenerateEmailResponse(BaseModel):
    email: EmailPiece

class GenerateNarrativeResponse(BaseModel):
    narrative: NarrativePiece

class GenerateEmailResponseCompat(GenerateEmailResponse):
    # legacy keys for old UI; included in schema so FastAPI won't drop them
    email_md: Optional[str] = None
    email_sources: Optional[List[SourceItem]] = None

class GenerateNarrativeResponseCompat(GenerateNarrativeResponse):
    # legacy keys for old UI
    narrative_md: Optional[str] = None
    narrative_sources: Optional[List[SourceItem]] = None