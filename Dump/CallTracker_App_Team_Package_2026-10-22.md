# Call Tracker app: everything the backend now offers (one-page index for the app team)

Written for the Android app team. Four documents belong together; this page says what each one is, what is live, and what we need confirmed.

## 1. The documents
| # | Document | What it covers | Backend status |
|---|---|---|---|
| 1 | `Call_Review_API_Handoff_for_App_Team_2026-10-15.md` | `submit_call_review` (incl. `call_category`, `auto`, `force`, 409 conflict), `get_call_review_options`, `get_my_call_reviews` | **Live** |
| 2 | `Call_Review_UI_Implementation_Guide_2026-10-15.md` | Screens, states and a test plan for the review feature | **Live** |
| 3 | `CallTracker_App_Login_Handoff_2026-10-22.md` | New sign-in that works outside the office: password, then (new phone, outside the office) an OTP that goes **only to the administrators**; own token; `app_logout`; scope of the token | Code ready; **live after a server reload and switch-on** |
| 4 | `CallTracker_Recording_Toggle_Handoff_2026-10-22.md` | Per-user "Record Calls" pause/resume; `get_my_app_config`; heartbeat answer; server answer `skipped` | Code ready; **live after the same reload** |

## 2. What to build, in one list
1. Review feature (docs 1 and 2): lists from the options endpoint, `auto` for missed calls, 409 dialog.
2. Sign-in (doc 3): `app_login_start` then `app_login_verify`; keep the key and secret; send both headers on every call (`Authorization: token key:secret` and `Frappe-Authorization-Source: Call Tracker Token`); **never send the `sid` cookie**; `app_logout` on logout; on 401 ask the rep to sign in again and keep data queued.
3. Recording toggle (doc 4): read `get_my_app_config` (or the heartbeat answer); never upload the recording of a call that started inside a paused period; drop those items; treat the server answer `skipped` as done.

## 3. Questions we need answered
1. **Does the app treat `{"status": "skipped"}` from `receive_call_recording` as done, drop the item and not retry?** (Doc 4, section 4.)
2. **The complete list of endpoints the app calls** (any besides those in doc 3 section 3). The new token works only for the listed ones; anything else gets 403.
3. **Does the current app send the `sid` cookie** (a cookie jar) with its calls? The new build must not.
4. **Does the app store the rep's password and silently re-login?** Under the new flow it must keep the token and not store the password.
5. Is a 6-digit code entry screen with the text "Ask the administrator for the code" acceptable for the OTP step?

## 4. Order we suggest
Review feature (already live) -> new sign-in and recording toggle in the same app build (1.5) -> we switch on the backend after your build is on a pilot phone.
