#!/usr/bin/env python3
"""Generate seed cache files for Vercel deployment.

Fetches + scores jobs for the most common searches, then saves the cache
files to data/seed/ so they can be committed to the repo and loaded
instantly on Vercel cold starts (no API calls needed).

Run this locally before deploying:
    python scripts/generate_seed.py

Then commit data/seed/ to git and redeploy.
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Queries to pre-seed — these cover the most common searches on jobsgrep.com
SEED_QUERIES = [
    "Software Engineer remote",
    "Software Engineer San Francisco Bay Area",
    "Senior Software Engineer remote",
    "Senior Software Engineer San Francisco Bay Area",
    "Staff Software Engineer",
    "Backend Engineer remote",
    "Machine Learning Engineer",
]


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate seed cache for Vercel deploy")
    parser.add_argument("--queries", default="",
                        help="Comma-separated override queries")
    parser.add_argument("--first-only", action="store_true",
                        help="Only seed the first 2 queries (fastest)")
    args = parser.parse_args()

    # Ensure we run in LOCAL mode so scrapers + all sources are available
    import os
    os.environ.setdefault("JOBSGREP_MODE", "LOCAL")

    from jobsgrep.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    from jobsgrep.job_cache import _cache_dir, _scored_dir, cache_key
    from jobsgrep.nlp.parser import parse_query
    from jobsgrep.scoring.engine import score_jobs
    from jobsgrep.prefetch import _prefetch_query

    seed_dir = Path(__file__).parent.parent / "data" / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)

    queries = SEED_QUERIES
    if args.first_only:
        queries = SEED_QUERIES[:2]
    elif args.queries:
        queries = [q.strip() for q in args.queries.split(",") if q.strip()]

    print(f"\nGenerating seed data for {len(queries)} queries\n")

    seeded = 0
    for i, query in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] {query}")
        try:
            raw_count, scored_count = await _prefetch_query(query)
            if scored_count == 0:
                print(f"  WARNING: no scored results — skipping")
                continue

            # Copy scored cache file to data/seed/
            parsed = await parse_query(query, None)
            key = cache_key(parsed)

            scored_src = _scored_dir() / f"{key}.json"
            raw_src    = _cache_dir()  / f"{key}.json"

            if scored_src.exists():
                dst = seed_dir / f"scored__{key}.json"
                shutil.copy(scored_src, dst)
                size_kb = dst.stat().st_size // 1024
                print(f"  OK scored__{key}.json — {scored_count} scored jobs ({size_kb}KB)")
                seeded += 1
            if raw_src.exists():
                dst = seed_dir / f"raw__{key}.json"
                shutil.copy(raw_src, dst)

        except Exception as e:
            print(f"  FAILED error: {e}")

        # Stagger to be polite to APIs
        if i < len(queries):
            await asyncio.sleep(15)

    print(f"\nDone — {seeded} seed files written to data/seed/")
    print("\nNext steps:")
    print("  git add data/seed/")
    print("  git commit -m 'seed: pre-scored job data for Vercel cold starts'")
    print("  vercel deploy")


if __name__ == "__main__":
    asyncio.run(main())
