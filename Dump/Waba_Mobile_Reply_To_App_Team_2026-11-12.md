# Reply to the Call Track team's status report (12 Nov 2026)

For the Call Track app team. This answers section 6 of your report. The API contract is now **draft 3** (`Waba_Mobile_API_Contract_2026-11-12.md`): it holds the full field tables and the details below. Nothing in your report needs a server rule to change.

## 1. Access
- **Base URL: `https://uat-splashjet.sigzencloud.com`.** Waba is installed on UAT only. It is the same host as the existing `splinh.api.*` calls and uses the same token headers. `erp.splashjetink.com` is production: Waba is not there, so do not point the inbox at it. A token from production does not work on UAT; sign in again on UAT.
- **Test logins:** not released yet. We will tell you when they are. Until then build against the sample JSON below.
- **Receive-only test number:** every test chat will sit on a receive-only number, so a send is refused with the server's own message and nothing ever reaches a customer.
- **Sample JSON (real answers, fake data):** coming as `Waba_Mobile_API_Samples_2026-11-12.json`: `me`, `conversations` (open, unread, bot, unassigned, window closed, done), `summary`, `unread_total`, `thread` (text, every media kind, note, event, reaction, failed message), `quick_replies`, `templates_for`, `template_form` (before and after values), `my_numbers`, the privacy-block answer, a refused send. Correct your types against it before the field names spread.

## 2. Your 13 questions
1. **Template display name, category, description.** `title` is the display name, `category` is there (Utility; Marketing only if the company allows it). There is **no description**. Your default is right: show `title` and the category chip; hide the description.
2. **Window time on a list row.** Rows carry `window_expires_at`, `last_inbound_at` and `window_open`. Compute the countdown from `window_expires_at` and `me.server_time` (both site time, no zone; use their difference to correct the phone's clock). `seconds_left` is in `thread` only.
3. **Reply SLA.** None for the phone. The window countdown is the only indicator.
4. **"Needs reply".** Not a server field; your derivation (unread, window open, no bot) is fine.
5. **`view="all"` and counts.** `all` includes Done chats. `open` is status Open only. `summary` returns `open, unread, triage, unassigned` plus `can_oversee` and `available`; there is no count for done or all.
6. **Field names.** Contract section 4b lists every row and message field; the sample file settles the shape.
7. **`media.file`.** No range requests and no thumbnails; one whole-file response with content type and file name set. Your default (download to the private cache, play from there) is right.
8. **Duplicate protection: built.** Send an optional `client_key` (8 to 64 characters, a UUID is fine) with `send_text`, `send_template`, `send_document`, `send_library_file` and `send_device_file`. A repeat with the same key within about 10 minutes gets the first answer with `duplicate: true` and nothing more is sent. Details in contract section 4a. It becomes active on UAT after the next server restart.
9. **Dialable number.** `customer_phone` (digits as Meta gives them; the samples show the exact shape).
10. **Marketing templates.** Show whatever `templates_for(conversation)` returns; today that is Utility only.
11. **Several business numbers.** `conversations(number=None)` returns every number the person may see; `number` narrows.
12. **Push.** Parked; no date yet. The local-notification interim is fine: `unread_total` is one cheap count. Suggest 60 to 120 seconds with the app closed and 30 seconds (`summary`) while open. Generic text only, never customer text.
13. **`vectors.json`.** The 12 Nov file is current. You get a new file whenever the desktop rules change.

## 3. Polling rate
Your proposed rate (about 2 light calls a minute open, 1 a minute closed) is acceptable at this team size. The interval is yours to tune.

## 4. Separate items
- **OPPO Wi-Fi call direction:** noted; we will come back to it later, nothing needed from you now.
- **"Backend action items" in your `docs/CHANGELOG.md`:** please paste that list so we can see what is still open.

## 5. What we need back
- Confirmation that `client_key` is what you want for questions of double taps.
- When your first build can call UAT, tell us which two roles you need logins for (agent, team lead) so we can set them up.
