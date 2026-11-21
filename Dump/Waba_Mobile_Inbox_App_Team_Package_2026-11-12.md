# Waba team inbox on the phone: package for the app team (2026-11-12)

**What this is.** The company's WhatsApp team inbox (built in the ERP as the Waba app) is to appear as a new tab inside the Call Tracker Android app, for inside sales and telecallers first. The screens are yours. The server side is ours. This package tells you what to build against and how we will test it together.

## Documents
1. `Waba_Mobile_API_Contract_2026-11-12.md`: every call, its arguments, answers, the privacy-block flow, the screens to mirror. Start here.
2. `apps/waba/docs/mobile_vectors/vectors.json`: shared test cases from the desktop inbox's own code. Your helpers must match them.
3. `CallTracker_App_Login_Handoff_2026-10-22.md`: sign-in and token headers (unchanged).

## The one rule
**The phone does what the desktop does, because it calls the same server code.** The server decides who sees which chat, whether a message may go out (24-hour window, privacy block, consent, receive-only numbers), and what a Lead may override. The app never re-decides these. If you find the app needing to guess, ask us: that is a gap on our side.

## What is switched on and when
- Everything inbox-related is **off until a Waba Admin ticks "Phone inbox (Call Tracker app)"** in Waba Settings. While it is off every inbox call answers 403. We will switch it on for the shared test window and for the pilot.
- A user needs the Call Tracker User role and the app tick box (as today) **and** a Waba role (Waba Agent, Supervisor or Admin). Without a Waba role the inbox calls answer 403: show no Inbox tab.
- The IP rule for outside-office phones is the same as for the other Call Tracker calls.

## What is NOT in the first release
Push notifications when the app is closed (parked, decision pending; Android only, details later); sending voice notes (received ones can be played); the calls-plus-chat timeline, WhatsApp from the call log, the after-call prompt and the customer card (deferred). While open, refresh on foreground and every 30 seconds with `summary`.

## What we need from you
- A build with the Inbox tab behind a server-driven check (call `waba.api.mobile.me`; if it succeeds show the tab).
- Media requests (images, audio, documents) carry the Authorization headers. A bare URL will not work.
- The privacy-block and override dialog exactly as in the contract.
- Android only for now.

## How we test together (UAT, test numbers only, no real customer is messaged)
1. We give you a UAT test telecaller login (Waba Agent) and a test Lead login, and switch the inbox on for the window.
2. A seeded test conversation on a receive-only test number: sending is refused with the server's own message, which is the expected result.
3. Checklist: list and chips and Mine/Team scope, search, thread and window banner, composer (reply, note, quick reply, template form, closed-window state), file from phone and ERP document and library file, privacy block and Lead override, transfer and return to waiting list and done/reopen, reminders, Available/Away, bot banner and Take over, media viewing with the header, revoked token and lost-phone behaviour.
4. Anything the server refuses should show the server's message, not a generic error.

## Contacts and ownership
Server calls, rules and this contract: the Waba/ERP side (apps/waba and apps/splinh). Screens, build and release: the app team. Questions about a rule: ask before building around it.
