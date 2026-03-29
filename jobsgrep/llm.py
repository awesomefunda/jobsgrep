"""Unified LLM provider interface — Gemini → Groq(70b) → Groq(8b) fallback chain.

Usage:
    from jobsgrep.llm import complete
    text = await complete(prompt="...", system="...")
"""
from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger("jobsgrep.llm")


async def complete(
    prompt: str,
    system: str = "",
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> str | None:
    """Call the best available LLM provider. Returns raw text or None on total failure."""
    from .config import get_settings
    settings = get_settings()

    if settings.gemini_api_key:
        result = await _gemini(settings.gemini_api_key, system, prompt, temperature, max_tokens)
        if result:
            return result

    if settings.groq_api_key:
        for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
            result = await _groq(settings.groq_api_key, model, system, prompt, temperature, max_tokens)
            if result:
                return result

    logger.error("all LLM providers failed — set GEMINI_API_KEY or GROQ_API_KEY")
    return None


async def _gemini(api_key: str, system: str, prompt: str, temperature: float, max_tokens: int) -> str | None:
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: model.generate_content(
                full_prompt,
                generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
            ),
        )
        return resp.text
    except Exception as e:
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
        if "rate_limit" in msg:
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
