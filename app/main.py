from dotenv import load_dotenv; load_dotenv()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.generate import router as generate_router
from app.routes.export import router as export_router

app = FastAPI(title="WNC Proposal Assistant API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate_router, prefix="/generate", tags=["generate"])
app.include_router(export_router,  prefix="/export",   tags=["export"])

@app.get("/healthz")
def healthz():
    return {"ok": True}
