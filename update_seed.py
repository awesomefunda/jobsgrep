"""Copy fresh scored-cache entries into jobsgrep/seed_data/ for deployment.

Run this after `jobsgrep run-prefetch` to bake the latest job data into the
next Vercel deploy.

Usage:
    python update_seed.py
"""
import json
import shutil
from pathlib import Path

SEED_DIR = Path(__file__).parent / "jobsgrep" / "seed_data"
SCORED_DIR = Path.home() / ".jobsgrep" / "scored_cache"


def main():
    if not SCORED_DIR.exists():
        print(f"No scored cache found at {SCORED_DIR}")
        print("Run `jobsgrep run-prefetch` first.")
        return

    files = list(SCORED_DIR.glob("*.json"))
    if not files:
        print(f"Scored cache is empty at {SCORED_DIR}")
        print("Run `jobsgrep run-prefetch` first.")
        return

    SEED_DIR.mkdir(parents=True, exist_ok=True)

    # Clear old seed files
    old = list(SEED_DIR.glob("scored__*.json"))
    for f in old:
        f.unlink()
    print(f"Removed {len(old)} old seed file(s).")

    # Copy new scored files
    copied = 0
    total_jobs = 0
    for src in sorted(files):
        dst = SEED_DIR / f"scored__{src.name}"
        shutil.copy2(src, dst)
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            count = data.get("job_count", "?")
            label = data.get("label", src.stem)
            total_jobs += int(count) if isinstance(count, int) else 0
            print(f"  [OK] {label:<45} {count} jobs")
        except Exception:
            print(f"  [OK] {src.name}")
        copied += 1

    print(f"\nDone. {copied} file(s) -> jobsgrep/seed_data/  ({total_jobs} total jobs)")
    print("\nNext steps:")
    print("  git add jobsgrep/seed_data/")
    print("  git commit -m 'chore: refresh job seed data'")
    print("  git push")


if __name__ == "__main__":
    main()
