"""TeamBlind job board via their encrypted REST API (SJCL AES-CCM + RSA PKCS1v15).

Classified as SCRAPER — reverse-engineered encryption protocol.
Enabled in LOCAL mode only.

Protocol (from /_next/static/chunks/*.js):
  1. Generate random 256-bit hex session key.
  2. sjcl.encrypt(hexKey, '{}') → AES-CCM/PBKDF2 JSON blob.
  3. RSA-PKCS1v15 encrypt hex key with site's 1024-bit public key.
  4. POST {payload, encClientKey}; filters go in URL query params.
  5. Decrypt response: sjcl.decrypt(hexKey, json.loads(body)).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.teamblind")

_JOBS_PER_PAGE = 50

_RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBITANBgkqhkiG9w0BAQEFAAOCAQ4AMIIBCQKCAQBOBw7Q2T0Wmb/qNPuNbk+f
ZWRbKgBwikJa2vJ5Ht+quwhLbvpUVOKwlNM93huIzkM5wWTRoVpLmczfCt3CyxBd
eU5PxY8JhXxHch/h41e/AgKXrOPFDJuH5T2V++Zw21ArC6rk3YFScNH9xOa0YXfY
x2RQxLM7hD7Bzy5mtxN5nqULxDhYWTeZT6aQw9Wii/0HBoePqgW77TpXcgQxJ5AP
bQQ7QlGdAFMWgjhFWret7cffGrd2lFn5RCgMU316UKf2CTkB4orcsiqCYJ76+LZJ
jLT7kk0ZWYk8Xnn7uwpiCMVipOmZS7cmX3MWiRhbQqkw1UGi2SWn2Ov7plwgx9CB
AgMBAAE=
-----END PUBLIC KEY-----"""

_SJCL_L         = 2
_SJCL_NONCE_LEN = 15 - _SJCL_L   # 13-byte nonce for AES-CCM
_rsa_key_cache  = None


# ─── SJCL crypto helpers ─────────────────────────────────────────────────────

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode().rstrip("=")

def _b64d(s: str) -> bytes:
    return base64.b64decode(s + "=" * ((-len(s)) % 4))

def _sjcl_encrypt(hex_key: str, plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    salt = os.urandom(8)
    iv   = os.urandom(_SJCL_NONCE_LEN)
    kdf  = PBKDF2HMAC(algorithm=hashes.SHA256(), length=16, salt=salt, iterations=10000)
    ct   = AESCCM(kdf.derive(hex_key.encode()), tag_length=8).encrypt(iv, plaintext.encode(), b"")
    return json.dumps({
        "iv": _b64e(iv), "v": 1, "iter": 10000, "ks": 128, "ts": 64,
        "mode": "ccm", "adata": "", "cipher": "aes",
        "salt": _b64e(salt), "ct": _b64e(ct),
    }, separators=(",", ":"))

def _sjcl_decrypt(hex_key: str, sjcl_str: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    d    = json.loads(sjcl_str)
    salt = _b64d(d["salt"])
    iv   = _b64d(d["iv"])[:_SJCL_NONCE_LEN]
    ct   = _b64d(d["ct"])
    kdf  = PBKDF2HMAC(algorithm=hashes.SHA256(), length=d.get("ks", 128) // 8,
                      salt=salt, iterations=d.get("iter", 10000))
    return AESCCM(kdf.derive(hex_key.encode()), tag_length=d.get("ts", 64) // 8).decrypt(iv, ct, b"").decode()

def _rsa_encrypt(hex_key: str) -> str:
    global _rsa_key_cache
    from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    if _rsa_key_cache is None:
        _rsa_key_cache = load_pem_public_key(_RSA_PUBLIC_KEY.strip().encode())
    return base64.b64encode(
        _rsa_key_cache.encrypt(hex_key.encode(), rsa_padding.PKCS1v15())
    ).decode()

def _encrypted_fetch_sync(path: str, client_headers: dict) -> dict | None:
    """Synchronous encrypted fetch — run in executor."""
    import requests
    hex_key        = os.urandom(32).hex()
    encrypted_body = _sjcl_encrypt(hex_key, "{}")
    enc_client_key = _rsa_encrypt(hex_key)

    try:
        resp = requests.post(
            f"https://www.teamblind.com{path}",
            data=json.dumps({"payload": encrypted_body, "encClientKey": enc_client_key}),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, */*",
                "Origin": "https://www.teamblind.com",
                "Referer": "https://www.teamblind.com/jobs",
                **client_headers,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.debug("teamblind %s → HTTP %d", path, resp.status_code)
            return None
        inner = json.loads(resp.text)
        return json.loads(_sjcl_decrypt(hex_key, inner))
    except Exception as e:
        logger.debug("teamblind encrypted fetch failed: %s", e)
        return None


# ─── Source ──────────────────────────────────────────────────────────────────

class TeamBlindSource(BaseSource):
    source_name = "teamblind"

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        search_terms = list(dict.fromkeys((query.titles or []) + ["software engineer"]))[:3]
        jobs: list[RawJob] = []
        seen_ids: set[str] = set()
        loop = asyncio.get_event_loop()

        for term in search_terms:
            for page in range(4):
                offset = page * _JOBS_PER_PAGE
                params = f"searchKeyword={__import__('urllib.parse', fromlist=['']).parse.quote(term)}&page={page}&offset={offset}"
                if query.remote_ok:
                    params += "&remoteOnly=true"

                data = await loop.run_in_executor(
                    None,
                    lambda p=params: _encrypted_fetch_sync(f"/api/jobs?{p}", self._HEADERS),
                )
                if not data or "feeds" not in data:
                    break

                for item in data.get("feeds", []):
                    jid = str(item.get("id", ""))
                    if not jid or jid in seen_ids:
                        continue
                    seen_ids.add(jid)

                    title    = item.get("title", "").strip()
                    company  = item.get("companyName", "").strip()
                    location = item.get("location", "").strip()
                    highlights = item.get("highlights", [])

                    # Parse salary from highlights e.g. "$176K-$264K"
                    sal_min = sal_max = ""
                    sal_text = ""
                    for h in highlights:
                        if h.startswith("$") and "-" in h:
                            sal_text = h
                            parts = h.replace("$", "").replace("K", "000").split("-")
                            try:
                                sal_min = str(int(float(parts[0].replace(",", ""))))
                                sal_max = str(int(float(parts[1].replace(",", ""))))
                            except (ValueError, IndexError):
                                pass
                            break

                    skills_str = ", ".join(h for h in highlights if not h.startswith("$") and h.lower() != "remote")
                    is_remote  = "remote" in location.lower() or any(h.lower() == "remote" for h in highlights)

                    rj = RawJob(
                        id=f"tblind_{jid}",
                        title=title,
                        company=company,
                        location=location,
                        remote=is_remote,
                        url=f"https://www.teamblind.com/jobs/{jid}",
                        description=f"Skills: {skills_str}" if skills_str else "",
                        salary_text=sal_text,
                        salary_min=float(sal_min) if sal_min else None,
                        salary_max=float(sal_max) if sal_max else None,
                        source="teamblind",
                        source_type=DataSourceType.SCRAPER,
                    )
                    if self._keyword_match(rj, query):
                        jobs.append(rj)

                if not data.get("hasMore", False):
                    break

        logger.info("teamblind: %d jobs", len(jobs))
        return jobs
