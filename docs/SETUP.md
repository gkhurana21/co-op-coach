# Setting up Co-op Coach for real

You need a Mac with Python 3.10+ (`brew install python@3.12`), Google Chrome and git.

```bash
./run.sh            # first run sets everything up, then http://127.0.0.1:8765
```

Add yourself, set a passcode, upload your resume in Settings → Profile, and say what you're looking for. A friend can
have their own profile (and passcode) on the same Mac, or run their own copy.

## AI: your Claude Code, ChatGPT, or free Groq

Everything works without AI. Settings → AI picks the engine:
* **Claude Code** or **ChatGPT (Codex)**: if the CLI is installed and signed in, your plan does all the AI work, with
  no API key. *Connect* also installs the Co-op Coach plugin, so in Claude Code you can type `/coop-coach:autopilot`,
  `/coop-coach:apply Shopify`, `/coop-coach:match quant`, `/coop-coach:interview-prep RBC`... (in Codex: `$autopilot`, or just ask).
* **Groq (free)**: get a key at <https://console.groq.com/keys> and set `GROQ_API_KEY=` in `.env`.


## Inbox: Gmail, Outlook, iCloud, any IMAP

Settings → Inbox → *Connect an inbox* and type your address; the steps for your provider appear. Tip: use
a separate email just for applications. Gmail, Yahoo and iCloud use an app password. Outlook, Hotmail and school
Microsoft 365: press *Sign in to Outlook* and sign in in the window that opens, as you would in a browser (Duo included).
It needs no IT approval and no app registration. The window reads your first emails and closes by itself; after that it
reads only what Outlook's own search finds for recruiting words, and *Sign in again* is there when your school ends the
session (Co-op Coach never sees your password, and keeps the sign-in in that window's own profile). If you'd rather use Microsoft's API, register an app once:
1. <https://entra.microsoft.com> → App registrations → New registration ("Accounts in any organizational directory
   and personal Microsoft accounts", no redirect URI).
2. Authentication → *Allow public client flows: Yes*. API permissions → Microsoft Graph → Delegated → `Mail.Read`.
3. Paste the Application (client) ID in the app (it isn't a secret), then sign in with the code it shows.

The first scan reads the last 120 days. Statuses only move forward; fix any email from *Inbox activity*. No inbox?
Paste an email into the chat or Applications.




## Your data in Supabase, and the calendar link

1. Create a free project, then make the app's database login. The password is generated on your Mac and goes
   straight to the Keychain; the project only gets a one-way hash:
   `.venv/bin/python -c "from coopcoach import supa; print(supa.setup_sql(supa.new_credentials('<project ref>')))"`
   and run the printed SQL in the project's SQL editor.
2. `.venv/bin/python -m coopcoach supabase --connect <project ref> --region <region> --move` copies every table
   (checking counts) and switches. `--local` switches back; the local file is kept as a backup.
3. Deploy `supabase/functions/calendar` (with JWT verification off; the long random token in the URL is the key).

Tables live in a private `coopcoach` schema the public API can't reach.


## Alerts, passcodes, background syncs

* **Alerts:** phone push with [ntfy](https://ntfy.sh) (a hard-to-guess topic), a Discord webhook, or email (SMTP in
  `.env`). Mac banners are always on.
* **Passcodes** are salted PBKDF2 hashes; the app auto-locks after 30 idle minutes. Forgot it? On this Mac:
  `.venv/bin/python -m coopcoach reset-passcode "Your Name"`. It's a lock, not disk encryption.
* **Syncing while the app is closed:** `0 */3 * * * cd /path/to/coop-coach && .venv/bin/python -m coopcoach sync`
  in `crontab -e`.


## Privacy

Your data stays in `data/` on your Mac (or your own Supabase project), including the recruiters you hear from.
Passwords and sign-in tokens are in the macOS Keychain; your private calendar link's token is in the database, like the
link itself. Nothing is sent anywhere except requests to job and event sites, the Workday sites you saved logins for,
your alert channels, and the AI you chose. Your Claude Code, Codex and Groq get your text with contact details
redacted; with your own Anthropic API key, it goes to Anthropic's API as is. The one exception is when you ask your
own Claude or ChatGPT to fill a form: it then gets the answers it's typing in.
