// Co-op Coach calendar feed, served from Supabase so Apple Calendar, Google Calendar, Outlook and phones can
// subscribe and stay up to date even when the Mac running Co-op Coach is off.
//
//   GET /functions/v1/calendar/<token>.ics
//
// The token is a long random secret made in the app (Events → Add to my calendar). Only its SHA-256 is stored
// (profiles.prefs_json.calendar_token_hash), so the database never holds the link itself. The feed has the events
// the person saved (⭐ / ✅), the interviews, assessment deadlines, coffee chats and info sessions found in their email
// (calendar_items), the interview times / deadlines they added in Applications, and their reminders, each with an alarm.
import postgres from "npm:postgres@3.4.5";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { prepare: false, max: 2, idle_timeout: 20 });

async function sha256hex(s: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const esc = (s: string) => (s || "").replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r\n|\r|\n/g, "\\n");

// only http(s) links, and no line breaks or other control characters that would add lines to the feed
function link(u?: string): string {
  const s = (u || "").replace(/[\x00-\x1f\x7f]/g, "");
  return /^https?:\/\//i.test(s) ? s : "";
}

function stamp(ts: number, allDay: boolean): string {
  const d = new Date(ts * 1000).toISOString();
  return allDay ? `;VALUE=DATE:${d.slice(0, 10).replace(/-/g, "")}` : `:${d.replace(/[-:]/g, "").replace(/\.\d{3}/, "")}`;
}

// lines longer than 75 octets are folded, as the iCalendar format requires
function fold(line: string): string {
  const bytes = new TextEncoder().encode(line);
  if (bytes.length <= 75) return line;
  const out: string[] = [];
  let cur = "";
  for (const ch of line) {
    if (new TextEncoder().encode(cur + ch).length > (out.length ? 74 : 75)) { out.push(cur); cur = ""; }
    cur += ch;
  }
  out.push(cur);
  return out.join("\r\n ");
}

type Item = { uid: string; title: string; start: number; end: number; allDay: boolean; location?: string;
  url?: string; description?: string; alarm: string };

function ics(items: Item[], name: string): string {
  const now = Math.floor(Date.now() / 1000);
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Co-op Coach//Calendar//EN", "CALSCALE:GREGORIAN",
    `X-WR-CALNAME:${esc(name)}`, "X-PUBLISHED-TTL:PT1H", "REFRESH-INTERVAL;VALUE=DURATION:PT1H"];
  for (const e of items) {
    lines.push("BEGIN:VEVENT", `UID:${e.uid}@coopcoach`, `DTSTAMP${stamp(now, false)}`,
      `DTSTART${stamp(e.start, e.allDay)}`, `DTEND${stamp(e.end, e.allDay)}`, `SUMMARY:${esc(e.title)}`);
    if (e.location) lines.push(`LOCATION:${esc(e.location)}`);
    const desc = [e.url || "", e.description || ""].filter(Boolean).join("\n").slice(0, 900);
    if (desc) lines.push(`DESCRIPTION:${esc(desc)}`);
    const url = link(e.url);
    if (url) lines.push(`URL:${url}`);
    lines.push("BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${esc(e.title)}`, `TRIGGER:${e.alarm}`, "END:VALARM", "END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return lines.map(fold).join("\r\n") + "\r\n";
}

Deno.serve(async (req) => {
  const m = new URL(req.url).pathname.match(/\/calendar\/([A-Za-z0-9_-]{16,})(?:\.ics)?$/);
  if (req.method !== "GET" || !m) return new Response("Not found", { status: 404 });
  try {
    const hash = await sha256hex(m[1]);
    const prof = await sql`select id, name from coopcoach.profiles
                           where (prefs_json::jsonb ->> 'calendar_token_hash') = ${hash} limit 1`;
    if (!prof.length) return new Response("Not found", { status: 404 });
    const pid = prof[0].id;
    const since = Math.floor(Date.now() / 1000) - 30 * 86400;
    const evs = await sql`select e.id, e.title, e.start_ts, e.end_ts, e.all_day, e.location, e.url, e.description, m.mark
                          from coopcoach.event_marks m join coopcoach.events e on e.id = m.event_id
                          where m.profile_id = ${pid} and coalesce(e.end_ts, e.start_ts) >= ${since}
                          order by e.start_ts`;
    const apps = await sql`select a.job_id, a.status, a.interview_at, a.oa_due_at,
                             coalesce(j.company, a.company) as company, coalesce(j.title, a.title) as title,
                             coalesce(j.url, a.url) as url
                           from coopcoach.applications a left join coopcoach.jobs j on j.id = a.job_id
                           where a.profile_id = ${pid} and (coalesce(a.interview_at, 0) >= ${since}
                                                            or coalesce(a.oa_due_at, 0) >= ${since})`;
    const items: Item[] = evs.map((e) => {
      const start = Number(e.start_ts), allDay = !!Number(e.all_day);
      return { uid: e.id, title: (e.mark === "going" ? "✅ " : "⭐ ") + e.title, start,
        end: Number(e.end_ts) || start + (allDay ? 86400 : 3600), allDay, location: e.location || "", url: e.url || "",
        description: (e.description || "").slice(0, 600), alarm: "-P1D" };
    });
    // interviews, assessments, coffee chats and info sessions found in email (the app keeps only the item, not the email)
    const covered = new Set<string>();
    const ALARM: Record<string, string> = { interview: "-PT1H", oa: "-P1D", recruiter_call: "-PT30M", coffee_chat: "-PT30M",
      info_session: "-PT1H", career_fair: "-P1D", other: "-PT30M" };
    const MINUTES: Record<string, number> = { interview: 60, oa: 30, recruiter_call: 30, coffee_chat: 30, info_session: 60,
      career_fair: 180, other: 30 };
    try {
      const mine = await sql`select id, kind, title, start_ts, end_ts, due_ts, all_day, location, join_url, book_url,
                               email_link, who, email_subject, job_id from coopcoach.calendar_items
                             where profile_id = ${pid} and status = 'confirmed'
                               and coalesce(start_ts, due_ts) >= ${since}`;
      for (const c of mine) {
        const due = c.start_ts == null, start = Number(c.start_ts ?? c.due_ts), allDay = !!Number(c.all_day);
        items.push({ uid: `item-${c.id}`, title: due ? `${c.title} (due)` : c.title, start,
          end: Number(c.end_ts) || start + (due ? 1800 : (MINUTES[c.kind] ?? 30) * 60), allDay,
          location: c.location || "", url: c.join_url || c.book_url || c.email_link || "",
          description: [c.who, c.email_subject].filter(Boolean).join(" · "), alarm: ALARM[c.kind] ?? "-PT30M" });
        if (c.job_id) covered.add(`${c.kind === "oa" ? "oa" : "interview"}-${c.job_id}`);
      }
    } catch (e) {
      console.error("calendar items skipped", e);  // an older database without the table still serves the rest
    }
    for (const a of apps) {
      if (Number(a.interview_at) >= since && !covered.has(`interview-${a.job_id}`)) {
        items.push({ uid: `interview-${a.job_id}`, title: `Interview: ${a.company} · ${a.title}`, start: Number(a.interview_at),
          end: Number(a.interview_at) + 3600, allDay: false, url: a.url || "", alarm: "-PT1H" });
      }
      if (Number(a.oa_due_at) >= since && !covered.has(`oa-${a.job_id}`)) {
        items.push({ uid: `oa-${a.job_id}`, title: `Online assessment due: ${a.company} · ${a.title}`,
          start: Number(a.oa_due_at), end: Number(a.oa_due_at) + 1800, allDay: false, url: a.url || "", alarm: "-P1D" });
      }
    }
    try {  // reminders set in the chat ("remind me Friday to follow up")
      const rems = await sql`select id, title, due_at from coopcoach.reminders
                             where profile_id = ${pid} and done = 0 and due_at >= ${since}`;
      for (const r of rems) {
        items.push({ uid: `reminder-${r.id}`, title: `⏰ ${r.title}`, start: Number(r.due_at),
          end: Number(r.due_at) + 900, allDay: false, alarm: "-PT0M" });
      }
    } catch (e) {
      console.error("reminders skipped", e);  // an older database without the table still serves the rest
    }
    return new Response(ics(items, "Co-op Coach"), {
      headers: { "Content-Type": "text/calendar; charset=utf-8", "Cache-Control": "public, max-age=300" },
    });
  } catch (e) {
    console.error("calendar feed failed", e);
    return new Response("Calendar temporarily unavailable", { status: 503 });
  }
});
