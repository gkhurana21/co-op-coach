"""Narration and music through OpenRouter: speech from openai/gpt-audio, cached per line, so re-running a cut costs nothing.

The key is read from .env (OPENROUTER_API_KEY). Every call's cost is added to docs/handoff/film-shots/voice/spend.json.
"""

import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "docs" / "handoff" / "film-shots" / "voice"
URL = "https://openrouter.ai/api/v1/chat/completions"
VOICE = "cedar"
STYLE = ("You are a voice actor narrating a short film about a university student. Read the user's line exactly, word for word, "
         "and say nothing else. Deep, warm and smooth, low and close to the mic; calm confidence, like a premium film trailer told "
         "gently. Relaxed pace, natural pauses, never loud or salesy.")


def _key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no OPENROUTER_API_KEY in .env")


def _spend(cost: float) -> float:
    f = CACHE / "spend.json"
    total = (json.loads(f.read_text())["usd"] if f.exists() else 0.0) + cost
    f.write_text(json.dumps({"usd": round(total, 4)}))
    return total


def say(text: str, voice: str = VOICE, style: str = STYLE) -> Path:
    """One line of narration as a 24 kHz mono WAV."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / (hashlib.sha1(f"{voice}|{style}|{text}".encode()).hexdigest()[:16] + ".wav")
    if out.exists():
        return out
    body = {"model": "openai/gpt-audio", "modalities": ["text", "audio"], "audio": {"voice": voice, "format": "pcm16"},
            "stream": True, "usage": {"include": True},
            "messages": [{"role": "system", "content": style}, {"role": "user", "content": text}]}
    pcm, cost = b"", 0.0
    for attempt in range(8):  # a new account is rate-limited per minute: wait and try again
        r = httpx.Client(timeout=180).send(httpx.Request("POST", URL, headers={"Authorization": f"Bearer {_key()}"}, json=body), stream=True)
        if r.status_code == 429:
            r.close()
            time.sleep(20)
            continue
        break
    try:
        if r.status_code != 200:
            raise SystemExit(f"voice request refused ({r.status_code}): {r.read()[:300]!r}")
        for line in r.iter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            d = json.loads(line[6:])
            if d.get("usage"):
                cost = float(d["usage"].get("cost") or 0)
            for c in d.get("choices", []):
                a = (c.get("delta") or {}).get("audio") or {}
                if a.get("data"):
                    pcm += base64.b64decode(a["data"])
    finally:
        r.close()
    if not pcm:
        raise SystemExit(f"no audio came back for: {text}")
    raw = out.with_suffix(".pcm")
    raw.write_bytes(pcm)
    trim = "silenceremove=start_periods=1:start_threshold=-48dB,areverse,silenceremove=start_periods=1:start_threshold=-48dB,areverse"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(raw), "-af", trim, str(out)], check=True)
    raw.unlink()
    total = _spend(cost)
    print(f"  voice: {text[:48]!r} ${cost:.4f} (total ${total:.3f})", flush=True)
    if total > 9.0:
        raise SystemExit("stopping: OpenRouter spend is near the $10 cap")
    return out
