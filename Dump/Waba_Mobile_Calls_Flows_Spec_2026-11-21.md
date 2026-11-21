# Waba WhatsApp calls in the app: the exact flows (incoming and outgoing), copied from the browser phone

Written for: the Call Tracker app developers. Read together with `Waba_Mobile_Calls_Contract_2026-11-20.md` (the method list). This file says **what to call, in what order, and what to draw**. It is a line-for-line description of the browser phone (`waba_call_phone.js`), which is tested and works. If the app does something this file does not say, the app is wrong.

---

## 0. Why the screens got mixed up (read first)
 
Two things were causing "the outgoing screen on an incoming call":

1. **Server bug, fixed on 21 Nov:** `my_calls` also returned a call the person had *placed themselves* while it was ringing (an outgoing call is stored as ringing for its caller). A phone polling `my_calls` every 3 s therefore showed an **incoming ring screen for its own outgoing call**. `my_calls` now returns **incoming calls only**, and `offer` refuses a call you placed ("This is a call you placed, not one to answer."). *Restart UAT to have it.*
2. **App rule:** incoming and outgoing are **two separate flows with two separate screens and two separate state machines**. They share only the in-call screen (section 4), and the in-call screen is entered with a fixed `direction`, set once and never changed.

Golden rules:
- The **direction is decided by how the call started**: pressed the Call button = **Outgoing**; the call appeared in `my_calls` = **Incoming**. Never infer it from a status.
- **Incoming never shows "Calling…/Ringing…".** It shows Accept / Decline.
- **Outgoing never shows Accept / Decline.** It shows "Ringing…" and a Hang up button.
- The server decides everything (may call, who rings, who wins). The app only draws and carries audio.

---

## 1. HTTP methods (wrong method = `403 Not permitted`, which is NOT a permission problem)

| Method | HTTP |
|---|---|
| `waba.api.calls.my_calls` | GET |
| `waba.api.calls.can_call` | GET |
| `waba.api.calls.call_status` | GET |
| `waba.api.calls.offer` | **POST** |
| `waba.api.calls.answer` | **POST** |
| `waba.api.calls.decline` | **POST** |
| `waba.api.calls.hangup` | **POST** |
| `waba.api.calls.request_permission` | **POST** |
| `waba.api.calls.place` | **POST** |
| `waba.api.calls.answer_sdp` | **POST** (a poll, but POST) |

Every reply is Frappe's `{"message": ...}`. Errors are Frappe errors: show the server's message as it is.

---

## 2. Server call statuses and the words on screen

Stored statuses: `Ringing, Answered, Completed, Missed, No answer, Rejected, Failed, Cancelled`. Finished = every one except Ringing and Answered.

| Status | Outgoing screen says | Incoming (in-call) screen says |
|---|---|---|
| Ringing | "Ringing..." | "Incoming call" (ring screen, not the in-call screen) |
| Answered | "Connected" | "Connected" |
| Completed | "Call ended" | "Call ended" |
| Missed / No answer | "No answer" | (ring screen closes; Missed toast, section 3.5) |
| Rejected | "Declined" | (ring screen closes) |
| Failed | "The call failed" | "The call failed" |
| Cancelled | "Call cancelled" | "Call cancelled" |

`call_status` gives a ready-made `state` per person: `ringing, on_call, taken, ended, missed, no_answer, declined, failed, cancelled`, plus `mine`, `finished` and `answered_by_name`.

---

## 3. INCOMING flow (a customer calls our Waba number)

### 3.1 State machine

```
(nothing) --my_calls shows a new call--> RING SCREEN
RING SCREEN --Accept--> CONNECTING --answer ok--> IN-CALL (direction Incoming) --end--> ENDED
RING SCREEN --Decline--> closed
RING SCREEN --call leaves my_calls--> closed (then call_status once, 3.5)
RING SCREEN --countdown reaches 0--> closed
CONNECTING --answer {ok:false}--> closed + message (3.3)
```

### 3.2 Seeing the ring (polling, no Firebase)
1. Call `GET my_calls` every **3 s** (every **1 s** while a ring screen is open). Also once on app open and when the screen turns on.
2. Reply rows: `{call, phone, name, label, party_doctype, party, conversation, number, seconds_left, ring_expires_at}`.
3. A `call` you are not already showing = **new ring**: open the full-screen ring screen. A call you were showing that is no longer in the list = **stop ringing**.
4. Ring screen content (same as the browser's ring box): big title `label` (never build your own name); small line "Incoming WhatsApp call"; if `party` is set show it (the customer record); if `phone` differs from `label` show the phone; countdown from `seconds_left` (never from the time you received it); buttons **Accept** (green) and **Decline** (red); ringtone and vibration until it closes.
5. Maximum 5 rows; show the newest first; each has its own countdown. Opening a second ring while in a call is allowed (it shows on top of the call, as in the browser); accepting while already in a call is refused ("You are already in a call.").

### 3.3 Accept (exact order, same as the browser's `accept`)
1. If the app is already in a call: refuse, tell the person, stop.
2. Fix `direction = Incoming`, label = the ring's `label`, phone = the ring's `phone`.
3. Ask for the microphone (stop with a clear message if blocked; listen-only is not in this release).
4. `POST offer {call}` -> `{sdp, sdp_type:"offer", ring_seconds, caller, party_doctype, party}`.
   - Error "This call has ended." = the offer expired (kept 120 s) -> close the ring, say so.
   - Error "This call is no longer ringing." -> someone took it or it ended -> close the ring and use `call_status` for the message.
5. Create the peer connection with the microphone track; `setRemoteDescription({type:"offer", sdp})`; `createAnswer()`; `setLocalDescription(answer)`.
6. `POST answer {call, sdp: <your local SDP>}`:
   - `{"ok": true}` -> the call is yours: close **all** ring screens (the browser closes every ring), go to the **in-call screen** as **Connected**, start the timer **now**.
   - `{"ok": false, "reason": "Taken by <name>."}` -> release the microphone and peer, close the ring, show the reason.
7. If anything throws between steps 4 and 6, release the microphone and peer connection, close the ring and show the error.

### 3.4 Decline
Close the ring screen at once, then `POST decline {call}` (ignore the reply). The call keeps ringing for the other people; it is rejected only when everyone declined (a server setting). Nothing is shown afterwards.

### 3.5 When a ring disappears without you answering (use `call_status` once)
`GET call_status {call}` and show:
- `state: "taken"` -> toast "The call was taken by `answered_by_name`." (blue, 6 s).
- `state: "missed"` -> toast "Missed WhatsApp call from `<label>`" (orange, 15 s, with an **Open** button that opens the conversation; if there is no conversation, open the call). Play a short sound.
- `state: "declined"`, `ended` -> just close, no toast.
Do not guess between "taken" and "missed" from the list alone.

### 3.6 During the call
- In-call screen (section 4). Timer runs from the moment `answer` returned ok.
- Poll `GET call_status {call}` every **3 s**. If `state` is anything other than `on_call`, end the call screen (`ended` = "Call ended"); then `POST hangup {call}` once (safe to repeat).
- Also end when the WebRTC connection state stays disconnected, failed or closed for about 3 s.

---

## 4. In-call screen (shared by both directions; `direction` is fixed on entry)

Contents (identical to the browser call bar): status dot (amber ringing, green connected, grey ended), **who** (`label`, else the phone), sub-line (status words from section 2, then " · phone" when a name is shown), **timer** (`00:07`, `1:02:03`), **Mute** and **Hang up**.
- Mute = disable the local audio track (`enabled=false`); the button shows "Unmute" while muted.
- Hang up: `POST hangup {call}` and **immediately** show "Call ended" locally (the browser does not wait for the reply). If the reply is `{ok:false, reason}` show the reason as a small alert.
- When finished: replace the buttons with one **Close** button, keep the final timer visible, release microphone, peer connection and audio output, stop all polling.
- One call at a time: while `call` is set, the Call button and Accept are refused ("You are already in a call.").
- Audio: the speaker plays the remote stream. Audio-only; no video.

---

## 5. OUTGOING flow (the person presses the WhatsApp call button on a chat)

### 5.1 State machine
```
CHAT --press Call--> CHECKING --can_call--> (a) CONFIRM --place ok--> OUTGOING SCREEN "Ringing..." --Answered--> IN-CALL (Outgoing) --end--> ENDED
                                         --> (b) ASK PERMISSION --request_permission--> toast, back to chat
                                         --> (c) message only (cannot call now)
OUTGOING SCREEN --Rejected / No answer / Failed / 70 s--> ENDED (status words)
OUTGOING SCREEN --Hang up--> ENDED "Call ended"
```

### 5.2 Press Call (the browser's `startCall`)
1. If `me.calls.make` is not 1: say "You are not set up to place WhatsApp calls." and stop.
2. `GET can_call {conversation}` -> `{may, status, can_call, can_request, expires, reason}` (show a "Checking with WhatsApp..." spinner).
3. `may == false` -> show `reason` (orange), stop.
4. `can_call == true` -> confirm dialog: **"Call {name} on WhatsApp now?"** and under it **"The call is recorded (WhatsApp tells both people) and outgoing calls are billed per minute."** [Call] [Cancel]. On Call go to 5.3.
5. else if `status` is `no_permission` or `none` -> dialog: **"{name} has not allowed WhatsApp calls yet. Send them a message asking to allow calls?"** + **"They tap Allow in the chat, then you can call. WhatsApp allows one such request a day."** [Send] [Cancel]. On Send: `POST request_permission {conversation}`; then toast **"Request sent. Call again when the customer has allowed calls."** (green, 8 s). If the reply is `{ok:false, reason}` show the reason.
6. otherwise -> message with `reason` (or "A call to this customer is not possible right now.").

### 5.3 Place (the browser's `place`)
1. Refuse if already in a call.
2. Fix `direction = Outgoing`, label = the customer's display name, phone = their number.
3. Ask for the microphone (stop with a clear message if blocked).
4. Create the peer connection with the microphone track, `createOffer({offerToReceiveAudio:true, offerToReceiveVideo:false})`, `setLocalDescription(offer)`. No ICE candidates are needed (Meta answers as ICE-lite). STUN: `stun:stun.l.google.com:19302`, `stun:stun1.l.google.com:19302`.
5. Open the **outgoing screen** as **"Ringing..."** (status dot amber, Hang up visible, no Accept/Decline).
6. `POST place {conversation, sdp: <your offer SDP>}` (spinner "Calling..."):
   - `{ok:true, call:"<id>"}` -> remember `call`, note the start time, go to 5.4.
   - `{ok:false, reason, need_permission}` -> close the screen, release everything, show "Call not placed" with `reason`. (`need_permission:true` = go to the 5.2 step 5 dialog.)
   - Typical reasons: "There is already a call in progress with this customer.", permission texts, Meta errors.

### 5.4 After place (poll, exactly like the browser)
Every **1 s**, up to **70 s**, `POST answer_sdp {call}` -> `{sdp, status}`:
- `status` is finished (`Rejected, No answer, Failed, Completed, Cancelled, Missed`) -> end the screen with those words (section 2) and release everything.
- `sdp` is present and no remote description is set yet -> `setRemoteDescription({type:"answer", sdp})`. Do this once.
- After 70 s with no answer -> end as "No answer" (and `POST hangup` to be safe).
- The call counts as **connected** when `status == "Answered"` (in `answer_sdp`, or `call_status.state == "on_call"`): change the screen to **"Connected"**, green dot, **start the timer then** (not at dial time).
- Keep polling `call_status` every 3 s after connection to notice an end that the audio link did not report (same rule as 3.6).

### 5.5 Outgoing ends
Hang up -> 4. Customer hangs up or Meta ends it -> `status` becomes finished -> "Call ended" and Close.

---

## 6. Wrong behaviours we have seen, and the fix

| What the app shows | Cause | Fix |
|---|---|---|
| Incoming ring screen for a call the person just placed | Old server returned own outgoing calls in `my_calls` | Fixed on the server (restart). In the app: never open a ring for a call that this app placed (compare with the `call` returned by `place`). |
| Outgoing "Calling…/Ringing…" bar for an incoming call | One shared screen state without a fixed direction | Two flows (sections 3 and 5); direction fixed on entry; in-call screen takes `direction` as an argument |
| 403 "Not permitted" on `offer`, `answer_sdp` | GET sent to a POST method | Use the table in section 1 |
| "This call has ended." on Accept | Offer expired (120 s) or tried to accept a call that was placed by the person | Only Accept rows from `my_calls`; show the message and close |
| Ring does not go away after another device took it | Not noticing the call left `my_calls` | Compare each poll with the previous list; close rings that vanished; then `call_status` |
| Timer starts when dialling | Timer started at `place` | Start only when Answered (outgoing) or after `answer` ok (incoming) |
| Both "Missed" and "Taken" shown wrongly | Guessing from the list | Use `call_status.state` |

---

## 7. Conformance checklist (the app is "identical to the browser" when all pass)

Incoming: ring appears within about 3 s with `label`; countdown equals `seconds_left`; Decline closes only this phone; Accept shows "Connected" and starts the timer at once; taken elsewhere shows "taken by NAME" toast; ring that times out shows the Missed toast with Open; ringing on phone and browser at once, first Accept wins.
Outgoing: Call button -> can_call -> confirm text exactly as 5.2; permission request path; "Ringing..." with Hang up only; "Connected" and timer only after the customer answers; Declined / No answer / The call failed words; cancel while ringing shows "Call ended".
Both: one call at a time; Mute/Unmute; Close button after the end; microphone and connection released; a Call Log with a recording appears on the server a minute after an answered call.
A placed call never produces an incoming ring on the same phone.

---

## 8. Notes
- Listen-only is not part of this release. When it comes back: our browser does **not** use a muted track; it sends a real audio tone (997 Hz at about -62 dB, gain 0.0008) because Meta did not record a call that carried no real signal. Use the same tone, not a disabled microphone track.
- Push (Firebase) is optional later; polling is the first release. The push kinds are `ring, taken, dismiss, ended, missed`, equivalent to the polling results above.
- Cost: incoming calls are free; outgoing calls to Indian numbers are about Rs 0.39 a minute.
