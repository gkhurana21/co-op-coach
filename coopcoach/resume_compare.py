""""Check against my file": every difference between the Studio copy (with your edits) and a fresh read of your
uploaded resume, and taking the parts you pick."""

import copy
import hashlib
import json

from .resume_checker import _tidy, unglue_project

_ENTRY_FIELDS = {"education": ("school", "degree", "location", "dates"),
                 "experience": ("title", "company", "location", "dates", "link"),
                 "projects": ("name", "tech", "dates", "link", "demo")}
_SECTION_LABEL = {"education": "Education", "experience": "Experience", "projects": "Projects"}


def _sim(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, _tidy(a).lower(), _tidy(b).lower()).ratio()


def _entry_key(sec: str, e: dict) -> str:
    if sec == "experience":
        return f"{e.get('company', '')} {e.get('title', '')}"
    if sec == "projects":
        glued = unglue_project(e.get("name") or "")
        return (glued[0] if glued else e.get("name", "")).split("|")[0]
    return e.get("school", "")


def _lines_overlap(x: list[str], y: list[str]) -> float:
    if not x or not y:
        return 0.0
    return sum(max(_sim(l, m) for m in x) for l in y) / len(y)


def _match(a: list, b: list, key, floor: float = 0.5) -> list[tuple[int, int]]:
    """Greedy best pairs (i in a, j in b): similar names, and (when both have them) the same bullets."""
    def score(x, y):
        k = _sim(key(x), key(y))
        lx, ly = x.get("bullets") or x.get("details") or [], y.get("bullets") or y.get("details") or []
        return 0.5 * k + 0.5 * _lines_overlap(lx, ly) if lx and ly else k
    pairs = sorted(((score(x, y), i, j) for i, x in enumerate(a) for j, y in enumerate(b)), reverse=True)
    used_a, used_b, out = set(), set(), []
    for score, i, j in pairs:
        if score >= floor and i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            out.append((i, j))
    return out


def compare(mine: dict, fresh: dict, snapshot: dict | None = None) -> list[dict]:
    """Differences between the Studio copy and a fresh read of the file. Each item: id, label, kind
    (changed | missing | only_mine), mine / file values, and `untouched` when you never edited that spot (it still
    matches what was read last time), so taking the file's version is safe."""
    from .resume_studio import (  # resume_studio imports this module
        _get,
        _tidy,
        normalize,
    )
    mine, fresh = normalize(mine), normalize(fresh)
    snap = normalize(snapshot) if snapshot else None
    items: list[dict] = []

    def snap_val(path):
        try:
            return _get(snap, path) if snap else None
        except (KeyError, IndexError, TypeError):
            return None

    def add(kind, label, path, mine_v, file_v, extra=None):
        it = {"kind": kind, "label": label, "path": path, "mine": mine_v, "file": file_v,
              "untouched": bool(snap) and kind == "changed" and snap_val(path) == mine_v}
        it["suggested"] = kind != "only_mine" and (it["untouched"] or not mine_v or (
            isinstance(mine_v, str) and isinstance(file_v, str) and _sim(mine_v, file_v) >= 0.88))
        it.update(extra or {})
        it["id"] = hashlib.sha1(json.dumps([kind, path, file_v], ensure_ascii=False, default=str).encode()).hexdigest()[:10]
        items.append(it)

    for k in ("name", "email", "phone", "location"):
        a, b = mine["header"].get(k, ""), fresh["header"].get(k, "")
        if b and _tidy(a) != _tidy(b):
            add("changed", f"Header · {k}", ["header", k], a, b)
    have = {x.lower().rstrip("/") for x in mine["header"]["links"]}
    for link in fresh["header"]["links"]:
        if link.lower().rstrip("/") not in have:
            add("missing", "Header · link", ["header", "links"], "", link, {"insert": "link"})

    for sec, fields in _ENTRY_FIELDS.items():
        a, b = mine[sec], fresh[sec]
        pairs = _match(a, b, lambda e, s=sec: _entry_key(s, e))
        for i, j in pairs:
            ea, eb = a[i], b[j]
            name = (_entry_key(sec, eb) or _entry_key(sec, ea)).strip()[:60]
            for f in fields:
                if eb.get(f) and _tidy(ea.get(f, "")) != _tidy(eb[f]):
                    add("changed", f"{_SECTION_LABEL[sec]} · {name} · {f}", [sec, i, f], ea.get(f, ""), eb[f])
            lines = "details" if sec == "education" else "bullets"
            la, lb = ea.get(lines, []), eb.get(lines, [])
            for jj, line in enumerate(lb):
                best = max(((_sim(line, x), ii) for ii, x in enumerate(la)), default=(0, -1))
                if best[0] >= 0.97:
                    continue
                if best[0] >= 0.6:
                    add("changed", f"{_SECTION_LABEL[sec]} · {name} · line {best[1] + 1}", [sec, i, lines, best[1]],
                        la[best[1]], line)
                else:
                    add("missing", f"{_SECTION_LABEL[sec]} · {name} · a line only in your file", [sec, i, lines],
                        "", line, {"insert": "line", "at": jj})
            for ii, line in enumerate(la):
                if max((_sim(line, x) for x in lb), default=0) < 0.6:
                    add("only_mine", f"{_SECTION_LABEL[sec]} · {name} · a line you added", [sec, i, lines, ii], line, "")
        matched_a, matched_b = {i for i, _ in pairs}, {j for _, j in pairs}
        for j, eb in enumerate(b):
            if j not in matched_b:
                add("missing", f"{_SECTION_LABEL[sec]} · {_entry_key(sec, eb).strip()[:60]} (only in your file)", [sec],
                    "", _entry_key(sec, eb).strip(), {"insert": "entry", "entry": eb, "at": j})
        for i, ea in enumerate(a):
            if i not in matched_a:
                add("only_mine", f"{_SECTION_LABEL[sec]} · {_entry_key(sec, ea).strip()[:60]} (you added it)", [sec, i],
                    _entry_key(sec, ea).strip(), "")

    by_label = {s["label"].lower(): i for i, s in enumerate(mine["skills"])}
    for s in fresh["skills"]:
        i = by_label.get(s["label"].lower())
        if i is None:
            add("missing", f"Skills · {s['label']}", ["skills"], "", f"{s['label']}: {s['items']}",
                {"insert": "skills", "entry": s})
        elif _tidy(mine["skills"][i]["items"]) != _tidy(s["items"]):
            add("changed", f"Skills · {s['label']}", ["skills", i, "items"], mine["skills"][i]["items"], s["items"])
    return items


def apply_compare(mine: dict, fresh: dict, ids: list[str], snapshot: dict | None = None) -> tuple[dict, int]:
    """Take the file's version for the chosen differences. Edits first, then additions (in file order)."""
    from .resume_studio import (  # resume_studio imports this module
        _get,
        _set,
        normalize,
    )
    r = normalize(mine)
    chosen = [it for it in compare(r, fresh, snapshot) if it["id"] in set(ids) and it["kind"] != "only_mine"]
    n = 0
    for it in [x for x in chosen if x["kind"] == "changed"]:
        try:
            _set(r, it["path"], it["file"])
            n += 1
        except (KeyError, IndexError, TypeError):
            pass
    for it in sorted([x for x in chosen if x["kind"] == "missing"], key=lambda x: x.get("at", 0)):
        ins = it.get("insert")
        if ins == "link":
            r["header"]["links"].append(it["file"])
        elif ins == "line":
            lst = _get(r, it["path"])
            lst.insert(min(it.get("at", len(lst)), len(lst)), it["file"])
        elif ins == "entry":
            sec = it["path"][0]
            r[sec].insert(min(it.get("at", len(r[sec])), len(r[sec])), copy.deepcopy(it["entry"]))
        elif ins == "skills":
            r["skills"].append(copy.deepcopy(it["entry"]))
        else:
            continue
        n += 1
    return normalize(r), n
