# Call Tracker: stay signed in and syncing from any network. Research and plan (22 Oct 2026)

Research only; nothing was changed. Findings come from three independent read-only investigations (Frappe auth internals, the Sigzen SFA code, live data for the last 14 days). Items marked VERIFIED were read in code or counted in the database; INFERRED means reasoning from verified facts.

## 1. The requirement
Call Tracker app users must stay signed in and keep syncing from any network (field, other city) until they log out on purpose. The ERP desk must remain closed to anyone outside the 3 office IPs. No change to SFA, the desk login, or any other function.

## 2. How it works today
| Step | What happens | Where |
|---|---|---|
| Sign in | The app posts the password to `/api/method/login`. Frappe checks the password, then checks `User.restrict_ip`, then creates a desk session. | frappe `auth.py:168-175` (post_login) |
| Get a token | The app calls `get_my_api_credentials`, which generates a NEW api secret on the User on every call and saves the User. | `splinh/api/call_tracking.py:590-611` |
| Sync | The app sends `Authorization: token key:secret`. Frappe does NOT check the IP for token requests. It does check the IP when a `sid` cookie is also sent. | frappe `app.py:102-104`, `auth.py:685-733`, `sessions.py:321-328` |
| Hourly sweep | The Sigzen job `revoke_expired_mobile_sessions` (every hour at :02) deletes the api secret and every session of any user who lacks the "Enable Mobile App" flag. | sigzensfa `tasks.py:12-22, 40-47` |

VERIFIED: Sigzen's sweep only looks at `User.api_secret` and sessions. Nothing else in any installed app deletes secrets or restrict_ip.

## 3. What actually causes the problem (live data, 8-22 Oct)
- **Hourly sweep, the dominant cause.** 517 of 555 forced logouts fall at minute :02 (93%). 549 of the 555 hit users without the SFA flag. Of the 23 call-tracker-only users who synced, 9 to 16 are logged out on a typical working day.
- **Result:** only 2 of 23 call-tracker-only users hold a token right now (24 of 25 SFA users do). 11 of the 23 have not checked in for more than 24 hours.
- **Re-login churn:** reps log in 3 to 5 times a day. Calls reach the server a median of **13.1 hours** after they happen, and the slowest tenth wait **4.9 days**. Call Logs arrive in bursts when reps are on the office Wi-Fi (about 98% of successful logins are from office IPs).
- **IP rule:** a password login from outside is refused after the password check (VERIFIED). That refusal is not written to the Activity Log, so its size is invisible (INFERRED to be real: no call-tracker-only user has ever synced from a non-office IP).
- **Second cause, `deny_multiple_sessions = 1`:** every new login kills the user's other session. About 213 to 230 such kicks in 14 days. It affects cookie sessions only, not an API token.
- **Not causes:** session expiry is 7 days with only 19 expired sessions; the licence grace date is April 2027.

## 4. Options considered
| Option | Verdict |
|---|---|
| Tick "Enable Mobile App" for call-tracker-only reps | Rejected. Only 5 licence seats are free against 13 needed, and it grants SFA login rights. |
| Remove or loosen `restrict_ip` | Rejected. It would also open the ERP desk from outside. |
| Copy Sigzen's path-scoped IP patch into splinh | Rejected. It is a global monkey-patch, depends on import order and still leaves the hourly sweep. |
| Ask Sigzen to exempt Call Tracker users from the sweep | Possible later, but vendor-controlled and out of our hands. |
| **Separate per-device token for the app (recommended)** | Fixes both causes. Uses only supported Frappe mechanisms. |

## 5. Recommended design: a "Call Tracker Token" for each device
**Idea:** the app gets its own credential that lives outside `User.api_secret`, so the sweep cannot see it. It is created from the password without a desk session, and it is only valid for the Call Tracker endpoints.

1. **New doctype `Call Tracker Token`** (System Manager only): user, device id, device model, api key, api secret (encrypted Password field), enabled, created, last used, last IP, revoked reason.
2. **`app_login(usr, pwd, device_id, ...)`** (new endpoint, no session):
   - Checks the password with Frappe's own credential check, so the existing lockout (10 failed attempts) still applies, and adds a per-IP rate limit.
   - Requires an enabled user who may create Call Logs.
   - Does NOT run the post-login step, so there is no IP check, no desk session and no cookie.
   - Returns the key and secret for this user and device. The same device gets the same token again and nothing is rotated.
3. **App sends** `Authorization: token key:secret` plus `Frappe-Authorization-Source: Call Tracker Token`. This is native Frappe (`auth.py:685-733`); it loads the user from the token record.
4. **Scope guard (`auth_hooks`, runs right after token validation, VERIFIED `auth.py:635`):** if the request used this token source and the path is not on the allowlist, answer 403. Allowlist: `push_calls`, `ingest_whatsapp_call`, `receive_call_recording`, `submit_call_review`, `get_call_review_options`, `get_my_call_reviews`, heartbeat, `app_logout`. So a stolen token can never read or change ERP data through the normal API, and never opens the desk.
5. **`app_logout`:** the token is disabled when the rep chooses to log out. That is the only normal way it ends.
6. **Automatic revoke:** when a User is disabled, all their tokens are disabled. An admin can also disable any token (lost phone) from the doctype list.
7. **`last used` and `last IP`** are updated, throttled to every few minutes. This also gives us the visibility we lack today for IP-blocked logins.

**Why this is safe**
- The desk login, `restrict_ip`, Sigzen patches and the hourly sweep are untouched, so the desk stays closed outside the office.
- Nothing is monkey-patched.
- The old flow (`login` plus `get_my_api_credentials`) stays exactly as it is, so old app builds keep working in the office.
- The sweep only touches `User.api_secret`, so this token is out of its reach (VERIFIED).
- Frappe's session expiry and `deny_multiple_sessions` do not apply to a token.

## 6. Scenarios
| Scenario | Today | With the plan |
|---|---|---|
| Rep in office on Wi-Fi | Works until the next :02 sweep, then re-login | Works, no re-login |
| Rep outside the city on mobile data | Password login refused after the password check, sync stops | Signs in once and syncs from anywhere |
| Phone offline for days | Data waits and the token is already revoked | Data pushes the moment the network returns |
| Rep opens the ERP desk from outside | Refused | Still refused (unchanged) |
| Stolen token used against `/api/resource/Lead` | Works for any user holding a token today | Refused with 403 |
| Rep logs out on purpose | Session ends | Token disabled |
| User disabled or leaves | Token revoked by the sweep | Tokens disabled immediately |
| Rep also uses the desk in the office | Desk login kicks other sessions | Unaffected; the app token is not a session |
| Admin wants to cut off a phone | Not possible per device | Disable that token |

## 7. App-side changes (separate small doc for the app team)
1. Sign in through `app_login` and store the key and secret; stop calling `get_my_api_credentials`.
2. Send both headers on every call (token and `Frappe-Authorization-Source`).
3. Never send the `sid` cookie: use a cookie-less client, or clear the cookie jar. A cookie would bring back the IP check. NOT VERIFIED: what the current app sends; to be confirmed with the app team.
4. On 401 or 403, ask for the password once (the token was revoked); never log out silently.
5. Call `app_logout` on the logout button.

## 8. Rollout (UAT first, as you decided earlier)
1. Build the doctype, `app_login` and `app_logout`, the scope guard and the User-disable revoke in `splinh`, with offline tests and a rolled-back check on real data.
2. Deploy to UAT; the app team tests a new build against it from outside the office network.
3. Run the test list: login from office and from mobile data; sync, recording, review and heartbeat calls with the token; a token call to a desk API is refused; desk login from a non-office IP is still refused; disable a user and see the token stop; logout; survive the :02 sweep; survive a night offline.
4. Then production: fields and doctype in-process, a web reload, then `bench clear-cache` last. The hook cache needs the usual order.
5. Roll out the new app build gradually; old builds keep working.

## 9. Decisions needed from you
1. **Inactivity expiry:** should an unused token expire (for example after 90 days without use), or never expire until logout or disable? Recommended: 90 days.
2. **Password change:** should a password change revoke the user's tokens? Recommended: yes for a reset by an admin, no for a normal self-change.
3. **Devices:** one token per user and device (recommended) or one per user?
4. **Old builds:** let old builds keep using the old flow (recommended, they remain in the office), or force an upgrade date?

## 10. Open items
- What the Android app sends (cookies and headers) is unconfirmed.
- This does not remove the hourly sweep. If you also want the old flow fixed, ask Sigzen to exempt Call Tracker users.
- The kicks from `deny_multiple_sessions` affect desk users who also use the app on the same account. They do not matter for this design.
