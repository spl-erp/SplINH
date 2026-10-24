# Call Tracker app: sign-in that works outside the office (for the app team)

Status: backend written and tested on the server; **switched off until the administrator enables it and the web server is reloaded** (we will tell you). Build against this now. Written for the Android app developers.

## 1. What changes for the app
Today the app signs in through `/api/method/login` and then fetches a key through `get_my_api_credentials`. That fails outside the office (the ERP refuses a password login from other IPs) and the key is wiped every hour by another app's job. The new flow gives the app its own credential that is not affected by either. The old flow keeps working for old builds; the new build should use only the new flow.

**Important:** the new credential is valid until the rep logs out, with these exceptions: unused for 30 days, the rep's password is changed (by anyone), the user is disabled, or the administrator disables that device.

## 2. Sign-in (two steps; the second only for a new phone outside the office)
### Step 1: `POST /api/method/splinh.api.call_tracker_auth.app_login_start`
JSON body: `usr` (email), `pwd`, `device_id` (the same Android ID used in call names, 6 to 64 characters of letters, digits and `._:-`), `device_model`, `app_version`. No headers or cookies needed.

| HTTP | `status` | Meaning and what the app does |
|---|---|---|
| 200 | `ok` | Signed in. Body has `api_key`, `api_secret`, `user`, `full_name`, `device_id`, `signed_in_via` (`office`, `otp` or `existing`). Store the key and secret. |
| 200 | `otp_required` | New phone outside the office. Body has `challenge_id`, `expires_in` (seconds), `sent_to_approvers`. **The code is NOT sent to the rep.** Show a screen: "Ask the administrator for the sign-in code", with a field for the 6-digit code. Keep `challenge_id`. |
| 401 | (error) | Wrong username or password. |
| 403 | `not_allowed` | This account may not use the Call Tracker app. Show a message; do not retry. |
| 422 | `invalid_device` | `device_id` unusable. |
| 429 | `too_many_requests` | Too many code requests (3 per 15 minutes per user), or too many attempts in general. Wait. |
| 503 | `disabled` / `no_approver` / `mail_failed` | Remote sign-in is off, or nobody can approve right now. Show "Try again later / contact the administrator". |

### Step 2: `POST /api/method/splinh.api.call_tracker_auth.app_login_verify`
JSON body: `challenge_id`, `otp` (the 6 digits), `device_id` (same as step 1).

| HTTP | `status` | Meaning |
|---|---|---|
| 200 | `ok` | Same body as step 1. |
| 401 | `otp_invalid` | Wrong code; `attempts_left` tells how many tries remain (5 in total). |
| 403 | `device_mismatch` | The code was requested for another device. |
| 410 | `otp_expired` | Code expired (10 minutes) or unknown. Go back to step 1 for a new code. |
| 429 | `otp_locked` | Too many wrong codes. Go back to step 1. |

## 3. Using the credential
Every call to the server sends **both** headers:
```
Authorization: token <api_key>:<api_secret>
Frappe-Authorization-Source: Call Tracker Token
```
- **Never send a `sid` cookie** (use a cookie-less HTTP client or clear the cookie jar). A cookie brings back the office-IP check and the call is refused.
- The credential is accepted only for these methods; anything else returns 403:
  `splinh.api.call_tracking.push_calls`, `ingest_whatsapp_call`, `receive_call_recording`, `submit_call_review`, `get_call_review_options`, `get_my_call_reviews`, `splinh.api.app_status.heartbeat`, `splinh.api.app_status.get_my_app_config`, `splinh.api.call_tracker_auth.app_logout`.
  **Tell us if the app uses any other endpoint now** so we can add it. Do not pass a `cmd=` parameter.
- **401** on any call = the credential was ended (password changed, unused for 30 days, or disabled by the administrator). Ask the rep to sign in again. Keep unsent calls and reviews queued; do not delete them.
- **403** = this user is no longer allowed (role removed, tick box removed, or user disabled). Show a message; keep data queued.
- No server timeout applies: the credential does not expire while it is being used, and there is no hourly sign-out any more.

## 4. Logout
`POST /api/method/splinh.api.call_tracker_auth.app_logout` with the two headers above. It disables this phone's credential. Call it from the logout button; then delete the stored key and secret. This is the only normal way the credential ends.

## 5. Rules the server enforces (for your information)
- Only users who have the Call Tracker User role **and** the "Call Tracker App User (INH)" tick box can sign in this way.
- One phone per rep by default (a setting). Approving a new phone disables the old one, whose next call then gets 401.
- Inside the office (the user's allowed IPs) a new phone signs in with just the password. Outside, an approved code is required once per phone.

## 6. Test list for the app team (when we tell you it is live)
1. Sign in at the office: `ok`, no code asked; calls sync.
2. Sign in on mobile data with a new phone: `otp_required`; wrong code gives `otp_invalid`; the right code gives `ok`; calls sync from mobile data.
3. Sign in again on the same phone: no code (`existing`).
4. Leave the phone for 3 hours (several hourly marks): still syncing.
5. Logout: the credential stops (next call 401); sign in again works.
6. Change the rep's password (administrator): next call 401, sign in again needs the new password (and a code if outside).
7. Call any other ERP URL with the credential: 403.
8. Call sync, recordings, reviews, heartbeat behave exactly as before.
