#!/usr/bin/env python3
"""Seed Vercel cache using already-fetched raw job data.

Instead of re-fetching from sources, this script takes the existing raw job
cache and scores it for the target queries, then writes seed files to data/seed/.

Usage:
    python scripts/seed_from_cache.py
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Queries to seed — maps to (search_terms, locations, remote_ok)
# These must match what users will actually type on jobsgrep.com
SEED_QUERIES = [
    "Software Engineer remote",
    "Software Engineer San Francisco Bay Area California",
    "Senior Software Engineer remote",
    "Senior Software Engineer San Francisco Bay Area",
]


async def main() -> None:
    import os
    os.environ.setdefault("JOBSGREP_MODE", "LOCAL")

    from jobsgrep.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    from jobsgrep.job_cache import _cache_dir, _scored_dir, cache_key, get as raw_get
    from jobsgrep.models import RawJob
    from jobsgrep.nlp.parser import parse_query
    from jobsgrep.scoring.engine import score_jobs
    from jobsgrep.job_cache import store_scored

    seed_dir = Path(__file__).parent.parent / "data" / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)

    # Load ALL raw jobs from every existing cache file into one pool
    cache_dir = _cache_dir()
    all_raw: list[RawJob] = []
    seen: set[str] = set()
    for p in cache_dir.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            for j in d.get("jobs", []):
                jid = j.get("id", "")
                if jid and jid not in seen:
                    seen.add(jid)
                    try:
                        all_raw.append(RawJob(**j))
                    except Exception:
                        pass
            print(f"  Loaded {len(d.get('jobs',[]))} jobs from {p.name} "
                  f"(label: {d.get('label','')})")
        except Exception as e:
            print(f"  Skip {p.name}: {e}")

    if not all_raw:
        print("No raw job cache found. Run a search first, then re-run this script.")
        sys.exit(1)

    print(f"\nTotal unique raw jobs: {len(all_raw)}\n")

    seeded = 0
    for i, query in enumerate(SEED_QUERIES, 1):
        print(f"[{i}/{len(SEED_QUERIES)}] Scoring for: '{query}'")
        try:
            parsed = await parse_query(query, None)
            key = cache_key(parsed)
            print(f"  cache key: {key}  titles={parsed.titles}  locs={parsed.locations}  remote={parsed.remote_ok}")

            scored = await score_jobs(all_raw, parsed)
            print(f"  → {len(scored)} scored jobs")

            if scored:
                store_scored(key, scored, source="seed", label=query)
                # Copy to data/seed/
                src = _scored_dir() / f"{key}.json"
                dst = seed_dir / f"scored__{key}.json"
                if src.exists():
                    shutil.copy(src, dst)
                    size_kb = dst.stat().st_size // 1024
                    print(f"  ✓ wrote {dst.name} ({size_kb} KB)")
                    seeded += 1
        except Exception as e:
            import traceback
            print(f"  ✗ {e}")
            traceback.print_exc()

        print()

    print(f"Done — {seeded}/{len(SEED_QUERIES)} seed files written to data/seed/")
    if seeded:
        print("\nNext steps:")
        print("  git add data/seed/")
        print("  git commit -m 'seed: pre-scored jobs for Vercel'")
        print("  vercel --prod")


if __name__ == "__main__":
    asyncio.run(main())
