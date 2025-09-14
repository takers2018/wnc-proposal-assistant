# sanitizer-tests.py
import sys, pathlib

# Add repo root to Python path
repo_root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(repo_root))

from app.services.generator import _sanitize_markdown, _sanitize_inline_text

tests = [
    "Raise $250, 000 in microgrants",
    "$5-10 k",
    "Provide $6–10k microgrants to 40 affected businesses",
    "October 15,2025",
    "impacted\u2009businesses\u202Fin",
]

print("=== _sanitize_markdown ===")
for t in tests:
    print("IN :", t)
    print("OUT:", _sanitize_markdown(t))
    print("---")

print("=== _sanitize_inline_text ===")
for t in tests:
    print("IN :", t)
    print("OUT:", _sanitize_inline_text(t))
    print("---")
