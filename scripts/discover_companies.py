#!/usr/bin/env python3
"""One-time ATS slug discovery for all YC companies.

Usage:
    python scripts/discover_companies.py [--limit N] [--csv extra_companies.csv]
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

# Ensure jobsgrep package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Discover ATS slugs for YC companies")
    parser.add_argument("--limit", type=int, default=500, help="Max YC companies to probe")
    parser.add_argument("--csv", type=str, help="Optional CSV with additional companies (column: name)")
    args = parser.parse_args()

    from jobsgrep.discovery.company_list import discover_from_yc, upsert_mapping
    from jobsgrep.discovery.ats_prober import probe_company
    from jobsgrep.config import get_settings
    import httpx

    print(f"Discovering ATS slugs for up to {args.limit} YC hiring companies...")
    yc_count = await discover_from_yc(limit=args.limit)
    print(f"  → YC discovery: {yc_count} new mappings found")

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"CSV file not found: {csv_path}", file=sys.stderr)
        else:
            settings = get_settings()
            names = []
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row.get("name") or row.get("company") or row.get("Name", "")
                    if name.strip():
                        names.append(name.strip())

            print(f"Probing {len(names)} companies from CSV...")
            async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
                for name in names:
                    mapping = await probe_company(name, client)
                    if mapping.greenhouse_slug or mapping.lever_slug or mapping.ashby_slug:
                        upsert_mapping(mapping)
                        print(f"  ✓ {name}: gh={mapping.greenhouse_slug} lv={mapping.lever_slug} ash={mapping.ashby_slug}")
                    else:
                        print(f"  · {name}: no ATS found")

    print("\nDone! Run 'jobsgrep sources' to see enabled sources.")


if __name__ == "__main__":
    asyncio.run(main())
