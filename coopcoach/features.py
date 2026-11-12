"""Lean or everything. Lean is what a new person sees: matches, preparing and filling applications, the inbox moving them
along, your calendar of interviews, the Path and Jarvis. Everything else (People, the Tracks browser, public events and
hackathons, the Excel export) is still there, a switch away in Settings. Nothing is deleted either way.
"""

from . import db

MODES = ("lean", "all")
# what lean leaves out, by name (the page reads these too)
LEAN_HIDES = ("people", "hackathons", "excel")  # tracks and events: shown to everyone (the owner uses them daily)
# the chat's tools for those, so Jarvis doesn't offer what the app isn't showing
TOOLS_OF = {
    "people": ("recruiters", "draft_message"),
    "tracks": ("pin_track", "follow_track", "my_market", "explore_niche", "follow_niche", "startup_feed", "watch_startup"),
    "events": ("find_events", "save_event", "add_event_source", "remove_event_source"),
    "hackathons": ("hackathon_application", "track_hackathon", "devpost_submission"),
}
# Path goals that only make sense with those pages
GOALS_OF = {"people": ("people",), "tracks": ("follow",), "events": ("event",), "hackathons": ("hack", "devpost")}


def mode() -> str:
    m = db.meta_get("features")
    return m if m in MODES else "lean"


def set_mode(m: str) -> str:
    if m not in MODES:
        raise ValueError("mode is 'lean' or 'all'")
    db.meta_set("features", m)
    return m


def hidden() -> set[str]:
    return set(LEAN_HIDES) if mode() == "lean" else set()


def shown(name: str) -> bool:
    return name not in hidden()


def hidden_tools() -> set[str]:
    return {t for f in hidden() for t in TOOLS_OF.get(f, ())}


def goal_shown(goal_id: str) -> bool:
    return not any(goal_id.startswith(p) for f in hidden() for p in GOALS_OF.get(f, ()))
