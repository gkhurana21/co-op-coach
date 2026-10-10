"""Claude Code PreToolUse hook (Co-op Coach plugin): Claude in Chrome may fill applications but never submit them.

Blocks browser clicks/keys whose described effect is submitting or sending an application, and tells Claude to hand
over to the student. Exit code 2 = block, with the reason on stderr.
"""
import json
import re
import sys

SUBMIT = re.compile(
    r"\b(submit|send|finish|complete|confirm)\w*\b[^.]{0,40}\bapplication\b"      # "Submits the application"
    r"|\bapplication\b[^.]{0,20}\b(submit|send)\w*\b"                           # "application ... submitted"
    r"|\bsubmit(s|ting)? (the |my |this |your )?(form|application)\b"            # "Submit the form"
    r"|\b(click|press|tap)\w* (on )?(the )?[\"'“]?submit\b", re.I)              # "Clicks the Submit button"


def _summaries(tool_input: dict) -> list[str]:
    out = [str(tool_input.get("action_summary") or "")]
    for a in tool_input.get("actions") or []:  # browser_batch
        out.append(str((a.get("input") or {}).get("action_summary") or ""))
    return out


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if any(SUBMIT.search(s) for s in _summaries(event.get("tool_input") or {})):
        print("Co-op Coach: the student submits applications themselves. Stop here, tell them the form is filled and "
              "ready to review, and ask them to press Submit.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
