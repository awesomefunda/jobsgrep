#!/usr/bin/env python3
"""Seed known_companies.json with well-known tech companies and their confirmed ATS slugs.

Run this once to pre-populate the cache.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

KNOWN = [
    # (name, greenhouse_slug, lever_slug, ashby_slug)
    ("Stripe",          "stripe",       None,       None),
    ("Airbnb",          "airbnb",       None,       None),
    ("Figma",           "figma",        "figma",    None),
    ("Notion",          "notion",       None,       None),
    ("Miro",            "miro",         None,       None),
    ("Airtable",        None,           "airtable", None),
    ("Linear",          None,           None,       "linear"),
    ("Vercel",          None,           None,       "vercel"),
    ("Supabase",        None,           None,       "supabase"),
    ("OpenAI",          "openai",       None,       "openai"),
    ("Anthropic",       "anthropic",    None,       "anthropic"),
    ("Cohere",          "cohere",       None,       "cohere"),
    ("Databricks",      "databricks",   None,       None),
    ("Snowflake",       "snowflake",    None,       None),
    ("Cloudflare",      "cloudflare",   None,       None),
    ("Datadog",         "datadog",      None,       None),
    ("MongoDB",         "mongodb",      None,       None),
    ("Elastic",         "elastic",      None,       None),
    ("HashiCorp",       "hashicorp",    "hashicorp", None),
    ("Confluent",       "confluent",    None,       None),
    ("dbt Labs",        "dbt-labs",     None,       "dbt-labs"),
    ("Airbyte",         "airbyte",      None,       None),
    ("HuggingFace",     "huggingface",  None,       None),
    ("Retool",          None,           None,       "retool"),
    ("Brex",            "brex",         None,       None),
    ("Ramp",            "ramp",         None,       None),
    ("Plaid",           "plaid",        None,       None),
    ("Rippling",        "rippling",     None,       None),
    ("Duolingo",        "duolingo",     None,       None),
    ("Grammarly",       "grammarly",    None,       None),
    ("Intercom",        None,           None,       None),
    ("Loom",            None,           None,       "loom"),
    ("Postman",         None,           "postman",  None),
    ("Netlify",         None,           "netlify",  None),
    ("GitLab",          None,           "gitlab",   None),
    ("Asana",           None,           "asana",    None),
    ("Weights & Biases","weights-biases", None,     "weights-biases"),
    ("Scale AI",        None,           "scale-ai", None),
    ("Anyscale",        None,           None,       "anyscale"),
    ("Modal",           None,           None,       "modal"),
    ("Replicate",       None,           None,       "replicate"),
    ("CockroachDB",     "cockroachdb",  "cockroachdb", None),
    ("Temporal",        None,           "temporal", None),
    ("Sourcegraph",     None,           None,       "sourcegraph"),
    ("Replit",          None,           None,       "replit"),
    ("Cursor",          None,           None,       "cursor"),
    ("Mercury",         None,           None,       "mercury"),
    ("Neon",            None,           None,       "neon"),
    ("Render",          None,           None,       "render"),
    ("PlanetScale",     None,           None,       "planetscale"),
    ("Mistral",         None,           None,       "mistral"),
    ("Stability AI",    None,           None,       "stability"),
    ("Benchling",       None,           None,       "benchling"),
    ("Recursion",       None,           None,       "recursion"),
]


def main() -> None:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from jobsgrep.discovery.company_list import upsert_mapping
    from jobsgrep.models import ATSMapping
    from datetime import datetime, timezone

    print(f"Seeding {len(KNOWN)} known companies...")
    for name, gh, lv, ash in KNOWN:
        mapping = ATSMapping(
            company=name,
            greenhouse_slug=gh,
            lever_slug=lv,
            ashby_slug=ash,
            discovered_at=datetime.now(timezone.utc).isoformat(),
        )
        upsert_mapping(mapping)
        print(f"  ✓ {name}")

    print(f"\nSeeded {len(KNOWN)} companies into ~/.jobsgrep/company_ats_mapping.json")


if __name__ == "__main__":
    main()
