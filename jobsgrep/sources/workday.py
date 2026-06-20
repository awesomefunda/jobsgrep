"""Workday public client-side jobs API scraper (Nvidia, AMD, Intel)."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.workday")

WORKDAY_TARGETS = [
    {
        "host": "nvidia.wd5.myworkdayjobs.com",
        "tenant": "nvidia",
        "career_site": "NVIDIAExternalCareerSite",
        "company_name": "Nvidia",
    },
    {
        "host": "intel.wd1.myworkdayjobs.com",
        "tenant": "intel",
        "career_site": "External",
        "company_name": "Intel",
    },
    {
        "host": "broadcom.wd1.myworkdayjobs.com",
        "tenant": "broadcom",
        "career_site": "External_Career",
        "company_name": "Broadcom",
    },
    {
        "host": "qualcomm.wd1.myworkdayjobs.com",
        "tenant": "qualcomm",
        "career_site": "External",
        "company_name": "Qualcomm",
    },
    {
        "host": "micron.wd1.myworkdayjobs.com",
        "tenant": "micron",
        "career_site": "External",
        "company_name": "Micron",
    },
    {
        "host": "zillowgroup.wd5.myworkdayjobs.com",
        "tenant": "zillowgroup",
        "career_site": "Zillow_Careers",
        "company_name": "Zillow",
    },
    {
        "host": "dataminr.wd1.myworkdayjobs.com",
        "tenant": "dataminr",
        "career_site": "Dataminr_Careers",
        "company_name": "Dataminr",
    },
    {
        "host": "uber.wd1.myworkdayjobs.com",
        "tenant": "uber",
        "career_site": "External",
        "company_name": "Uber",
    },
    {
        "host": "salesforce.wd1.myworkdayjobs.com",
        "tenant": "salesforce",
        "career_site": "External_Career_Site",
        "company_name": "Salesforce",
    },
    {
        "host": "analog.wd1.myworkdayjobs.com",
        "tenant": "analog",
        "career_site": "External",
        "company_name": "Analog Devices",
    },
    {
        "host": "appliedmaterials.wd1.myworkdayjobs.com",
        "tenant": "appliedmaterials",
        "career_site": "External",
        "company_name": "Applied Materials",
    },
    {
        "host": "lam.wd1.myworkdayjobs.com",
        "tenant": "lam",
        "career_site": "External",
        "company_name": "Lam Research",
    },
    {
        "host": "asml.wd1.myworkdayjobs.com",
        "tenant": "asml",
        "career_site": "External",
        "company_name": "ASML",
    },
    {
        "host": "texasinstruments.wd1.myworkdayjobs.com",
        "tenant": "texasinstruments",
        "career_site": "External",
        "company_name": "Texas Instruments",
    },
]


def parse_workday_date(posted_on: str) -> str:
    if not posted_on:
        return ""
    posted_on = posted_on.lower().strip()
    now = datetime.now(timezone.utc)
    if "today" in posted_on:
        return now.strftime("%Y-%m-%d")
    if "yesterday" in posted_on:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    match = re.search(r"(\d+)\s+days?\s+ago", posted_on)
    if match:
        days = int(match.group(1))
        return (now - timedelta(days=days)).strftime("%Y-%m-%d")
    return ""


class WorkdaySource(BaseSource):
    source_name = "workday"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        from ..discovery.company_list import get_mapping_cache
        cache = get_mapping_cache()
        dynamic_targets = []
        for m in cache.values():
            if m.workday_host and m.workday_tenant and m.workday_career_site:
                dynamic_targets.append({
                    "host": m.workday_host,
                    "tenant": m.workday_tenant,
                    "career_site": m.workday_career_site,
                    "company_name": m.company,
                })

        targets = list(WORKDAY_TARGETS)
        seen_hosts = {t["host"].lower() for t in targets}
        for dt in dynamic_targets:
            if dt["host"].lower() not in seen_hosts:
                targets.append(dt)

        results: list[RawJob] = []
        sem = asyncio.Semaphore(3)

        async def fetch_company(target: dict) -> list[RawJob]:
            async with sem:
                return await self._fetch_company_jobs(target, query)

        tasks = [fetch_company(t) for t in targets]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        return results

    async def _fetch_company_jobs(self, target: dict, query: ParsedQuery) -> list[RawJob]:
        host = target["host"]
        tenant = target["tenant"]
        site = target["career_site"]
        company_name = target["company_name"]

        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        jobs: list[RawJob] = []
        seen: set[str] = set()

        # Fetch up to 25 pages (limit 20 per page)
        for page in range(25):
            offset = page * 20
            payload = {
                "appliedFacets": {},
                "limit": 20,
                "offset": offset,
                "searchText": "",
            }
            try:
                resp = await self._post(url, json=payload)
                if resp.status_code != 200:
                    logger.debug("workday %s HTTP %d", company_name, resp.status_code)
                    break
                data = resp.json()
            except Exception as e:
                logger.debug("workday %s request failed: %s", company_name, e)
                break

            postings = data.get("jobPostings", [])
            if not postings:
                break

            for p in postings:
                title = p.get("title", "")
                if not title:
                    continue
                ext_path = p.get("externalPath", "")
                if not ext_path:
                    continue
                
                # Construct URL
                job_url = f"https://{host}/en-US/{site}{ext_path}"
                location = p.get("locationsText", "")
                posted_on = p.get("postedOn", "")
                date_posted = parse_workday_date(posted_on)

                rj = RawJob(
                    id=job_id(company_name, title, location),
                    title=title,
                    company=company_name,
                    location=location,
                    remote="remote" in location.lower() or "remote" in title.lower(),
                    url=job_url,
                    date_posted=date_posted,
                    source="workday",
                    source_type=DataSourceType.PUBLIC_API,
                )
                if self._keyword_match(rj, query) and rj.id not in seen:
                    seen.add(rj.id)
                    jobs.append(rj)

            if len(postings) < 20:
                break

        logger.info("workday: fetched %d jobs for %s", len(jobs), company_name)
        return jobs
