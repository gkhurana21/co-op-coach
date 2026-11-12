"""Microsoft Graph inbox reader: the delta query keeps its place, emails keep Microsoft's own dates, only job-looking
emails get their body fetched (batched), and a forgotten place starts over. Against a stand-in Graph, never Microsoft.

Run: .venv/bin/python -m tests.test_graph_delta
"""

import json
import os
import tempfile
from datetime import datetime, timezone

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-graph-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="")

import httpx  # noqa: E402

from coopcoach import db, mail  # noqa: E402

MSGS = [
    {"id": "m1", "internetMessageId": "<a@shopify.com>", "subject": "Thank you for applying to Shopify",
     "from": {"emailAddress": {"name": "Shopify Early Talent", "address": "earlytalent@shopify.com"}},
     "receivedDateTime": "2026-07-02T14:05:00Z", "bodyPreview": "We received your application", "webLink": "https://x"},
    {"id": "m2", "internetMessageId": "<b@linkedin.com>", "subject": "Your interview prep guide",
     "from": {"emailAddress": {"name": "LinkedIn News", "address": "editors-noreply@linkedin.com"}},
     "receivedDateTime": "2026-11-03T21:00:00Z", "bodyPreview": "Top interview tips this week", "webLink": ""},
    {"id": "m3", "internetMessageId": "<c@uni.ca>", "subject": "Lunch on Friday?",
     "from": {"emailAddress": {"name": "Sam", "address": "sam@uni.ca"}},
     "receivedDateTime": "2026-11-04T10:00:00Z", "bodyPreview": "Want to grab food", "webLink": ""},
]
NEW = {"id": "m4", "internetMessageId": "<d@greenhouse.io>", "subject": "Your CodeSignal assessment for Ramp",
       "from": {"emailAddress": {"name": "Ramp Recruiting", "address": "no-reply@us.greenhouse-mail.io"}},
       "receivedDateTime": "2026-11-06T09:30:00Z", "bodyPreview": "Please complete the assessment", "webLink": ""}


def main():
    db.init()
    checks: dict[str, bool] = {}
    seen = {"gets": [], "batches": [], "phase": "first"}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if req.method == "POST" and url.endswith("/$batch"):
            reqs = json.loads(req.content)["requests"]
            seen["batches"].append([r["url"] for r in reqs])
            return httpx.Response(200, json={"responses": [
                {"id": r["id"], "status": 200, "body": {"body": {"content": f"full body of {r['url'].split('/')[3].split('?')[0]}"}}}
                for r in reqs]})
        seen["gets"].append(url)
        if "deltatoken=stale" in url:
            return httpx.Response(410, json={"error": {"code": "syncStateNotFound"}})
        if "deltatoken=one" in url:
            return httpx.Response(200, json={"value": [NEW, {"id": "m3", "@removed": {"reason": "deleted"}}],
                                             "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/delta?deltatoken=two"})
        if "skiptoken=p2" in url:
            return httpx.Response(200, json={"value": MSGS[2:], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/delta?deltatoken=one"})
        return httpx.Response(200, json={"value": MSGS[:2], "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/delta?skiptoken=p2"})

    mail.ms_refresh = lambda tok: {"access_token": "AT", "refresh_token": tok}
    mail.account_secret = lambda acct: "RT"
    acct = {"id": 7, "profile_id": 1, "address": "student@uwaterloo.ca"}
    client = httpx.Client(transport=httpx.MockTransport(handler))

    out = mail.graph_fetch(acct, 1700000000, client=client)
    checks["the first check lists the inbox from the lookback date, page after page"] = \
        "receivedDateTime+ge+2023-11-14" in seen["gets"][0] and "/mailFolders/inbox/messages/delta" in seen["gets"][0] \
        and len(seen["gets"]) == 2
    checks["only the job-looking email is kept: not LinkedIn News, not a lunch invite"] = [m["graph_id"] for m in out] == ["m1"]
    checks["its date is Microsoft's receivedDateTime (July), not when it was read"] = \
        out[0]["received_at"] == int(datetime(2026, 7, 2, 14, 5, tzinfo=timezone.utc).timestamp())
    checks["bodies come in one batch, only for the emails worth reading"] = \
        seen["batches"] == [["/me/messages/m1?$select=body"]] and out[0]["body"] == "full body of m1"
    checks["Microsoft's place is saved for next time"] = db.meta_get("graph_delta:7").endswith("deltatoken=one")

    seen["gets"].clear()
    out = mail.graph_fetch(acct, 1700000000, client=client)
    checks["the next check is one request and returns only what's new (deleted ones ignored)"] = \
        len(seen["gets"]) == 1 and [m["graph_id"] for m in out] == ["m4"] and db.meta_get("graph_delta:7").endswith("two")

    db.meta_set("graph_delta:7", "https://graph.microsoft.com/v1.0/me/delta?deltatoken=stale")
    seen["gets"].clear()
    out = mail.graph_fetch(acct, 1700000000, client=client)
    checks["a place Microsoft forgot starts over from the lookback date instead of failing"] = \
        "receivedDateTime+ge+" in seen["gets"][1] and [m["graph_id"] for m in out] == ["m1"]

    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
