"""Unified LLM provider interface — Claude → Gemini → Groq(70b) → Groq(8b) fallback chain.

Usage:
    from jobsgrep.llm import complete
    text = await complete(prompt="...", system="...")
"""
from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger("jobsgrep.llm")

# Circuit breaker: providers that hit non-retryable errors (quota exhausted) are
# skipped for the rest of the process lifetime to avoid wasting time on every call.
_dead_providers: set[str] = set()


async def complete(
    prompt: str,
    system: str = "",
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> str | None:
    """Call the best available LLM provider. Returns raw text or None on total failure."""
    from .config import get_settings
    settings = get_settings()

    if settings.anthropic_api_key and "claude" not in _dead_providers:
        result = await _claude(settings.anthropic_api_key, system, prompt, temperature, max_tokens)
        if result:
            return result

    if settings.gemini_api_key and "gemini" not in _dead_providers:
        result = await _gemini(settings.gemini_api_key, system, prompt, temperature, max_tokens)
        if result:
            return result

    if settings.groq_api_key:
        for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
            if model in _dead_providers:
                continue
            result = await _groq(settings.groq_api_key, model, system, prompt, temperature, max_tokens)
            if result:
                return result

    logger.error("all LLM providers failed — set GEMINI_API_KEY or GROQ_API_KEY")
    return None


async def _claude(api_key: str, system: str, prompt: str, temperature: float, max_tokens: int) -> str | None:
    try:
        import anthropic  # type: ignore
        client = anthropic.AsyncAnthropic(api_key=api_key)
        kwargs: dict = dict(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system
        resp = await client.messages.create(**kwargs)
        return resp.content[0].text
    except ImportError:
        logger.debug("anthropic not installed — skipping Claude")
        _dead_providers.add("claude")
        return None
    except Exception as e:
        logger.warning("claude failed: %s", e)
        return None


async def _gemini(api_key: str, system: str, prompt: str, temperature: float, max_tokens: int) -> str | None:
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
        client = genai.Client(api_key=api_key)
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-2.0-flash",
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            ),
        )
        return resp.text
    except ImportError:
        logger.debug("google-genai not installed — skipping Gemini")
        _dead_providers.add("gemini")
        return None
    except Exception as e:
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
            if "PerDay" in msg or "per_day" in msg.lower() or "daily" in msg.lower():
                logger.warning("gemini daily quota exhausted — skipping for this run")
                _dead_providers.add("gemini")
            else:
                logger.warning("gemini rate-limited, trying next provider")
        else:
            logger.warning("gemini failed: %s", e)
        return None


async def _groq(api_key: str, model: str, system: str, prompt: str, temperature: float, max_tokens: int) -> str | None:
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except Exception as e:
        msg = str(e).lower()
        if "rate_limit" in msg or "429" in msg:
            logger.warning("groq %s rate-limited, trying next provider", model)
        else:
            logger.warning("groq %s failed: %s", model, e)
        return None


def strip_fences(text: str) -> str:
    """Strip markdown code fences that some models wrap around JSON."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()
