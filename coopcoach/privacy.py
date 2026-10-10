"""Strip people's contact details out of anything sent to a third-party AI provider, and put them back in
the reply. Used for providers on free tiers (Groq) so names, emails, phone numbers and profile links never
leave the machine.
"""

import json
import re

from . import db

_TOKEN = "[[P{}]]"


def pii_terms() -> list[str]:
    terms: set[str] = set()
    for p in db.rows("SELECT name, profile_json FROM profiles"):
        prof = db.loads(p["profile_json"], {}) or {}
        terms.update([p["name"], prof.get("name"), prof.get("email"), *(prof.get("links") or [])])
    for d in db.rows("SELECT content FROM documents WHERE kind IN ('resume_master', 'resume')"):
        h = (db.loads(d["content"], {}) or {}).get("header") or {}
        terms.update([h.get("name"), h.get("email"), h.get("phone"), *(h.get("links") or [])])
    out = set()
    for t in terms:
        t = (t or "").strip()
        if len(t) >= 4:  # very short display names would collide with ordinary words
            out.add(t)
            if t.startswith(("https://", "http://")):
                out.add(re.sub(r"^https?://(www\.)?", "", t))
    return sorted(out, key=len, reverse=True)


class Redactor:
    def __init__(self, terms: list[str] | None = None):
        self.terms = pii_terms() if terms is None else terms
        self.to_token = {t.lower(): _TOKEN.format(i) for i, t in enumerate(self.terms)}
        self.to_term = {_TOKEN.format(i): t for i, t in enumerate(self.terms)}
        # whole words only: a name like "Demo" or "Alex" must not eat "demographics" or "AlexNet"
        self._rx = re.compile("|".join(rf"(?<![\w@.]){re.escape(t)}(?![\w])" for t in self.terms), re.I) \
            if self.terms else None
        self._phone = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
        self._email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

    def redact(self, text: str) -> str:
        if not text:
            return text
        if self._rx:
            text = self._rx.sub(lambda m: self.to_token[m.group(0).lower()], text)
        # catch contact details that aren't in any profile yet (e.g. a freshly uploaded resume)
        text = self._email.sub("[[EMAIL]]", text)
        return self._phone.sub("[[PHONE]]", text)

    def restore(self, obj):
        if isinstance(obj, str):
            for tok, term in self.to_term.items():
                obj = obj.replace(tok, term)
            return obj
        if isinstance(obj, list):
            return [self.restore(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.restore(v) for k, v in obj.items()}
        return obj

    def redact_json(self, obj) -> str:
        return self.redact(json.dumps(obj, ensure_ascii=False))
