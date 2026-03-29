#!/usr/bin/env python3
"""Discover ATS slugs for high-paying tech companies.

Fetches a curated list of high-compensation tech companies and runs the ATS
prober (Greenhouse / Lever / Ashby) against each one. Results are saved to
~/.jobsgrep/company_ats_mapping.json and used automatically in future searches.

Usage:
    python scripts/discover_highpaying.py [--limit N]
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Curated list of high-compensation tech companies (parsed from public rankings)
# These are well-known to pay in the top percentile for software engineers.
HIGH_PAYING_COMPANIES = [
    "OpenAI", "Waymo", "Jane Street", "Netflix", "Scale AI",
    "Citadel", "Databricks", "Anthropic", "Stripe", "Airbnb",
    "Brex", "Ramp", "Figma", "Notion", "Linear",
    "Vercel", "Supabase", "Anyscale", "Modal", "Weights & Biases",
    "Cohere", "Mistral", "Together AI", "Replicate", "Hugging Face",
    "Google", "Meta", "Apple", "Amazon", "Microsoft",
    "Palantir", "Snowflake", "Cloudflare", "Datadog", "MongoDB",
    "Confluent", "HashiCorp", "CockroachDB", "Elastic", "Redis Labs",
    "Robinhood", "Plaid", "Chime", "Mercury", "Ramp",
    "Coinbase", "Gemini", "Alchemy", "Phantom",
    "Benchling", "Recursion", "Insitro", "Zymergen",
    "SpaceX", "Anduril", "Shield AI", "Joby Aviation",
    "Airtable", "Coda", "Retool", "Loom", "Miro",
    "Temporal", "Replit", "Sourcegraph", "Cursor", "Codeium",
    "Intercom", "Amplitude", "Mixpanel", "PostHog", "Segment",
    "PlanetScale", "Neon", "Turso", "Railway", "Render",
    "Samsara", "Verkada", "Verkada", "Armis", "Wiz",
    "Lacework", "Orca Security", "Snyk", "Drata", "Vanta",
    "Rippling", "Gusto", "Deel", "Remote", "Lattice",
    "Gong", "Outreach", "Salesloft", "Chorus", "People.ai",
    "Faire", "Flexport", "Convoy", "Project44", "Samsara",
    "DoorDash", "Instacart", "Gopuff", "Nuro", "Aurora",
    "Duolingo", "Grammarly", "Canva", "Figma",
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Discover ATS slugs for high-paying companies")
    parser.add_argument("--limit", type=int, default=len(HIGH_PAYING_COMPANIES),
                        help="Max companies to probe")
    args = parser.parse_args()

    import httpx
    from jobsgrep.config import get_settings
    from jobsgrep.discovery.ats_prober import probe_company
    from jobsgrep.discovery.company_list import get_mapping_cache, upsert_mapping

    settings = get_settings()
    targets = list(dict.fromkeys(HIGH_PAYING_COMPANIES))[:args.limit]

    existing = get_mapping_cache()
    to_probe = [c for c in targets if c.lower() not in existing]
    already  = len(targets) - len(to_probe)
    print(f"\nHigh-paying company discovery")
    print(f"  {len(targets)} companies, {already} already cached, {len(to_probe)} to probe\n")

    if not to_probe:
        print("All companies already in cache. Run 'jobsgrep sources' to verify.")
        return

    found = 0
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
        for i, name in enumerate(to_probe, 1):
            mapping = await probe_company(name, client, settings)
            slugs = [s for s in [mapping.greenhouse_slug, mapping.lever_slug, mapping.ashby_slug] if s]
            if slugs:
                upsert_mapping(mapping)
                found += 1
                print(f"  [{i:3}/{len(to_probe)}] ✓ {name:<30} "
                      f"gh={mapping.greenhouse_slug or '-':<20} "
                      f"lv={mapping.lever_slug or '-':<20} "
                      f"ash={mapping.ashby_slug or '-'}")
            else:
                print(f"  [{i:3}/{len(to_probe)}] · {name}")

    print(f"\nDone. {found}/{len(to_probe)} new ATS mappings found.")


if __name__ == "__main__":
    asyncio.run(main())
