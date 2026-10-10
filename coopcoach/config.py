import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("COOPCOACH_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "coopcoach.db"
UPLOAD_DIR = DATA_DIR / "resumes"
CACHE_DIR = DATA_DIR / "cache"

for d in (DATA_DIR, UPLOAD_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:  # an empty ANTHROPIC_API_KEY= would otherwise shadow an `ant auth login` profile
            os.environ.setdefault(k.strip(), v)


_load_dotenv()


@lru_cache(maxsize=1)
def settings() -> dict:
    path = ROOT / "config.yaml"
    cfg = yaml.safe_load(path.read_text()) if path.exists() else {}
    return cfg or {}


def get(path: str, default=None):
    """Dotted lookup into config.yaml, e.g. get('sources.linkedin.enabled', True)."""
    node = settings()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def seed_companies() -> dict:
    return yaml.safe_load((PKG / "seed_companies.yaml").read_text()) or {}


def seed_version() -> str:
    """Changes whenever the shipped company lists change, so existing databases pick up new boards."""
    import hashlib
    h = hashlib.sha1()
    for name in ("seed_companies.yaml", "boards_discovered.yaml"):
        f = PKG / name
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]
