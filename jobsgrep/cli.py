"""CLI entry point: jobsgrep serve | search | discover | sources | health | add-company."""
from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jobsgrep",
        description="JobsGrep — legal-first job search aggregator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    p_serve = sub.add_parser("serve", help="Start the web server")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--reload", action="store_true")

    # search
    p_search = sub.add_parser("search", help="CLI-only search, outputs Excel directly")
    p_search.add_argument("query", nargs="+")
    p_search.add_argument("--out", default=".")
    p_search.add_argument("--no-score", action="store_true", help="Skip AI scoring")

    # discover
    p_discover = sub.add_parser("discover", help="Run ATS slug discovery for all YC companies")
    p_discover.add_argument("--limit", type=int, default=500)

    # sources
    sub.add_parser("sources", help="List enabled sources for current mode")

    # health
    sub.add_parser("health", help="Check all source APIs are responding")

    # add-company
    p_add = sub.add_parser("add-company", help="Manually add a company ATS mapping")
    p_add.add_argument("name")
    p_add.add_argument("ats", choices=["greenhouse", "lever", "ashby", "recruitee", "workable"])
    p_add.add_argument("slug")

    # run-prefetch
    p_prefetch = sub.add_parser(
        "run-prefetch",
        help="Fetch + score jobs for common queries and populate the local cache",
    )
    p_prefetch.add_argument(
        "--queries", default="",
        help="Comma-separated override (default: built-in 10-query list)",
    )
    p_prefetch.add_argument(
        "--first-only", action="store_true",
        help="Only warm up 'Software Engineer' — fastest initial run",
    )
    p_prefetch.add_argument(
        "--no-score", action="store_true", help="Skip AI scoring"
    )

    # push
    p_push = sub.add_parser(
        "push",
        help="Push cached jobs from this machine to a remote JobsGrep server",
    )
    p_push.add_argument("--server", required=True,
                        help="Remote server base URL, e.g. https://jobs.example.com")
    p_push.add_argument("--token", default="",
                        help="Push token (JOBSGREP_ACCESS_TOKEN on the remote server)")
    p_push.add_argument("--query", default="",
                        help="Only push cache entries matching this label substring")
    p_push.add_argument("--dry-run", action="store_true",
                        help="Show what would be pushed without actually sending")

    args = parser.parse_args()

    # Configure logging before running any command
    from .config import get_settings
    from .logging_config import setup_logging
    _s = get_settings()
    setup_logging(
        mode=_s.jobsgrep_mode.value,
        log_dir=_s.data_dir / "logs",
        log_level=_s.log_level,
    )

    if args.command == "serve":
        _cmd_serve(args)
    elif args.command == "search":
        asyncio.run(_cmd_search(args))
    elif args.command == "discover":
        asyncio.run(_cmd_discover(args))
    elif args.command == "sources":
        _cmd_sources()
    elif args.command == "health":
        asyncio.run(_cmd_health())
    elif args.command == "add-company":
        _cmd_add_company(args)
    elif args.command == "run-prefetch":
        asyncio.run(_cmd_run_prefetch(args))
    elif args.command == "push":
        asyncio.run(_cmd_push(args))


def _cmd_serve(args) -> None:
    import uvicorn
    from .config import get_settings
    settings = get_settings()
    uvicorn.run(
        "jobsgrep.main:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
        log_level="info",
    )


async def _cmd_search(args) -> None:
    import shutil
    from pathlib import Path
    from .main import _run_search, _tasks
    from .models import SearchTask, TaskStatus

    query = " ".join(args.query)
    task_id = "cli-search"
    task = SearchTask(task_id=task_id, query=query, skip_scoring=args.no_score)
    _tasks[task_id] = task

    sys.stderr.write(f"Searching: {query} (skip_scoring={args.no_score})\n"); sys.stderr.flush()
    await _run_search(task_id, query, None, skip_scoring=args.no_score)

    task = _tasks[task_id]
    if task.status in (TaskStatus.COMPLETE, TaskStatus.SEARCH_COMPLETE):
        report_path = getattr(task, "_report_path", None)
        sys.stderr.write(f"Found: {task.total_jobs_found}  Scored: {task.total_jobs_scored}\n"); sys.stderr.flush()
        if report_path:
            src = Path(report_path)
            out_dir = Path(args.out).expanduser().resolve()
            dest = out_dir / src.name
            if src.resolve() != dest:
                out_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(str(src), str(dest))
            sys.stderr.write(f"Report: {dest}\n"); sys.stderr.flush()
        else:
            sys.stderr.write("No matches above threshold.\n"); sys.stderr.flush()
    else:
        sys.stderr.write(f"Search failed: {task.error}\n"); sys.stderr.flush()
        sys.exit(1)


async def _cmd_discover(args) -> None:
    from .discovery.company_list import discover_from_yc
    print(f"Discovering ATS slugs for up to {args.limit} YC companies...")
    count = await discover_from_yc(limit=args.limit)
    print(f"Done. {count} new mappings found.")


def _cmd_sources() -> None:
    from .config import get_enabled_sources, get_settings
    settings = get_settings()
    enabled = get_enabled_sources()
    print(f"\nEnabled sources for {settings.jobsgrep_mode.value} mode:\n")
    for name, meta in enabled.items():
        print(f"  {name:<20} [{meta.source_type.value}]  {meta.description}")
    print()


async def _cmd_health() -> None:
    import httpx
    from .config import get_settings
    settings = get_settings()
    probes = {
        "greenhouse": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
        "lever": "https://api.lever.co/v0/postings/linear?mode=json",
        "hn_algolia": "https://hn.algolia.com/api/v1/search_by_date?tags=ask_hn&query=who+is+hiring&hitsPerPage=1",
        "yc_oss": "https://yc-oss.github.io/api/companies/all.json",
    }
    print("\nChecking source APIs...\n")
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=10) as client:
        for name, url in probes.items():
            try:
                r = await client.get(url)
                status = "OK" if r.status_code == 200 else f"HTTP {r.status_code}"
            except Exception as e:
                status = f"ERROR: {e}"
            icon = "✓" if status == "OK" else "✗"
            print(f"  {icon} {name:<20} {status}")
    print()


async def _cmd_run_prefetch(args) -> None:
    """Fetch + score jobs for common queries and write to local cache."""
    from .prefetch import run_prefetch_cycle, _DEFAULT_QUERIES

    if args.first_only:
        queries = ["Software Engineer"]
    elif args.queries:
        queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    else:
        queries = _DEFAULT_QUERIES

    print(f"\nPrefetching {len(queries)} quer{'y' if len(queries)==1 else 'ies'} (skip_scoring={args.no_score}):")
    for q in queries:
        print(f"  · {q}")
    print()

    await run_prefetch_cycle(queries, stagger_seconds=20.0, skip_scoring=args.no_score)
    print("\nDone. Run 'jobsgrep push' to upload results to a remote server.")


def _cmd_add_company(args) -> None:
    from .discovery.company_list import upsert_mapping
    from .models import ATSMapping

    mapping = ATSMapping(company=args.name)
    setattr(mapping, f"{args.ats}_slug", args.slug)
    upsert_mapping(mapping)
    print(f"Added: {args.name} → {args.ats}:{args.slug}")


async def _cmd_push(args) -> None:
    """Push all local cache entries to a remote JobsGrep server via POST /api/import."""
    import json
    import httpx
    from .job_cache import list_entries, _cache_dir

    entries = list_entries()
    if not entries:
        print("No cache entries found. Run a search first.")
        return

    # Filter by label if --query provided
    if args.query:
        entries = [e for e in entries if args.query.lower() in e.get("label", "").lower()]
        if not entries:
            print(f"No cache entries match label '{args.query}'.")
            return

    server = args.server.rstrip("/")
    token  = args.token
    url    = f"{server}/api/import"

    print(f"\nPushing {len(entries)} cache entr{'y' if len(entries)==1 else 'ies'} → {server}\n")

    pushed = 0
    skipped = 0
    for entry in entries:
        key   = entry["key"]
        label = entry.get("label", key)
        count = entry.get("job_count", 0)

        # Load full job payload from disk
        cache_file = _cache_dir() / f"{key}.json"
        if not cache_file.exists():
            print(f"  ! {label:<40} file missing, skipping")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  ~ {label:<40} {count} jobs (dry run)")
            continue

        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            body = {
                "key":   key,
                "label": label,
                "jobs":  payload.get("jobs", []),
                "token": token,
            }
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, json=body)
            if r.status_code == 200:
                resp = r.json()
                print(f"  ✓ {label:<40} {resp.get('stored', '?')} jobs stored")
                pushed += 1
            elif r.status_code == 403:
                print(f"  ✗ {label:<40} 403 Forbidden — check --token")
                skipped += 1
                break   # No point retrying with wrong token
            else:
                print(f"  ✗ {label:<40} HTTP {r.status_code}: {r.text[:120]}")
                skipped += 1
        except Exception as e:
            print(f"  ✗ {label:<40} error: {e}")
            skipped += 1

    if not args.dry_run:
        print(f"\nDone. {pushed} pushed, {skipped} skipped.")
