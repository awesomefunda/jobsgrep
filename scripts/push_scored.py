#!/usr/bin/env python3
"""Push locally-scored jobs to jobsgrep.com so Vercel serves them instantly.

Usage:
    # Push all scored cache entries to the live site
    python scripts/push_scored.py

    # Push to a specific host (default: https://jobsgrep.com)
    python scripts/push_scored.py --host https://jobsgrep.com

    # Dry run: show what would be pushed without sending
    python scripts/push_scored.py --dry-run

    # Push only entries whose label matches a pattern
    python scripts/push_scored.py --filter "bay area"

Auth:
    Set PUSH_TOKEN env var (must match the server's PUSH_TOKEN).
    Falls back to JOBSGREP_ACCESS_TOKEN.

Workflow:
    1. Run jobsgrep locally (LOCAL mode) to search + score with jobspy/LinkedIn/Indeed
    2. Run this script to push scored results to Vercel
    3. jobsgrep.com immediately serves the richer local results from cache
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Push scored jobs to jobsgrep.com")
    parser.add_argument("--host", default="https://jobsgrep.com", help="Target host")
    parser.add_argument("--filter", default="", help="Only push entries whose label contains this string (case-insensitive)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be pushed, don't send")
    parser.add_argument("--min-jobs", type=int, default=1, help="Skip entries with fewer than this many jobs")
    args = parser.parse_args()

    import os as _os
    _os.environ.setdefault("JOBSGREP_MODE", "LOCAL")

    from jobsgrep.config import get_settings
    get_settings.cache_clear()

    token = os.environ.get("PUSH_TOKEN") or os.environ.get("JOBSGREP_ACCESS_TOKEN", "")
    if not token:
        print("WARNING: no PUSH_TOKEN set — push will be rejected if server requires auth")

    from jobsgrep.job_cache import _scored_dir
    scored_dir = _scored_dir()

    entries = []
    for path in scored_dir.glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            label = entry.get("label", "")
            job_count = entry.get("job_count", len(entry.get("jobs", [])))
            if args.filter and args.filter.lower() not in label.lower():
                continue
            if job_count < args.min_jobs:
                continue
            entries.append((path, entry, label, job_count))
        except Exception as e:
            print(f"  skip {path.name}: {e}")

    if not entries:
        print("No scored cache entries found to push.")
        print(f"  Looked in: {scored_dir}")
        print("  Run a search locally first (JOBSGREP_MODE=LOCAL), then re-run this script.")
        return

    entries.sort(key=lambda x: x[3], reverse=True)  # largest first

    print(f"Found {len(entries)} scored cache entries to push to {args.host}")
    print()

    import urllib.request
    import urllib.error

    pushed = 0
    failed = 0

    for path, entry, label, job_count in entries:
        key = entry.get("key", path.stem)
        jobs = entry.get("jobs", [])

        # Restamp stored_at so TTL is fresh on server
        entry["stored_at"] = time.time()

        print(f"  [{label or key}]  {job_count} jobs", end="")

        if args.dry_run:
            print("  (dry-run, skipping)")
            continue

        payload = json.dumps({
            "key":   key,
            "label": label,
            "jobs":  jobs,
            "token": token,
        }).encode("utf-8")

        url = f"{args.host.rstrip('/')}/api/push-scored"
        req = urllib.request.Request(
            url, data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; jobsgrep-push/1.0)",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
                print(f"  -> stored {result.get('stored', '?')} jobs")
                pushed += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"  -> FAILED {e.code}: {body[:120]}")
            failed += 1
        except Exception as e:
            print(f"  -> ERROR: {e}")
            failed += 1

    print()
    if args.dry_run:
        print(f"Dry run complete — would push {len(entries)} entries.")
    else:
        print(f"Done: {pushed} pushed, {failed} failed.")
        if pushed:
            print(f"\njobsgrep.com will now serve {pushed} richer local result sets from cache.")


if __name__ == "__main__":
    main()
