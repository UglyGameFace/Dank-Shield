"""Optional private RivaL Intelligence voice-advisory classifier.

Used only for Dank Shield's explicitly consented Watch/Vosk voice-safety
mode. A local LFM3 model gives an uncalibrated categorical suggestion; staff
review is required for every alert. Never takes disciplinary action.
"""
from __future__ import annotations

import asyncio
import os

import aiohttp

from .voice_safety_policy import VoiceSafetyVerdict

_URL = "http://rival-llm:8765/v1/moderate"
_CATEGORIES = {
    "targeted_harassment": "harassment",
    "sexual_harassment": "unwanted_sexual_comments",
    "graphic_sexual_content": "graphic_sexual_content",
    "threat": "threat",
}
_REASONS = {
    "harassment": "Possible targeted harassment; staff must check full context.",
    "unwanted_sexual_comments": "Possible unwanted sexual remarks; verify context.",
    "graphic_sexual_content": "Possible graphic sexual remarks; verify context.",
    "threat": "Possible threat; verify context and urgency independently.",
}


async def classify_local_voice(
    transcript: str, context: tuple[str, ...] = ()
) -> VoiceSafetyVerdict | None:
    text = str(transcript or "").strip()
    token = os.getenv("DANK_RIVAL_VOICE_TOKEN", "").strip()
    # Floor is 3, matching VoiceSafetyAdvisor.inspect: a lone word must reach
    # the model; the review/high-confidence/category bar decides, not length.
    if not (3 <= len(text) <= 1200) or not 32 <= len(token) <= 256:
        return None
    # Clamp context to what the review endpoint accepts, so a bad caller
    # cannot turn a verdict into a rejected request.
    safe_context = [str(segment)[:300] for segment in (context or ())][:2]
    safe_context = [segment for segment in safe_context if segment]
    timeout = aiohttp.ClientTimeout(total=65.0, connect=1.5)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
            async with client.post(
                _URL,
                headers={"Authorization": "Bearer " + token},
                json={"version": 1, "transcript": text, "context": safe_context},
                allow_redirects=False,
            ) as reply:
                if reply.status != 200:
                    return None
                if reply.content_length is not None and reply.content_length > 2048:
                    return None
                content = bytearray()
                while len(content) <= 2048:
                    chunk = await reply.content.read(2049 - len(content))
                    if not chunk:
                        break
                    content.extend(chunk)
                if len(content) > 2048:
                    return None
                try:
                    import json
                    result = json.loads(bytes(content))
                except (UnicodeError, ValueError):
                    return None
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, OSError):
        return None

    if not isinstance(result, dict) or set(result) != {
        "model", "decision", "category", "confidence",
        "advisory_only", "calibrated", "enforcement_authorized",
    }:
        return None
    if not (
        type(result["model"]) is str
        and result["model"].startswith("LiquidAI/LFM3-")
        and result["advisory_only"] is True
        and result["calibrated"] is False
        and result["enforcement_authorized"] is False
        and result["decision"] == "review"
        and result["confidence"] == "high"
    ):
        # Treat uncertain, low, or medium uncalibrated labels as abstentions.
        return None
    category = _CATEGORIES.get(result["category"])
    if category is None:
        return None
    return VoiceSafetyVerdict(
        category=category, severity="review", reason=_REASONS[category],
    )
