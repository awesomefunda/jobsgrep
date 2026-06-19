#!/usr/bin/env python3
"""Append the current corpus to a cumulative job archive committed to GitHub.

The live seed is open-only and gets overwritten each refresh, so jobs that close
or age out would otherwise be lost. This script maintains a growing historical
archive (deduped by job id) with first_seen / last_seen dates.

Run after a prefetch:
    jobsgrep run-prefetch
    python scripts/archive_corpus.py
    git add data/archive && git commit -m "archive: <date>" && git push

The archive lives in data/archive/ (NOT in the package's seed_data/), so it is
never bundled into the Vercel function. It is gzipped to keep the repo small.
"""
import gzip
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
ARCHIVE = ROOT / "data" / "archive" / "jobs.json.gz"


def _read_gz(path: Path) -> list:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _write_gz(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)


def main() -> None:
    import os
    os.environ.setdefault("JOBSGREP_MODE", "LOCAL")
    from jobsgrep.config import get_settings
    get_settings.cache_clear()
    from jobsgrep.job_cache import _cache_dir
    from jobsgrep.prefetch import CORPUS_KEY

    corpus_file = _cache_dir() / f"{CORPUS_KEY}.json"
    if not corpus_file.exists():
        print(f"No corpus at {corpus_file}. Run 'jobsgrep run-prefetch' first.")
        sys.exit(1)

    corpus = json.loads(corpus_file.read_text(encoding="utf-8")).get("jobs", [])
    today = date.today().isoformat()

    existing = _read_gz(ARCHIVE)
    by_id = {e.get("id"): e for e in existing if e.get("id")}
    before = len(by_id)

    new_count = 0
    for job in corpus:
        jid = job.get("id")
        if not jid:
            continue
        if jid in by_id:
            by_id[jid]["last_seen"] = today
        else:
            entry = dict(job)
            entry["first_seen"] = today
            entry["last_seen"] = today
            by_id[jid] = entry
            new_count += 1

    archive = list(by_id.values())
    _write_gz(ARCHIVE, archive)
    size_mb = ARCHIVE.stat().st_size / (1024 * 1024)
    print(f"Archive: {len(archive)} total jobs (+{new_count} new this run, "
          f"was {before}). {size_mb:.1f} MB gzipped -> {ARCHIVE.relative_to(ROOT)}")
    print("\nNext: git add data/archive && git commit -m 'archive: %s' && git push" % today)


if __name__ == "__main__":
    main()
