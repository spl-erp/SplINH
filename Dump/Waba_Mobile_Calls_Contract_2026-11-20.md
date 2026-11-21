# Waba WhatsApp calls on the phone: API contract for the Call Tracker app team (draft 2, 20 Nov 2026: polling is the main way to ring; push is optional and later)

Written for: the Call Tracker Android app team. Same pattern as the phone-inbox contract (Waba_Mobile_API_Contract_2026-11-12.md): the app calls the same server methods the browser phone uses; the server decides everything, the app never re-decides.

**See also `Waba_Mobile_Calls_Flows_Spec_2026-11-21.md`: the exact incoming and outgoing screen flows, copied from the browser phone.**

## 1. The one rule
Take and place WhatsApp Business calls on our Waba number inside the app. The server speaks to Meta; the app only carries audio (WebRTC) and sends/receives the SDP text. Nothing here is a SIM call.

## 2. Switches (all must be on, else every call below is 403)
- Waba Settings: "Phone inbox" and "WhatsApp calls" (calling) and **"WhatsApp calls on the phone app"** (new, off by default).
- The person must have a Waba role; taking needs `may_take_calls`, placing needs `may_make_calls` (set by their team).
- `GET waba.api.mobile.me` now returns `api_version: 2` and a `calls` block. Show phone-call screens only when `calls.enabled == 1`:
```json
"calls": {"enabled": 1, "take": 1, "make": 0, "listen_only": 1, "ring_seconds": 30}
```
`listen_only: 1` means a person without a microphone may still take or place a call and only listen (the customer hears silence). `ring_seconds` is how long a call rings.

## 3. Sign-in and token
As the inbox: `Authorization: token key:secret` and `Frappe-Authorization-Source: Call Tracker Token`. Errors are plain Frappe errors (401/403/417); show the message as is.

## 4. How the phone learns a call is ringing
**Main way (first release, no Firebase): poll.** While the app's background service runs, call `GET waba.api.calls.my_calls` every **3 seconds** (every 1 s while a ring screen is open, to notice `taken`/`ended` quickly). It returns only calls ringing for this person right now; an empty list means nothing rings. New call in the list -> show the full-screen incoming call; call no longer in the list -> stop ringing (someone took it, it ended, or it timed out). Also call it once when the app opens or the screen turns on. Use `seconds_left` / `ring_expires_at` for the countdown, never a timer started at receipt. Do not depend on websockets.
Keep the service alive: foreground service with an ongoing notification, battery-optimisation exemption, and test on the OPPO and Samsung phones. If Android stops the service, the phone cannot ring; that is the one thing Firebase would fix later.

**Optional later (Firebase push).** The server side is built but dormant until the owner stores a Firebase key. When enabled: `POST splinh.api.call_tracker_auth.register_push_token` (`push_token`, `platform` "android") after sign-in and whenever Firebase rotates the token; `POST ...unregister_push_token` to stop; logout removes it. The push is data-only, high priority, never holds a name, number or text:
```json
{"type":"waba_call","kind":"ring","call":"eocmf188vb","ring_expires_at":"2026-11-20 12:00:30"}
```
`kind`: `ring` (then call `my_calls` for the caller), `taken`, `dismiss`, `ended` (stop ringing), `missed`. Polling stays as the catch-up even with push.

## 5. The ten call methods (see the HTTP-method note under the table)
| Method | Purpose | Reply |
|---|---|---|
| `GET waba.api.calls.my_calls` | Incoming calls ringing for me now (max 5; never a call I placed) | `[{"call","phone","name","label","party_doctype","party","conversation","number","seconds_left","ring_expires_at"}]` (`label` is what to show) |
| `POST waba.api.calls.offer` `call` | The customer's SDP offer | `{"sdp","sdp_type":"offer","ring_seconds","caller","party_doctype","party"}`; error "This call has ended." once the offer expired (it is kept 120 s) |
| `POST waba.api.calls.answer` `call`, `sdp` | Accept with your SDP answer | `{"ok":true}` or `{"ok":false,"reason":"Taken by <name>."}` |
| `POST waba.api.calls.decline` `call` | Decline for me | `{"ok":true}`; the call keeps ringing for others unless the setting makes a decline reject it |
| `POST waba.api.calls.hangup` `call` | End a call I am on | `{"ok":true}` |
| `GET waba.api.calls.can_call` `conversation` | May I call this customer, and what is their permission | `{"may","status","can_call","can_request","expires","reason"}` |
| `POST waba.api.calls.request_permission` `conversation`, `text?` | Ask the customer for call permission (a WhatsApp message; only inside their 24-hour window) | `{"ok":true}` or `{"ok":false,"reason"}` |
| `POST waba.api.calls.place` `conversation`, `sdp` | Place a call with your SDP offer | `{"ok":true,"call":"<id>"}` or `{"ok":false,"reason","need_permission"}` |
| `POST waba.api.calls.answer_sdp` `call` | Poll (every 1 s, up to 70 s) for the customer's SDP answer after `place` | `{"sdp":null,"status":"Ringing"}` until it arrives, then `{"sdp":"v=0..."}` |
| `GET waba.api.calls.call_status` `call` | Where a call stands for me (works after it left `my_calls`) | `{"call","status","direction","state","mine","finished","answered_by_name","started_at","ended_at","duration"}`; `state` is `ringing`, `on_call`, `taken`, `ended`, `missed`, `no_answer`, `declined`, `failed`, `cancelled`; `answered_by_name` only for `taken`; 403 if the call is not yours |

**HTTP methods matter:** the server refuses the wrong one with `403 Not permitted` (this is not a permissions problem). Only `my_calls`, `can_call` and `call_status` are GET; `offer`, `answer`, `decline`, `hangup`, `request_permission`, `place` and `answer_sdp` must be POST (answer_sdp is a poll, but it is POST).

## 6. Flows
**Incoming:** `my_calls` shows a new ring -> show screen (Accept / Decline, countdown from `seconds_left`) -> Accept: ask microphone permission (skip when `listen_only` and none), `offer`, create the SDP answer, `answer`. If `{ok:false}` show the reason and close. During the call keep the screen until `ended`/`hangup`. Decline: `decline`. When the call leaves `my_calls` (or a `taken`/`dismiss`/`ended` push arrives), stop ringing at once.
**Outgoing (only when `calls.make == 1`):** `can_call` -> if `can_call` true: create the SDP offer, `place`, poll `answer_sdp`, set the remote description, talk; `hangup` to end. If not allowed and `can_request`: offer "Ask for permission" -> `request_permission`; tell the person the customer must tap Allow in WhatsApp, then they can call. `need_permission` in a `place` reply means the same. Meta limits: 1 permission request per 24 h and 2 per 7 days per customer; the server enforces and explains in `reason`.
**SDP rules:** plain text starting `v=0`, audio only, Opus (the browser phone uses the same), no video. The server refuses anything else with "The call description is not valid."
**Two places:** a person logged in on the browser and the phone rings on both; the first `answer` wins and the other gets `taken`.

## 7. Recording and cost (information for the app)
Meta plays a recording announcement to the customer; recordings arrive on the server about a minute after the call and appear on the call and its ERP Call Log. The app does nothing for recording. Customer calls to us are free; our calls to Indian numbers cost about Rs 0.39 a minute, so show the person when they are placing a call.

## 8. Not in the first release
Video calls, call transfer, conference, in-app call history screen (the ERP Call Log has it), call recording controls.

## 9. UAT plan (test numbers only)
1. Switches off: every call method returns 403; `me.calls.enabled` is 0.
2. Switches on, app in the background (service running), customer calls: phone rings full-screen within about 3 seconds; accept; audio both ways; hang up; Call Log appears with recording.
3. Same user on browser and phone: both ring; accept on phone; browser stops with "Taken".
4. Decline on phone; missed call (let it ring out): missed notice, Call Log status No Answer.
5. Listen-only on a phone without microphone permission.
6. Outgoing to a number without permission: `can_request`, request, customer allows, call connects.
7. App was asleep and wakes after the ring window: no ring shown (`my_calls` empty).
8. Sign out: token removed.

## 10. What we need from the app team
A test device, confirmation of the Opus/WebRTC stack and how the background service is kept alive. Firebase details only when push is switched on later.

## 11. Add-on: call_status (20 Nov 2026)
When a call leaves `my_calls`, call `call_status` once: `missed` -> "Missed call", `taken` -> "Taken by <answered_by_name>", `declined`/`ended` -> just close. During a call you are on, poll it every 3 s; `finished: true` or a `state` other than `on_call` means close the call screen even if the audio link is still up, then `hangup` (safe to repeat). Listen-only is not part of this release.
