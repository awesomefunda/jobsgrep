"""Levels.fyi job listings via their encrypted REST API (AES-ECB + zlib).

Classified as SCRAPER because it reverse-engineers the levels.fyi API encryption.
Enabled in LOCAL mode only.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import zlib

import httpx

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.levels_fyi")

_API_URL = "https://api.levels.fyi/v1/job/search"
_JOBS_PER_PAGE = 25

# AES-ECB key derived from the hardcoded levels.fyi secret
def _make_key() -> bytes:
    return base64.b64encode(hashlib.md5(b"levelstothemoon!!").digest()).decode("ascii")[:16].encode()


def _decrypt(payload_b64: str) -> dict:
    """Decrypt levels.fyi API response: AES-ECB → zlib → JSON."""
    try:
        from Cryptodome.Cipher import AES
    except ImportError:
        from Crypto.Cipher import AES  # type: ignore
    ct = base64.b64decode(payload_b64)
    raw = AES.new(_make_key(), AES.MODE_ECB).decrypt(ct)
    return __import__("json").loads(zlib.decompress(raw).decode("utf-8"))


def _location_slug(query: ParsedQuery) -> str:
    combined = " ".join(query.locations).lower()
    if "san francisco" in combined or "bay area" in combined:
        return "san-francisco-bay-area"
    if "new york" in combined or "nyc" in combined:
        return "new-york-city"
    if "seattle" in combined:
        return "seattle"
    if "austin" in combined:
        return "austin"
    return "united-states"


class LevelsFYISource(BaseSource):
    source_name = "levels_fyi"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        loc_slug = _location_slug(query)
        work_arrangements = ["remote"] if query.remote_ok else ["remote", "hybrid", "office"]
        search_terms = list(dict.fromkeys((query.titles or []) + ["Software Engineer"]))[:3]

        jobs: list[RawJob] = []
        seen_ids: set[str] = set()

        for term in search_terms:
            logger.info("levels.fyi: searching '%s' in %s", term, loc_slug)
            for page in range(4):  # up to 100 jobs per term
                offset = page * _JOBS_PER_PAGE
                results, total = await self._api_search(term, loc_slug, work_arrangements, offset)
                if not results:
                    break
                if page == 0:
                    logger.info("levels.fyi: %d total matching for '%s'", total, term)

                for company_group in results:
                    company = company_group.get("companyName", "")
                    for j in company_group.get("jobs", []):
                        jid = str(j.get("id", ""))
                        if not jid or jid in seen_ids:
                            continue
                        seen_ids.add(jid)

                        title = j.get("title", "")
                        locs = j.get("locations", [])
                        loc_str = locs[0] if locs else loc_slug.replace("-", " ").title()
                        arrangement = j.get("workArrangement", "")
                        min_base = j.get("minBaseSalary") or ""
                        max_base = j.get("maxBaseSalary") or ""
                        apply_url = j.get("applicationUrl") or f"https://www.levels.fyi/jobs?searchText={term}"
                        posted = (j.get("postingDate") or "")[:10]
                        salary_text = f"${min_base}–${max_base}" if min_base and max_base else ""

                        rj = RawJob(
                            id=job_id(company, title, jid),
                            title=title,
                            company=company,
                            location=loc_str,
                            remote=arrangement == "remote",
                            url=apply_url,
                            description=f"Levels.fyi: {title} at {company}",
                            salary_text=salary_text,
                            salary_min=float(min_base) if min_base else None,
                            salary_max=float(max_base) if max_base else None,
                            date_posted=posted,
                            source="levels_fyi",
                            source_type=DataSourceType.SCRAPER,
                        )
                        if self._keyword_match(rj, query):
                            jobs.append(rj)

                if offset + _JOBS_PER_PAGE >= min(total, 100):
                    break

        logger.info("levels.fyi: %d jobs from %d search terms", len(jobs), len(search_terms))
        return jobs

    # levels.fyi requires browser-like headers — rejects generic User-Agents
    _LEVELS_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, */*",
        "Referer": "https://www.levels.fyi/",
        "Origin": "https://www.levels.fyi",
    }

    async def _api_search(
        self,
        search_text: str,
        location_slug: str,
        work_arrangements: list[str],
        offset: int,
    ) -> tuple[list, int]:
        params: list[tuple[str, str]] = [
            ("searchText", search_text),
            ("locationSlugs[]", location_slug),
            ("limit", str(_JOBS_PER_PAGE)),
            ("offset", str(offset)),
            ("sortBy", "date_published"),
            ("postedAfterValue", "30"),
            ("postedAfterTimeType", "days"),
        ]
        for wa in work_arrangements:
            params.append(("workArrangements[]", wa))

        try:
            resp = await self.client.get(
                _API_URL,
                params=params,
                headers=self._LEVELS_HEADERS,
            )
            if resp.status_code != 200:
                logger.warning("levels.fyi API returned HTTP %d", resp.status_code)
                return [], 0
            data = resp.json()
            payload = data.get("payload", "")
            if not payload:
                logger.warning("levels.fyi: empty payload in response (API may have changed)")
                return [], 0
            decoded = await asyncio.get_event_loop().run_in_executor(None, _decrypt, payload)
            return decoded.get("results", []), decoded.get("totalMatchingJobs", 0)
        except Exception as e:
            logger.warning("levels.fyi api error: %s", e)
            return [], 0
