.PHONY: api ui dev seed

api:
	uvicorn app.main:app --reload --port 8000

ui:
	streamlit run ui/app.py --server.port 8501

dev:
	@echo "Run API and UI in two terminals:"
	@echo "  make api   # FastAPI on :8000"
	@echo "  make ui    # Streamlit on :8501"

seed:
	python scripts/seed_minimal.py
