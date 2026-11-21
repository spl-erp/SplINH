# Waba team inbox on the phone: API contract (draft 3, 12 Nov 2026)

For the Call Tracker app team. Status: **draft 3**. Anything marked *pending* is not built yet. Draft 2 added the device-file upload, the document picker and the test vectors. Draft 3 adds `client_key` (no double sends), the field tables, the exact meaning of `view` and `summary`, and how files and clocks behave. Real sample JSON follows in a separate file. Base URL while testing: **https://uat-splashjet.sigzencloud.com** (Waba is not installed on production yet; a production token does not work on UAT). Nothing here works until a Waba Admin ticks
**Waba Settings > Phone inbox (Call Tracker app)** (off by default); until then every call below answers 403.

## 1. The rule that matters most
The phone uses the **same server calls as the desktop inbox**, so visibility, the 24-hour window, privacy blocks, consent, receive-only
numbers and team rules are decided on the server and are identical on both. The app must **never** re-decide them; it only displays and
asks. If the desktop does something, the phone does the same thing by calling the same method.

## 2. Sign-in and headers (unchanged from the Call Tracker)
- Sign in with `app_login_start` / `app_login_verify`; keep the token (key + secret) per device.
- Every request: `Authorization: token <key>:<secret>` and `Frappe-Authorization-Source: Call Tracker Token`. Do not send a session cookie.
- All paths are `/api/method/<method>`. POST calls take form fields or JSON. Use only the calls listed here: anything else is 403.
- Who may call: an enabled user with the Call Tracker User role, the app tick box, **and a Waba role** (Waba Agent, Supervisor or Admin).
  A user without a Waba role gets 403 from every inbox call. The app should show the Inbox tab only when `me` succeeds.
- Errors are normal Frappe errors: 401 (token ended: sign in again), 403 (not allowed), 404, 417/ValidationError with a plain-words
  `exc`/`_server_messages` text that is meant to be shown to the person as is.

## 3. First call after sign-in
`waba.api.mobile.me` (GET) returns:
`{api_version, user, full_name, is_admin, can_oversee, numbers[{name, display_name, display_number, enabled}], counts{open, unread, triage, unassigned, can_oversee, available}, unread_chats, available, limits{media_bytes{image, video, audio, document, sticker}, caption_chars}, server_time}`.
`api_version` is 1. If the app is built for 1 and the server says something else, the app should show "update the app".
`can_oversee` is true for team Leads, supervisors and admins: show the **Team** scope and the Unassigned tab only then.

## 4. The calls (same names, same meaning as the desktop)
Lists and reading (GET):
- `waba.api.inbox.conversations(view="open", number=None, search=None, start=0, limit=30, scope="all")` view is open, unread, triage, unassigned, done or all; scope is all, mine or team (only the options `me` allows). Returns `{rows, has_more}`; each row has the label, phone, last message, `unread_count`, `window_open`, assignee name, `bot`.
- `waba.api.inbox.summary(number=None, scope="all")` tab counts; `waba.api.inbox.unread_total()` one number.
- `waba.api.inbox.thread(conversation, limit=60, before=None)` header (including `window_open`, `seconds_left`, `can_transfer`, `can_unassign`) and messages oldest first, notes and events included, `has_more` for older ones (`before` = the time of the oldest one you have).
- `waba.api.inbox.my_numbers()`, `quick_replies()`, `transfer_targets(conversation)`.
- `waba.api.send.templates_for(conversation)`, `template_form(conversation, template, values=None)` (the template pieces, the variables to fill, what is still missing, a rendered preview; call again with `values` to re-preview), `shared_files()`.
- `waba.api.party.search_people(query)`, `context(doctype, name, utility_only=0)`, `template_form(doctype, name, number, template, values)` for starting a chat with someone from the ERP (Utility templates only on the phone, as on the desktop).
- `waba.reminders.my_reminders(view="open")`.
- Media: `waba.api.media.file(message, download=0)` (GET). **It needs the Authorization headers on every request**, so the image loader, the audio player and the document download must send them; a bare URL does not work. Files are never public.

Doing things (POST):
- `waba.api.send.send_text(conversation, body, reply_to=None, override_reason=None)`
- `waba.api.send.send_template(conversation, template, params=None, override_reason=None)`
- `waba.api.send.send_document(conversation, doctype, name, caption="", reply_to=None, override_reason=None)` an ERP document as a PDF (Quotation, Sales Invoice and the other printable types)
- `waba.api.send.send_library_file(conversation, shared_file, caption="", reply_to=None, override_reason=None)`
- `waba.api.send.send_reaction(message, emoji="")`, `retry_message(message)`, `mark_read(conversation)`
- `waba.api.inbox.add_note(conversation, body)` (never sent to the customer), `set_status(conversation, status)` (Open or Done), `transfer(conversation, to_user, note)`, `unassign(conversation, note)` (return to the waiting list), `take_over_from_bot(conversation)`, `set_available(available)`
- `waba.reminders.remind(conversation, remind_at, note="")`, `done_reminder(reminder)`, `cancel_reminder(reminder)`
- `waba.api.party.start_chat(doctype, name, number, phone, template, params=None)` start a chat from a record with an approved Utility template. If a chat already exists the answer says so and gives its name.
- `waba.api.media.retry(message)` try the download of a received file again.
- `waba.api.mobile.send_device_file(conversation, caption="", reply_to=None, override_reason=None, filename="")` **a file from the phone's storage.** POST **multipart/form-data** with the file in the field named `file` (plus the other fields as form fields). The answer is the same as the other sends (`{ok, message, ...}`, or the privacy-block answer). The server decides everything: allowed types (images JPG/PNG, MP4/3GP video, audio, PDF/Office/text, WebP as a sticker), size per kind (the limits come in `me`), the file's contents must match its type, no caption on audio or stickers, the 24-hour window, the privacy scan. Show the server's message as it is. Do not use any other upload call: the general file upload is not available to the token.
- `waba.api.mobile.documents_for_chat(conversation, doctype, query="")` (GET) the picker for **an ERP document as a PDF**. `doctype` is Quotation, Sales Order, Sales Invoice or Delivery Note. Returns up to 20 rows `{doctype, name, title, date, total, currency, status, draft, own}`: first the chat's own customer's documents (`own: true`), then, when `query` has 2 or more characters, other documents the person may read that match the name or customer (what the desktop's document box offers). A person with no access to that document type gets an empty list. Cancelled documents are never listed; drafts are (`draft: true`: show a "Draft" label). Send the chosen one with `send_document`.

**Privacy block.** A send that contains something sensitive (Aadhaar, PAN, card, bank account, UPI, OTP) answers `{ok: false, blocked: true, can_override, found:[{label, sample}], error}` and nothing is stored or sent. If `can_override` is true (Leads, supervisors, admins) the app asks for a reason and calls the same method again with `override_reason`. An agent is told to ask a Lead. This is the same dialog as on the desktop.

**Closed window.** When `window_open` is false the composer offers templates only (`templates_for`), exactly like the desktop. The server refuses a free-text send anyway.

## 4a. No double sends: `client_key`
`send_text`, `send_template`, `send_document`, `send_library_file` and `send_device_file` take an optional `client_key`: a string you create **once per send attempt** (8 to 64 letters, digits or `- _ . :`; a UUID is fine). If the request times out and the person taps Send again, **send the same key**. The server remembers a send that worked for about 10 minutes (per person and conversation); a repeat gets `{ok: true, message, message_id, duplicate: true}` and nothing more is sent to the customer. A send that was refused or failed is not remembered, so it can be tried again with the same key. A new attempt (the person edited the text, or tapped Send on a different message) gets a new key. Without a key there is no protection.

## 4b. Field tables
**Conversation row** (`conversations`): `name, customer_label, customer_phone` (the number to dial; digits as Meta gives them, see the samples), `customer_bsuid, profile_name, match_state` (Matched or Triage), `party_doctype, party, status` (Open or Done), `unread_count, waba_number, last_message_at, last_message_preview, last_inbound_at, window_expires_at` (when the 24-hour window closes), `assigned_to` and the assignee's name, `window_open` (true or false, decided by the server) and the bot's name when a bot is handling the chat (the sample file shows the exact keys). **Time left in a row:** compute it from `window_expires_at` and the server's `server_time` from `me` (both are site time without a zone: use their difference to correct for the phone's own clock). `thread` also gives `seconds_left`.
**Message** (`thread`): `name, direction` (In or Out), `message_type` (text, image, audio, video, document, sticker, location, contacts, template, interactive and a few others; the sample file shows each one), `body, status` (Queued, Sent, Delivered, Read, Failed), `sent_at, delivered_at, read_at, error_code, error_message` (plain words, show as is), `waba_template, sent_by` (user id; the display name is added), `origin` (Person, Notification, Reminder, Campaign, Auto-reply, Phone app, System), `message_id, reply_to_message_id` (with the quoted text), `media_mime, media_filename, media_size, media_status` (None, Pending, Downloaded, Failed), `media_error, voice` (a voice note), `latitude, longitude, contacts_json, customer_reaction, our_reaction, retried, bot`. `notes` holds internal notes and event lines (kind Note or Event, text, user, time); merge them with the messages by time.
**Template** (`templates_for`): `name, title` (the display name), `category` (UTILITY; MARKETING only if the company allows it), `body_text, variable_count, language, header_format`. There is no description field. The list already contains only what may be sent in this chat.

## 4c. What the list calls mean
`conversations(view=...)`: `open` = status Open; `unread` = unread count above 0; `triage` = unmatched senders that are Open; `unassigned` = Open and nobody assigned (only people who oversee a number see any); `done` = status Done; `all` = every chat the person may see, **including Done**. Without `number`, all of the person's numbers are returned; `number` narrows to one. `summary` returns the counts `open, unread, triage, unassigned` plus `can_oversee` and `available`; there is **no count for done or all**. `unread_total` is one cheap number (the red badge).
**Media (`media.file`):** one whole-file response (no range requests, no thumbnails), with the content type and file name set. Download to a private cache and play from there. Images, audio and video are inline types, a PDF is a download, any other type is a neutral download.
**Refresh rate:** while the app is open `summary` every 30 seconds and on returning to the foreground; with the app closed (until push exists) `unread_total` every 60 to 120 seconds. The interval is yours to tune; a light call each is fine at this team size.

## 5. Not available yet (pending)
- Push notifications (device registration and alerts when the app is closed). Stage M-B.
- Recording and sending voice notes (received ones can be played).
- Realtime while open: use a refresh on foreground, on push (M-B) and a poll of `summary` every 30 seconds. Do not depend on websockets.

## 6. Screens to mirror (parity checklist)
Conversation list with the filter chips (Open, Unread, Triage, Unassigned, Done), Mine / My team switch and number picker, search; thread with date separators, sender label under every outgoing message, ticks, failed message with Retry, the window banner ("closes in 6h 40m", closed state), unread divider and jump-to-latest; composer with Reply / Note switch, emoji, quick replies, template picker and form, attach (library file, ERP document), reply-to quote; privacy block and override dialog; notes and event lines; transfer (with required note), return to waiting list, mark done / reopen; reminders; Available / Away switch; the "Bot ... is handling this chat" banner with Take over; the "Auto-reply · bot name" sender label; media cards (image, audio with speed, video, document, location, contact, reaction chips).
The desktop's helper rules are supplied as **test vectors**: `apps/waba/docs/mobile_vectors/vectors.json` (time left in the window, WhatsApp text formatting, quick-reply filter, template fill, missing slots, first unread, reminder choices, upload checks, file sizes, tick states). Each case has the input and the expected output produced by the browser's own code. The app's implementation must give the same answers. They are regenerated whenever the browser's rules change, so ask for the latest file before each release. (The formatting vectors show the expected result as simple tags `<b> <i> <s> <code>`; the app may draw them natively.)

## 7. Safety
Push alerts will carry no customer text. The token can call only the methods above; the bot builder, settings and consent editing are not reachable. A lost phone is handled by revoking the Call Tracker Token (all inbox access ends with it).
