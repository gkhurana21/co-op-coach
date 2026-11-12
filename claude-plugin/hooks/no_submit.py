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
    r"|\b(submit|send)(s|ting|ing)? (the |my |this |your )?(form|application)\b"  # "Submit the form", "Sends the form"
    r"|\b(click|press|tap|hit)\w* (on )?(the )?[\"'“‘]?(submit|send|finish)\b"   # "Clicks the Submit button", 'Click "Send"'
    r"|\bto (finish|submit|send)\b(?!\s+(the |this )?(\w+ )?(section|step|page|field)s?\b)"  # "Press Enter to send"
    # an Apply button on the form itself sends it ("click Apply on the job page" only opens the form)
    r"|\b(click|press|tap|hit)\w*\b[^.]{0,30}\bapply\b[^.]{0,40}\b((bottom|end) of|on|in|within) (the |this )?"
    r"(application )?form\b", re.I)


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
