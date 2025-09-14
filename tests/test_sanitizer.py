import sys, pathlib
repo = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))

from app.services.generator import _sanitize_markdown

CASES = [
    ("Raise $250, 000 in microgrants", "Raise $250,000 in microgrants"),
    ("$5-10 k", "$5-10k"),
    ("Provide $6–10k microgrants", "Provide $6-10k microgrants"),
    ("October 15,2025", "October 15, 2025"),
    ("impacted\u2009businesses\u202Fin", "impacted businesses in"),
]

def test_sanitizer_cases():
    for raw, expected in CASES:
        assert _sanitize_markdown(raw) == expected
