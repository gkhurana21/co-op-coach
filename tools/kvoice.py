"""Narration with Kokoro, a small open voice model that runs on this Mac: free, offline, and made for reading aloud.

say(text) returns a cached 24 kHz WAV. Needs the kokoro-onnx package and its two model files outside the project:
KOKORO_DIR (kokoro-v1.0.onnx + voices-v1.0.bin) and KOKORO_LIBS (where kokoro-onnx was pip-installed with --target).
"""

import hashlib
import os
import sys
from pathlib import Path

VOICE = os.environ.get("FILM_VOICE", "am_michael")
SPEED = float(os.environ.get("FILM_VOICE_SPEED", "0.94"))
CACHE = Path(__file__).resolve().parent.parent / "docs" / "handoff" / "film-shots" / "voice-kokoro"
_model = None


def _kokoro():
    global _model
    if _model is None:
        libs = os.environ.get("KOKORO_LIBS")
        if libs and libs not in sys.path:
            sys.path.insert(0, libs)
        from kokoro_onnx import Kokoro
        d = Path(os.environ["KOKORO_DIR"])
        _model = Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))
    return _model


def say(text: str, voice: str = VOICE, speed: float = SPEED) -> Path:
    key = hashlib.sha1(f"{voice}|{speed}|{text}".encode()).hexdigest()[:16]
    out = CACHE / voice / f"{key}.wav"
    if out.exists():
        return out
    audio, sr = _kokoro().create(text, voice=voice, speed=speed, lang="en-us" if voice[0] == "a" else "en-gb")
    import soundfile as sf  # importable once _kokoro() has put KOKORO_LIBS on the path
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, audio, sr)
    return out
