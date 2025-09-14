import json, os

os.makedirs("data/processed", exist_ok=True)
path = "data/processed/context.jsonl"

rows = [
    {
        "collection": "org_profile",
        "title": "Organization Boilerplate",
        "text": "The Western NC Community Fund (example) is a 501(c)(3) supporting small business recovery and community resilience across Western North Carolina counties.",
        "source": "https://example.org/boilerplate",
        "date": "2025-01-01",
        "geo": "WNC",
        "tags": ["boilerplate"]
    },
    {
        "collection": "programs",
        "title": "Microgrants Program Overview",
        "text": "Microgrants of $6–10k targeted to flood-impacted businesses to replace essential equipment and restart operations; initial cohort: 40 businesses in Haywood County.",
        "source": "https://example.org/programs/microgrants",
        "date": "2025-01-10",
        "geo": "Haywood County",
        "tags": ["microgrants","small_business"]
    },
    {
        "collection": "local_facts",
        "title": "Local Impact Fact — Placeholder",
        "text": "Placeholder: Verified post-storm impact statistic goes here with exact number and date. Replace with authoritative source in Seed Data thread.",
        "source": "https://REPLACE_WITH_REAL_SOURCE",
        "date": "2024-10-XX",
        "geo": "Western NC",
        "tags": ["impact","storm"]
    }
]

with open(path, "w", encoding="utf-8") as f:
    for row in rows:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

print(f"Wrote {len(rows)} rows to {path}")
