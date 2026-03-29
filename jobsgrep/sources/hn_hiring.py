"""Hacker News 'Who's Hiring' via Algolia + Firebase APIs."""
from __future__ import annotations

import asyncio
import logging
import re

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.hn_hiring")

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# "Company | Role | Location | Remote/Onsite" is a common format
_PIPE_RE = re.compile(r"^([^|]+)\|([^|]+)\|([^|\n]+)", re.MULTILINE)


def _parse_comment(comment_text: str) -> dict | None:
    """Try to extract company, title, location from a Who's Hiring comment."""
    m = _PIPE_RE.search(comment_text)
    if m:
        company = m.group(1).strip()
        title = m.group(2).strip()
        location = m.group(3).strip()
        return {"company": company, "title": title, "location": location}
    # Fallback: first line usually has the most info
    first_line = comment_text.split("\n")[0].strip()
    if "|" in first_line:
        parts = [p.strip() for p in first_line.split("|")]
        if len(parts) >= 2:
            return {"company": parts[0], "title": parts[1],
                    "location": parts[2] if len(parts) > 2 else ""}
    return None


class HNHiringSource(BaseSource):
    source_name = "hn_hiring"

    ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
    FIREBASE_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        # Find the latest "Who is Hiring" post
        story_id = await self._find_latest_hiring_post()
        if not story_id:
            logger.warning("could not find latest HN Who's Hiring post")
            return []

        comments = await self._fetch_comments(story_id)
        return self._parse_jobs(comments, query, story_id)

    async def _find_latest_hiring_post(self) -> int | None:
        try:
            resp = await self._get(
                self.ALGOLIA_URL,
                params={"tags": "ask_hn,story", "query": "who is hiring", "hitsPerPage": 5},
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            for hit in hits:
                title = hit.get("title", "").lower()
                if "who is hiring" in title:
                    return int(hit["objectID"])
        except Exception as e:
            logger.warning("hn hiring story lookup failed: %s", e)
        return None

    async def _fetch_comments(self, story_id: int) -> list[dict]:
        try:
            resp = await self._get(self.FIREBASE_ITEM_URL.format(item_id=story_id))
            resp.raise_for_status()
            story = resp.json()
        except Exception as e:
            logger.warning("hn story fetch failed: %s", e)
            return []

        kid_ids = story.get("kids", [])[:200]  # top 200 comments
        sem = asyncio.Semaphore(10)

        async def fetch_comment(kid_id: int) -> dict | None:
            async with sem:
                try:
                    r = await self._get(self.FIREBASE_ITEM_URL.format(item_id=kid_id))
                    if r.status_code == 200:
                        return r.json()
                except Exception:
                    pass
                return None

        results = await asyncio.gather(*[fetch_comment(k) for k in kid_ids])
        return [r for r in results if r and not r.get("dead") and not r.get("deleted")]

    def _parse_jobs(self, comments: list[dict], query: ParsedQuery, story_id: int) -> list[RawJob]:
        jobs = []
        story_url = f"https://news.ycombinator.com/item?id={story_id}"

        for comment in comments:
            text = comment.get("text", "") or ""
            # Clean HTML entities
            text = text.replace("&#x27;", "'").replace("&amp;", "&").replace("<p>", "\n").replace("</p>", "\n")
            # Strip remaining HTML tags
            text = re.sub(r"<[^>]+>", " ", text)

            parsed = _parse_comment(text)
            if not parsed:
                continue

            comment_url = f"https://news.ycombinator.com/item?id={comment.get('id', '')}"
            email_match = _EMAIL_RE.search(text)
            description = text[:2000]

            rj = RawJob(
                id=job_id(parsed["company"], parsed["title"], parsed["location"]),
                title=parsed["title"],
                company=parsed["company"],
                location=parsed["location"],
                remote="remote" in text.lower(),
                url=comment_url,
                description=description,
                source="hn_hiring",
                source_type=DataSourceType.OFFICIAL_API,
                raw={"email": email_match.group(0) if email_match else "", "story_url": story_url},
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
