"""Public GitHub repos: what a project is and what it's built with, for a hackathon write-up (hackathons.py).

Only github.com repo links, only what's public (no sign-in, no token), through the client that refuses anything but
the public internet. One repo costs four or five requests (GitHub allows 60 an hour without signing in), and the
answer is kept for six hours.
"""

import base64
import json
import logging
import re
import time

from . import config
from .sources import client

log = logging.getLogger(__name__)
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
TTL = 6 * 3600
_REPO = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?(?:[#?].*|/.*)?$")
# dependency files that say what a project is built with, at the top level or one folder down (frontend/, server/)
MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "Cargo.toml", "go.mod", "pubspec.yaml", "Gemfile",
             "build.gradle", "pom.xml", "composer.json")

# a dependency name (lowercase) -> the tag Devpost shows for it. Only what a manifest or the repo's own topics say.
TAGS = {
    "react": "react", "react-dom": "react", "next": "next.js", "vue": "vue.js", "nuxt": "nuxt", "svelte": "svelte",
    "@sveltejs/kit": "sveltekit", "@angular/core": "angular", "solid-js": "solid", "astro": "astro",
    "express": "express.js", "fastify": "fastify", "@nestjs/core": "nestjs", "hono": "hono", "socket.io": "socket.io",
    "tailwindcss": "tailwind", "three": "three.js", "d3": "d3.js", "chart.js": "chart.js", "electron": "electron",
    "react-native": "react-native", "expo": "expo", "vite": "vite", "prisma": "prisma", "@prisma/client": "prisma",
    "mongoose": "mongodb", "mongodb": "mongodb", "pg": "postgresql", "mysql2": "mysql", "redis": "redis",
    "firebase": "firebase", "firebase-admin": "firebase", "@supabase/supabase-js": "supabase", "supabase": "supabase",
    "openai": "openai", "@anthropic-ai/sdk": "claude", "anthropic": "claude", "@google/generative-ai": "gemini",
    "google-generativeai": "gemini", "google-genai": "gemini", "langchain": "langchain", "@langchain/core": "langchain",
    "llama-index": "llamaindex", "cohere": "cohere", "groq": "groq", "elevenlabs": "elevenlabs",
    "@tensorflow/tfjs": "tensorflow", "twilio": "twilio", "stripe": "stripe", "auth0": "auth0", "@clerk/nextjs": "clerk",
    "mapbox-gl": "mapbox", "leaflet": "leaflet", "@vercel/analytics": "vercel",
    "flask": "flask", "fastapi": "fastapi", "django": "django", "streamlit": "streamlit", "gradio": "gradio",
    "torch": "pytorch", "pytorch": "pytorch", "tensorflow": "tensorflow", "keras": "keras", "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn", "numpy": "numpy", "pandas": "pandas", "opencv-python": "opencv", "transformers":
    "hugging-face", "sqlalchemy": "sqlalchemy", "pydantic": "pydantic", "boto3": "amazon-web-services",
    "selenium": "selenium", "playwright": "playwright", "pygame": "pygame", "matplotlib": "matplotlib",
    "tokio": "tokio", "actix-web": "actix", "axum": "axum", "serde": "serde", "bevy": "bevy",
    "github.com/gin-gonic/gin": "gin", "flutter": "flutter", "rails": "ruby-on-rails", "spring-boot": "spring",
    "laravel/framework": "laravel",
}
# GitHub's language names -> Devpost tags
LANGS = {"JavaScript": "javascript", "TypeScript": "typescript", "Python": "python", "Java": "java", "C++": "c++",
         "C": "c", "C#": "c#", "Go": "go", "Rust": "rust", "Swift": "swift", "Kotlin": "kotlin", "Dart": "dart",
         "Ruby": "ruby", "PHP": "php", "HTML": "html", "CSS": "css", "Jupyter Notebook": "jupyter", "Shell": "shell",
         "Svelte": "svelte", "Vue": "vue.js", "Lua": "lua", "R": "r", "Scala": "scala", "Elixir": "elixir",
         "Haskell": "haskell", "Solidity": "solidity", "GDScript": "godot", "ShaderLab": "unity", "Arduino": "arduino",
         "MATLAB": "matlab", "Zig": "zig", "Objective-C": "objective-c", "TeX": "latex", "Dockerfile": "docker",
         "Verilog": "verilog", "SystemVerilog": "systemverilog", "VHDL": "vhdl", "Cuda": "cuda", "Julia": "julia",
         "OCaml": "ocaml", "Assembly": "assembly", "PowerShell": "powershell", "Processing": "processing"}


class NotARepo(ValueError):
    """Not a public github.com repository link."""


def parse(url: str) -> tuple[str, str]:
    m = _REPO.match((url or "").strip())
    if not m or m.group(2) in (".", ".."):
        raise NotARepo("paste the project's GitHub link, like https://github.com/you/project")
    return m.group(1), m.group(2)


def _cache_path(owner: str, name: str):
    d = config.CACHE_DIR / "github"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{owner.lower()}__{name.lower()}.json"


def repo(url: str, fresh: bool = False) -> dict:
    """What a public repo says about itself: description, topics, languages, its README, the dependency files it has
    and the tags they add up to. Raises NotARepo for anything else, and LookupError for a private or missing repo."""
    owner, name = parse(url)
    path = _cache_path(owner, name)
    if not fresh and path.exists() and time.time() - path.stat().st_mtime < TTL:
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
            return {**got, "built_with": built_with(got)}  # tags as this version reads them
        except ValueError:
            pass
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    with client(timeout=20, public_only=True) as c:
        r = c.get(f"{API}/repos/{owner}/{name}", headers=headers)
        if r.status_code == 404:
            raise LookupError("that repo isn't public (or doesn't exist): make it public on GitHub, or paste a public one")
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise LookupError("GitHub's limit for reading without signing in is used up for this hour: try again later")
        r.raise_for_status()
        meta = r.json()
        branch = meta.get("default_branch") or "main"
        langs = c.get(f"{API}/repos/{owner}/{name}/languages", headers=headers)
        langs = langs.json() if langs.status_code == 200 else {}
        readme = ""
        got = c.get(f"{API}/repos/{owner}/{name}/readme", headers=headers)
        if got.status_code == 200:
            try:
                readme = base64.b64decode(got.json().get("content") or "").decode("utf-8", "replace")
            except ValueError:
                readme = ""
        tree = c.get(f"{API}/repos/{owner}/{name}/git/trees/{branch}", params={"recursive": "1"}, headers=headers)
        paths = [t["path"] for t in (tree.json().get("tree") or [])] if tree.status_code == 200 else []
        found = [p for p in paths if p.rsplit("/", 1)[-1] in MANIFESTS and p.count("/") <= 1
                 and "node_modules" not in p][:4]
        manifests = {}
        for p in found:
            f = c.get(f"{RAW}/{owner}/{name}/{branch}/{p}")
            if f.status_code == 200:
                manifests[p] = f.text[:20000]
    total = sum(langs.values()) or 1
    out = {"owner": owner, "name": meta.get("name") or name, "url": meta.get("html_url") or f"https://github.com/{owner}/{name}",
           "description": (meta.get("description") or "").strip(), "topics": meta.get("topics") or [],
           "homepage": meta.get("homepage") or "", "stars": meta.get("stargazers_count") or 0,
           "created_at": meta.get("created_at") or "", "pushed_at": meta.get("pushed_at") or "",
           "languages": [k for k, v in sorted(langs.items(), key=lambda kv: -kv[1]) if v / total >= 0.02],
           "readme": readme[:30000], "files": len(paths), "manifests": sorted(manifests)}
    out["deps"] = sorted(dependencies(manifests))
    out["built_with"] = built_with(out)
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


def dependencies(manifests: dict[str, str]) -> set[str]:
    """Dependency names (lowercase) from the files a project uses to install them."""
    names: set[str] = set()
    for path, text in manifests.items():
        base = path.rsplit("/", 1)[-1]
        try:
            if base in ("package.json", "composer.json"):
                d = json.loads(text)
                for k in ("dependencies", "devDependencies", "require"):
                    names |= {n.lower() for n in (d.get(k) or {})}
            elif base == "requirements.txt":
                names |= {re.split(r"[<>=!~\[; ]", ln.strip(), 1)[0].lower() for ln in text.splitlines()
                          if ln.strip() and not ln.strip().startswith(("#", "-"))}
            elif base == "pyproject.toml":
                block = re.findall(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
                names |= {re.split(r"[<>=!~\[; ]", s, 1)[0].lower() for b in block for s in re.findall(r"[\"']([^\"']+)[\"']", b)}
                names |= {m.lower() for m in re.findall(r"^\s*([A-Za-z0-9_.-]+)\s*=", text.split("[tool.poetry.dependencies]")[-1], re.M)} \
                    if "[tool.poetry.dependencies]" in text else set()
            elif base == "Cargo.toml":
                deps = text.split("[dependencies]", 1)[-1].split("\n[", 1)[0] if "[dependencies]" in text else ""
                names |= {m.lower() for m in re.findall(r"^\s*([A-Za-z0-9_-]+)\s*=", deps, re.M)}
            elif base == "go.mod":
                names |= {m.lower() for m in re.findall(r"^\s*(?:require\s+)?([\w./-]+\.[\w./-]+)\s+v[\d.]", text, re.M)}
            elif base == "pubspec.yaml":
                names.add("flutter") if re.search(r"^\s*flutter:", text, re.M) else None
            elif base == "Gemfile":
                names |= {m.lower() for m in re.findall(r"""^\s*gem\s+['"]([\w-]+)['"]""", text, re.M)}
            elif base in ("build.gradle", "pom.xml"):
                names |= {"spring-boot"} if "spring-boot" in text else set()
        except ValueError:
            continue
    return {n for n in names if n}


def built_with(r: dict) -> list[str]:
    """Devpost's "Built with" tags, only from what the repo itself shows: its languages, its dependency files, and
    the topics its owner gave it."""
    tags = [LANGS[lang] for lang in r.get("languages") or [] if lang in LANGS]
    tags += [TAGS[d] for d in r.get("deps") or [] if d in TAGS]
    tags += [t for t in r.get("topics") or [] if t in set(TAGS.values()) | set(LANGS.values())]
    return list(dict.fromkeys(tags))[:16]


def readme_sections(text: str) -> dict[str, str]:
    """A README split by its headings: {"": the part before the first one, "features": ..., ...} with badges, images
    and HTML taken out."""
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images and badges
    text = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    out: dict[str, list[str]] = {"": []}
    key = ""
    for line in text.splitlines():
        m = re.match(r"^\s{0,3}#{1,4}\s+(.*?)\s*#*\s*$", line)
        if m:
            key = re.sub(r"[^a-z0-9 ']", "", m.group(1).lower()).strip()
            out.setdefault(key, [])
            continue
        out[key].append(line)
    return {k: re.sub(r"\n{3,}", "\n\n", "\n".join(v)).strip() for k, v in out.items()}
