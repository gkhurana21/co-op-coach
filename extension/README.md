# Co-op Coach Chrome extension — development preview

This preview supports secure session pairing, Today, form filling, prepared PDF attachments, an explicit tracker update, and opening the existing full app. Mailbox scanning is still being built. Python must be running.

1. Start the fictional demo with `.venv/bin/python -m coopcoach demo --port 8799`.
2. In Chrome open `chrome://extensions`, enable Developer mode, choose Load unpacked, and select this `extension/` directory.
3. In the app, Settings → AI & extension → Chrome extension → Get a code. Copy the temporary pairing code.
4. Click the extension toolbar icon. Set the app address to `http://127.0.0.1:8799`, paste the code, and connect.
5. Open an application tab, press the extension toolbar icon, then **Fill this form**. Review the result and finish the application yourself.
6. After you submit it yourself, press **I’ve submitted it — update tracker**. This updates only your tracker.
7. Use Refresh for Today updates and Open full app for the larger pages.

The extension requests access to the listed ATS hosts for embedded application frames, and temporary access to the tab you explicitly open it on. It never requests all-sites access. Prepare a known posting in the app first to attach tailored files; unknown pages get contact basics only.

Codes expire after five minutes and can be used once. Tickets stay in browser session storage and are bound to the app's unlocking session. Reconnect after restarting Chrome or the app. Locking the app blocks the extension; Disconnect extensions in Settings revokes access. No passcode goes into the extension.

Validation: `python -m tests.test_extension` exercises API boundaries on scratch SQLite. `.venv/bin/python -m tools.extension_smoke` loads the real unpacked extension into temporary Chromium and a temporary local app. It does not use the owner's browser/profile or accounts. It verifies actual dropdown selection, radio/native selects, an embedded frame, a rendered PDF attachment, untouched demographic/login/trap fields, no submission, and the explicit tracker update. This is a practice-form test in a full extension page, not proof of native side-panel UX or live employer forms. Inbox and owner-account checks remain.

After changing the shared Python form rules, run `.venv/bin/python -m tools.build_extension`; use `--check` to verify the packaged local script is current. No remote code or eval is used.
