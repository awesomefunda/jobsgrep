#!/usr/bin/env python3
"""Generate the seed file for the live (PUBLIC/Vercel) deployment.

The live site runs in PUBLIC mode, where the /api/import (push) endpoint does
NOT persist. Instead it loads committed seed files from jobsgrep/seed_data/ on
every cold start. This script turns the local corpus cache into that seed.

Flow:
    jobsgrep run-prefetch          # build/refresh the local corpus
    python scripts/generate_seed.py
    git add jobsgrep/seed_data && git commit && git push   # → redeploy

By default it copies the existing corpus cache. Pass --refresh to re-fetch first.
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
SEED_DIR = ROOT / "jobsgrep" / "seed_data"


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate seed corpus for the live deploy")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-run the corpus fetch before generating the seed")
    parser.add_argument("--keep-scored", action="store_true",
                        help="Keep legacy scored__*.json seeds (default: remove them)")
    args = parser.parse_args()

    import os
    os.environ.setdefault("JOBSGREP_MODE", "LOCAL")
    from jobsgrep.config import get_settings
    get_settings.cache_clear()

    from jobsgrep.job_cache import _cache_dir
    from jobsgrep.prefetch import CORPUS_KEY, fetch_corpus

    if args.refresh:
        print("Refreshing corpus (run-prefetch)...")
        n = await fetch_corpus()
        print(f"  fetched {n} jobs")

    corpus_file = _cache_dir() / f"{CORPUS_KEY}.json"
    if not corpus_file.exists():
        print(f"No corpus cache at {corpus_file}.\nRun 'jobsgrep run-prefetch' first (or pass --refresh).")
        sys.exit(1)

    SEED_DIR.mkdir(parents=True, exist_ok=True)

    # Remove legacy per-query scored seeds (old architecture) unless asked to keep.
    if not args.keep_scored:
        removed = 0
        for old in SEED_DIR.glob("scored__*.json"):
            old.unlink(); removed += 1
        for old in SEED_DIR.glob("raw__*.json"):
            old.unlink(); removed += 1
        if removed:
            print(f"Removed {removed} stale seed file(s).")

    dst = SEED_DIR / "raw__corpus.json"
    shutil.copy(corpus_file, dst)
    data = json.loads(dst.read_text(encoding="utf-8"))
    size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"\nWrote {dst.relative_to(ROOT)} — {data.get('job_count', '?')} jobs ({size_mb:.1f} MB)")
    print("\nNext steps:")
    print("  git add jobsgrep/seed_data")
    print("  git commit -m 'seed: refresh live corpus'")
    print("  git push   # triggers redeploy")


if __name__ == "__main__":
    asyncio.run(main())
