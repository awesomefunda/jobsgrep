"""Manages ~/.jobsgrep/company_ats_mapping.json."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import get_settings
from ..models import ATSMapping
from .ats_prober import CONCURRENT_PROBES, probe_company

logger = logging.getLogger("jobsgrep.discovery")

YC_API_URL = "https://yc-oss.github.io/api/companies/all.json"


def _mapping_path() -> Path:
    return get_settings().data_dir / "company_ats_mapping.json"


def _load_raw() -> dict[str, dict]:
    path = _mapping_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_raw(data: dict[str, dict]) -> None:
    path = _mapping_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_mapping_cache() -> dict[str, ATSMapping]:
    raw = _load_raw()
    result = {}
    for company_lower, v in raw.items():
        try:
            result[company_lower] = ATSMapping(**v)
        except Exception:
            pass
    return result


def upsert_mapping(mapping: ATSMapping) -> None:
    """Add or update a mapping. Idempotent: never removes existing slugs."""
    raw = _load_raw()
    key = mapping.company.lower()
    existing = raw.get(key, {})

    # Never remove existing slugs
    merged = {
        "company": mapping.company,
        "greenhouse_slug": mapping.greenhouse_slug or existing.get("greenhouse_slug"),
        "lever_slug": mapping.lever_slug or existing.get("lever_slug"),
        "ashby_slug": mapping.ashby_slug or existing.get("ashby_slug"),
        "recruitee_slug": mapping.recruitee_slug or existing.get("recruitee_slug"),
        "workable_slug": mapping.workable_slug or existing.get("workable_slug"),
        "website": mapping.website or existing.get("website"),
        "is_yc": mapping.is_yc or existing.get("is_yc", False),
        "team_size": mapping.team_size or existing.get("team_size"),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
    raw[key] = merged
    _save_raw(raw)


async def discover_from_yc(limit: int = 500) -> int:
    """Probe YC hiring companies and save mappings. Returns count of new mappings found."""
    settings = get_settings()
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
        resp = await client.get(YC_API_URL, timeout=30)
        resp.raise_for_status()
        companies = resp.json()

    hiring = [c for c in companies if c.get("isHiring")][:limit]
    logger.info("probing %d YC hiring companies", len(hiring))

    existing = get_mapping_cache()
    to_probe = [c for c in hiring if c.get("name", "").lower() not in existing]
    logger.info("%d companies not yet in cache — probing", len(to_probe))

    sem = asyncio.Semaphore(CONCURRENT_PROBES)
    found = 0

    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
        async def probe_one(company_data: dict) -> None:
            nonlocal found
            async with sem:
                name = company_data.get("name", "")
                mapping = await probe_company(name, client, settings)
                mapping.is_yc = True
                mapping.website = company_data.get("url", "")
                mapping.team_size = str(company_data.get("team_size", "")) if company_data.get("team_size") else None
                if mapping.greenhouse_slug or mapping.lever_slug or mapping.ashby_slug:
                    upsert_mapping(mapping)
                    found += 1

        await asyncio.gather(*[probe_one(c) for c in to_probe])

    logger.info("discovery complete: %d new ATS mappings found", found)
    return found
