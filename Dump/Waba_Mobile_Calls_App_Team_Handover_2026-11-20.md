# WhatsApp calls in the Call Tracker app: handover to the app team (20 Nov 2026)

Written for: the Call Tracker Android app team. The server side is finished and UAT-checked (19/19 with a fake Meta). This document tells you how the web version behaves so the app copies it, and what keeps both in sync. The exact API is in `Waba_Mobile_Calls_Contract_2026-11-20.md` (draft 2). Read that next; do not guess any call from this page.

## 1. The model in one paragraph
Customers call our WhatsApp Business number (Meta's Calling API). The server receives the call, decides who rings (same rule for browser and phone), and keeps Meta's SDP offer for 120 seconds. Whoever accepts sends back an SDP answer; the audio then flows directly between that device and Meta (WebRTC). The app is only a WebRTC client plus a ring screen. Everything else (who may call, permission, recording, Call Log in the ERP) is decided and done by the server, so nothing about it is re-decided in the app.

## 2. What you must NOT do
- Do not decide who may take or place calls: read `me.calls` (take, make, listen_only, ring_seconds) and show screens only from that.
- Do not time the ring yourself: use `seconds_left` / `ring_expires_at` from `my_calls`.
- Do not record audio, create Call Logs, or send anything to Meta. The server does all three.
- Do not call any method outside the contract; the token only works for the listed ones and only while the switches are on (otherwise 403).

## 3. How it works on the web (copy this behaviour)
**Incoming call**
1. A ring box appears at once, on any page: the caller (`label`: the Lead/Customer name, else a real WhatsApp name, else the phone number), a countdown from `seconds_left`, and **Accept** / **Decline**. A name that is only "." or symbols is never shown (the server already cleans it).
2. A ringtone loops until the call is answered, declined, taken by someone else, ended or timed out. Tones are chosen once for everyone in Waba Settings; the app should offer the same four by the name returned (`Classic phone ring` default, `Soft chime`, `Digital`, `Beep`) or its own tone for all; the web also blinks the tab title and sends a desktop notification when the page is hidden. On the phone: full-screen call UI, vibration, system ringtone are fine.
3. **Accept:** ask for the microphone, `offer`, build the SDP answer, `answer`. If the server says `{"ok": false, "reason": "Taken by Asha."}` another person already won: show the reason and close the ring. If the offer has expired the server says "This call has ended."
4. In the call: a bar with caller, **timer (00:07)**, **Mute** and **Hang up**. Hang up calls `hangup`. When the customer hangs up, Meta closes the audio connection: end the call screen when the WebRTC connection state becomes disconnected, failed or closed (and after a short wait, about 3 s, to ignore a blip). Also poll `call_status` (contract section 11) every 3 s during the call and close when its `state` is no longer `on_call`; calling `hangup` afterwards is always safe. For an outgoing call, `answer_sdp` also returns the call's status while you wait.
5. **Decline** only hides the call for that person; it keeps ringing for others and is rejected for the customer only when everyone rung has declined or it times out (a setting can make the first decline reject at once; the app does nothing different).
6. **Several people ring at once** (and one person's browser plus phone). First `answer` wins under a server lock; the others see "Taken by <name>" and the ring closes.
7. **Missed call:** a toast "Missed call from <label>" with an **Open** button (to the chat) to everyone who was rung and the Team Leads. On the phone: a normal notification opening that chat.
8. **No microphone:** if `listen_only` is 1 and the device has no usable microphone (not "permission denied" - a person who blocks the microphone chose that), the person may still take or place the call and only listen; the customer hears silence. The web sends an inaudible tone so Meta still carries and records the call. On the phone, send a near-silent audio track (about 997 Hz at -62 dB) the same way when listen-only.

**Outgoing call (only when `me.calls.make` = 1)**
1. On a customer's chat, a **Call** button. Tap: `can_call(conversation)`.
2. `can_call` true: ask for the microphone, create an audio-only SDP offer (offerToReceiveAudio true, video false; no ICE candidates needed, Meta answers as ICE-lite; Google STUN servers are used), `place(conversation, sdp)` -> `{"ok":true,"call":"..."}`, then poll `answer_sdp(call)` every second (give up after 70 s), set the remote description, talk.
3. `can_call` false and `can_request` true: offer **Ask for permission**; `request_permission` sends the customer a WhatsApp message (inside their 24-hour window) and the customer taps Allow. Tell the person: "Asked. You can call once the customer allows." Meta limits 1 request per 24 h and 2 per 7 days per customer; the server explains in `reason`.
4. `place` returning `{"ok":false,"reason":"...","need_permission":true}` means the same as step 3. A second call to a customer already on a call is refused with a reason.
5. Calls cost money to Indian numbers (about Rs 0.39 a minute, 6-second billing): show a small note before placing. Customer-initiated calls are free.

**What the ERP shows after any call (so both stay in sync, nothing for the app to send)**
- The chat gets a timeline line ("Incoming call answered by Ayush, 1m 02s", "Missed call.", "Incoming call declined.", ...).
- The Call Log (source "WhatsApp API") is created when the call ends: owner = the person who answered or placed it (Administrator when nobody answered), their Employee, the customer's name or "+number", duration, status, and the recording about a minute later if recording was not paused for that person.
- Reload of any screen shows the same data: the app never needs its own call history.

## 4. Sync rules (how the app and the web stay identical)
| Thing | Web | App must |
|---|---|---|
| Who rings | server `ring_list` | just show `my_calls` |
| Ring length | `ring_seconds` setting (30 default) | use `seconds_left` |
| Winner | server lock in `answer` | handle `{"ok":false}` |
| Taken/ended | realtime event | stop when the call leaves `my_calls` (or a push) |
| Rights | `me.calls` / Waba Agent Access | hide screens by `me.calls` only |
| Listen-only | setting `listen_only` | follow it |
| Ringtone | setting, same for all | same names or own tone for all |
| Call history | ERP Call Log + chat timeline | no local history needed |

## 5. Sample JSON (invented values)
`me.calls`: `{"enabled":1,"take":1,"make":0,"listen_only":1,"ring_seconds":30}`
`my_calls`: `[{"call":"eocmf188vb","phone":"+919876500000","name":"Asha","label":"Mystic Aromatics","party_doctype":"Lead","party":"CRM-LEAD-2026-00001","conversation":"abc123","number":"WN-0001","seconds_left":21,"ring_expires_at":"2026-11-20 12:00:30"}]`
`answer` lost: `{"ok":false,"reason":"Taken by Asha Rao."}`
`can_call` not allowed: `{"may":true,"status":"no_permission","can_call":false,"can_request":true,"expires":null,"reason":"This customer has not allowed calls yet. Ask for permission first."}`
`place` ok: `{"ok":true,"call":"eocmf188vb"}`; `answer_sdp` waiting: `{"sdp":null,"status":"Ringing"}`.

## 6. Switches (owner turns on when you are ready)
Waba Settings: **Phone inbox**, **WhatsApp calls**, **WhatsApp calls on the phone app**. All three off today on UAT. Tell the owner when your build is ready; calls from your build will then return real data instead of 403.

## 7. First-release scope and background running
In: receive, make, listen-only, ring screen, mute, hang up, missed-call notification, permission request. Out: video, transfer, conference, call history screen, in-app recording controls. Firebase push is optional and later; first release polls `my_calls` every 3 s from a foreground service (contract section 4). Test on the OPPO and Samsung phones with battery saver on.

## 8. Test plan and sign-off
Run the UAT plan in contract section 9 with test numbers only, and send us a recording of the ring, accept, talk, hang up flow. We check the Call Log and recording land in the ERP. Questions that change an API go to the owner first; additive changes are cheap for us, breaking ones are not.
