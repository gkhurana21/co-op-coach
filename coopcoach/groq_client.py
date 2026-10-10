"""Groq (free tier) via its OpenAI-compatible chat API, with strict JSON-schema outputs.

Free-tier reality for openai/gpt-oss-120b: 30 requests/min, 1,000/day, but only ~8,000 tokens/min and
~200,000/day per account. Requests are kept small, short rate-limit waits are retried, and when the daily
budget is gone the provider pauses so the app quietly falls back to its built-in rules until it resets.
"""

import json
import logging
import os
import threading
import time

import httpx

from . import config
from .privacy import Redactor

log = logging.getLogger(__name__)
_paused_until = 0.0
_lock = threading.Lock()


class Paused(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def model() -> str:
    return config.get("ai.groq_model", "openai/gpt-oss-120b")


def paused() -> float:
    return max(0.0, _paused_until - time.time())


def _base_url() -> str:
    return os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")


def _post(body: dict) -> dict:
    global _paused_until
    if paused():
        raise Paused(f"Groq free-tier limit reached; back in {int(paused() / 60) + 1} min")
    headers = {"Authorization": f"Bearer {os.environ.get('GROQ_API_KEY', '')}", "Content-Type": "application/json"}
    for attempt in range(3):
        r = httpx.post(f"{_base_url()}/chat/completions", json=body, headers=headers, timeout=120)
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after", "10") or 10)
            per_day = "per day" in r.text.lower() or wait > 60
            if per_day or attempt == 2:
                with _lock:
                    _paused_until = time.time() + max(wait, 300 if per_day else 60)
                raise Paused("Groq free-tier limit reached; using built-in rules for now")
            time.sleep(min(wait, 20))
            continue
        if r.status_code == 400 and "reasoning_effort" in body and "reasoning" in r.text.lower():
            body = {k: v for k, v in body.items() if k != "reasoning_effort"}  # model without that knob
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Groq error {r.status_code}: {r.text[:300]}")
        return r.json()
    raise RuntimeError("Groq request failed after retries")


def _effort(effort: str) -> str:
    return {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}.get(effort, "medium")


def call_json(system: str, text: str, schema: dict, effort: str = "medium", max_tokens: int = 3500) -> dict:
    red = Redactor()
    body = {
        "model": model(),
        "messages": [{"role": "system", "content": red.redact(system)},
                     {"role": "user", "content": red.redact(text)}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
        "reasoning_effort": _effort(effort),
        "max_completion_tokens": max_tokens,
        "temperature": 0.4,
    }
    def cut_off(msg: str) -> bool:
        return any(k in msg for k in ("json_validate_failed", "max completion tokens", "cut off"))

    try:
        data = _post(body)
        if data["choices"][0].get("finish_reason") == "length":
            raise RuntimeError("Groq response was cut off (max tokens)")
    except RuntimeError as e:  # reasoning used up the token budget before the JSON was finished: think less, retry
        if not cut_off(str(e)) or body.get("reasoning_effort") == "low":
            raise
        data = _post({**body, "reasoning_effort": "low"})
    choice = data["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError("Groq response was cut off (max tokens)")
    return red.restore(json.loads(choice["message"]["content"]))


def chat(system: str, history: list[dict], max_tokens: int = 1200) -> str:
    red = Redactor()
    msgs = [{"role": "system", "content": red.redact(system)}] + [
        {"role": m["role"], "content": red.redact(m["content"])} for m in history]
    data = _post({"model": model(), "messages": msgs, "max_completion_tokens": max_tokens,
                  "reasoning_effort": "low", "temperature": 0.5})
    return red.restore(data["choices"][0]["message"].get("content") or "").strip()
