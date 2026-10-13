# IP restriction investigation — Call Tracker app vs SFA app

Production `erp.splashjetink.com`, Frappe 16.12.1 / ERPNext 16.10.1. Investigated 12 Oct 2026 by three parallel read-only analyses (Frappe core mechanics / production data / SFA code audit); the key claims were re-verified directly. **Nothing was changed.** Items are marked **Verified** (code or data read, or in-process simulation) or **Inference**.

---

## 1. The answer in one paragraph

Every enabled user (81 of 81) has an IP restriction (`User.restrict_ip`), 79 of them the same three office IPs. Frappe applies that restriction to **password login** and to **session-cookie requests**, but **not** to API-token requests (`Authorization: token key:secret`). The **SFA app** is exempt because the `sigzensfa` app replaces Frappe's IP check at start-up with a wrapper that skips the check for users flagged `custom_is_mobile_access = 1`, but **only on URLs that start with `/api/method/sigzensfa.mobile_api.`** — its login endpoint lives under that prefix. The **Call Tracker app** logs in through Frappe's standard `/api/method/login` (not covered by the exemption) and later talks to `splinh.*` endpoints, so its **login and re-login are blocked outside the office IPs** — and that is exactly when it needs to log in again (token rotated or session cleared), so the user appears "signed off and unable to sign in again".

---

## 2. How IP restriction is configured on production (Verified)

| Item | Value |
|---|---|
| Enabled System Users with `restrict_ip` set / empty | **81 / 0** |
| Users with the exact list `183.177.126.223,117.247.52.23,122.169.40.250` | **79** |
| Other lists | `dummy@splashjetink.com` = those 3 + `113.20.17.68`; `support@sigzen.com` = `113.20.17.68` only |
| Users with the flag `custom_is_mobile_access = 1` (all have `restrict_ip` set) | **26** |
| System Settings | `enable_two_factor_auth` 0, `bypass_restrict_ip_check_if_2fa_enabled` 0, `session_expiry` 170:00, **`deny_multiple_sessions` 1**, `allow_consecutive_login_attempts` 10, `allow_login_after_fail` 60 |
| SFA Settings | `allow_restiction` = 1 (device-lock OTP at SFA login) |
| Proxy / IP keys in site_config or common_site_config | none |

**How `restrict_ip` changed over time** (from the Version log; agent-collected):
- 2 Apr: Administrator bulk-set the first two IPs on 104 users.
- 28 Jul and 5 Aug: Administrator **cleared** `restrict_ip` on about 26 and 12 users (SFA users among them); restored on 3 Aug, 5 Aug and 16 Sep.
- 1 Oct and 3 Oct: `lms.content@splashjet-ink.com` edited lists for a few users (`192.168.1.1` added for two, later overwritten).
- **4 Oct 17:xx: Administrator appended the third IP `122.169.40.250` to ~136 users.** No bulk change since.
- Nothing shows a deliberate removal of any IP after 4 Oct.

---

## 3. How Frappe enforces it (Verified, code read + in-process simulation)

Function `validate_ip_address` in `apps/frappe/frappe/auth.py:461-498`:
- Takes the user's `restrict_ip`, splits on commas, and accepts the request if its IP **starts with** any entry (a plain string-prefix test, not CIDR). IPv6 addresses never match an IPv4 list.
- The request IP is the first entry of `X-Forwarded-For` (nginx sets it), then `REMOTE_ADDR`.
- Only bypasses: realtime URLs (`/api/method/frappe.realtime.*`), users with an empty list, and the 2FA bypass flags (both off here).
- Failure: **HTTP 401** `Access not allowed from this IP Address: <ip>`.

| Authentication path | IP check applied? | Where |
|---|---|---|
| Password login `/api/method/login` | **Yes** | `auth.py:171` (after the password is accepted) |
| Resumed session cookie (`sid`) on every later request | **Yes** | `sessions.py:328` |
| **API token** `Authorization: token key:secret` | **No** | `validate_auth` / `validate_api_key_secret`, `auth.py:627-733` — no IP call anywhere |
| OAuth / Bearer | **No** | `auth.py:645-682` |

The only two places in all of Frappe core that call the check are `auth.py:171` and `sessions.py:328`.

Behaviour when a restricted user is outside the allowed IPs:
- **Login with the right password:** rejected with 401; **no Activity Log row is written** (so these blocks are invisible in the logs).
- **Session cookie:** the request gets 401 and the browser's cookies are deleted, so the client looks signed off; the server-side session row is *not* deleted.
- **Token request:** works from any IP.
- Nothing in Frappe invalidates a token or session when the IP changes.

Simulation (in-process, no network, nothing saved), user `bd2.sales@`, request from `8.8.8.8`: blocked; from `183.177.126.223`: allowed; IPv6: blocked; `183.177.126.2231` (allowed prefix plus an extra digit): **allowed** (prefix rule); `183.177.126.22`: blocked.

---

## 4. The Call Tracker app (splinh) flow

1. Phone logs in with username and password at `/api/method/login` → **IP-checked**.
2. Phone calls `splinh.api.call_tracking.get_my_api_credentials` (needs the session) → **every call generates a new `api_secret`**; the `api_key` stays the same (`call_tracking.py:578-600`).
3. Later calls (`push_calls`, `receive_call_recording`) use `token key:secret` → **not IP-checked**.

So an app that keeps its token keeps working from any network. It breaks when it must **log in again**.

### Why "signed off" and "cannot sign in again" happens (Verified mechanisms, **Inference** on which one hits whom)

| Trigger | Effect |
|---|---|
| **`api_secret` rotation** — any `get_my_api_credentials` call (second phone / re-login) **or the SFA app's login for the same user** | The token the Call Tracker holds becomes invalid → 401 → the app re-logs in at `/api/method/login` → **blocked off-network** |
| **`deny_multiple_sessions = 1`** — every new login by the same user (desk browser, SFA, another phone) deletes the user's other sessions | If the app relies on a cookie session it is signed off; the re-login is blocked off-network |
| If the app also sends the `sid` cookie with its token requests | The cookie triggers the session IP check even though a token is present → 401 off-network |
| Fresh login on a phone using mobile data | Blocked, with no log row |

Evidence from production data:
- **22 call-tracker-only users** (no mobile flag): **zero** successful logins from IPs outside the 3 office IPs in the Activity Log since 1 Oct.
- **10 users use both apps** (they own Call Logs and have the flag: asm.north, asm.south, asm2.south, em.sales, rsm.central, rsm.east, rsm.south, rsm.south1, sm.sales, tl.west). Every SFA login rotates their secret and invalidates the Call Tracker's token — a conflict between the two apps (Verified code; effect on these users is Inference).
- Users whose Call Logs stopped while they still log in at the office: tl.west (last call 3 Oct), temp@gmail.com (5 Oct), exports.af (5 Oct), exports.am (5 Oct), exports@ (5 Oct), exports.eu (6 Oct), sales.east (8 Oct), bd2.west (8 Oct), asm.north (9 Oct). Their logins and secret rotations happened at allowed IPs just before the last call (Verified); that they were blocked is **Inference** — a blocked login leaves no row.
- 603 "Invalid login credentials" failures since 1 Oct, mostly from office IPs (password problems, not IP blocks); a few come from mobile-carrier IPs, consistent with users trying from mobile data.

---

## 5. The SFA app (sigzensfa) flow and why it works from any network

1. Login endpoint `sigzensfa.mobile_api.api.login` (`allow_guest`; code in `mobile_api/domains/api/auth.py`): checks the user has `custom_is_mobile_access`, applies a device-lock OTP (`SFA Settings.allow_restiction = 1`), checks the password with `LoginManager.authenticate`, requires an SFA role, then calls `post_login()`.
2. `post_login()` calls Frappe's `validate_ip_address` — **the patched version**.
3. The patch (`apps/sigzensfa/sigzensfa/auth_patches.py`, installed when the package is imported, `sigzensfa/__init__.py:19`): *if the request path starts with `/api/method/sigzensfa.mobile_api.` and the user has `custom_is_mobile_access = 1`, return without checking.* Any failure falls back to the original check.
4. `splashjetsfa` adds the same patch for `/api/method/splashjetsfa.mobile_api.`.
5. After login the app receives `sid`, `api_key` and `api_secret` (`generate_keys` rotates the secret on every login). Later calls (inference: token header) are not IP-checked by Frappe anyway.

**Verified by simulation** (SFA apps imported, request from `8.8.8.8`): the mobile-flag user `asm.south@` is **allowed** on `/api/method/sigzensfa.mobile_api.api.login` but **blocked** on `/api/method/login`, `splinh.api.call_tracking.push_calls` and `get_my_api_credentials`; a user without the flag is blocked everywhere.

**Verified from the SFA log** (`logs/mobile_api_calls.log*`, 16,874 lines, 9–12 Oct): 20 of 20 users have `restrict_ip` set and the mobile flag; **14,722 lines (87%) come from IPs outside the user's own list**, almost all successful (200/201/204), typically dozens of mobile-carrier IPs per user, and there are **no IP-restriction errors**.

**The "ip_bypass_installed=False" lines are a false alarm** (Verified): the sigzensfa logger checks for sigzensfa's own marker on the current function, but `splashjetsfa` wraps on top of it and its wrapper carries only its own marker, so the log says "False" although the sigzensfa wrapper is still running underneath. In my simulation the outermost function carried only the splashjetsfa marker, and the sigzensfa path was still allowed.

---

## 6. Side by side

| | **SFA app** | **Call Tracker (splinh)** |
|---|---|---|
| Login endpoint | `/api/method/sigzensfa.mobile_api.api.login` (custom) | `/api/method/login` (Frappe core) |
| IP check at login | patched → **skipped** for flagged users | **enforced** |
| Extra login controls | role check, device-lock OTP (`custom_unique_id`) | none |
| Credentials after login | key + secret in the login response | key + secret from `get_my_api_credentials` |
| Later requests | token (IP not checked) | token (IP not checked) |
| Re-login off-network | works (path covered by the patch) | **blocked** |
| Secret rotation | every login | every `get_my_api_credentials` call |
| Users affected by the other app's rotation | the 10 dual-app users | the same 10 users |

**The decisive difference:** the URL prefix of the login. Everything else is similar.

---

## 7. Findings ranked

1. **Verified:** the IP restriction is universal (81/81) and identical for almost everyone; it is not the reason SFA users differ.
2. **Verified:** SFA works from any network because of a start-up wrapper around `validate_ip_address` limited to `/api/method/sigzensfa.mobile_api.*` for flagged users. The Call Tracker's login does not use that path.
3. **Verified:** API tokens are never IP-checked in this Frappe version, so after a successful login the Call Tracker can keep syncing from any network; it is **re-login** that fails off the office network.
4. **Verified:** the two apps rotate the same user's `api_secret` (and `deny_multiple_sessions` ends other sessions); for the 10 dual-app users each login in one app invalidates the other app's credentials.
5. **Verified:** blocked logins leave **no Activity Log row** and no log line, so the number of blocked attempts is unknowable from the server (nginx logs are unreadable).
6. **Inference:** the Call Tracker's "signed off" users are mostly hitting #3/#4 followed by a blocked re-login. The app's source is not on the server, so its exact behaviour (cookie vs token, when it re-logs in) must be confirmed with the app developers.
7. **Observation (security):** because tokens are never IP-checked, the IP restriction does not protect API-token use at all (for either app); a stolen key and secret work from anywhere. The IP rule effectively guards only password logins and desk sessions.
8. **Fragility:** the SFA bypass depends on an import-time patch (two apps stack theirs); the older `mobile_api_v22/v23/v24` copies use different prefixes and would **not** be bypassed (no traffic today).
9. **Agent-reported, not independently verified by me:** the SFA login response appears to echo a `current_password` field (usually empty) — worth a look by the Sigzen team.

---

## 8. Options (nothing implemented — for your decision)

| Option | What it means | Trade-off |
|---|---|---|
| **A. Clear or widen `restrict_ip`** for the call-tracker users | They can log in from anywhere | Simplest. Weakens the rule for those users (passwords usable from any network). Mobile IPs change constantly, so adding individual IPs does not work. |
| **B. SFA-style bypass for the Call Tracker** | Add a Call Tracker login endpoint (e.g. in splinh) and extend the IP exemption to that URL prefix for flagged users (the existing `custom_is_mobile_access` flag can be reused) | Same security level as SFA. Needs an app change (new login call) and another start-up patch stacked with the two existing ones; the Sigzen apps cannot be edited, so it would live in splinh. |
| **C. Stop the credential fighting** | Make `get_my_api_credentials` return the existing secret instead of generating a new one, and avoid app re-logins | Reduces the "token suddenly invalid" cases for the Call Tracker; does **not** stop SFA logins from rotating the secret for the 10 dual-app users unless SFA changes too. |
| **D. App-side** | Keep the stored token and show a clear message instead of forcing a re-login; do not send the `sid` cookie with token calls | Mitigation only; needs the app team. |
| **E. Do nothing structural** | Keep the office-network rule for Call Tracker | Matches the current policy; users must be on the office network to log in again. |

My recommendation to discuss: decide the **policy** first (should call-tracker users be able to log in from any network, as SFA users can?). If yes, B is the consistent fix (or A as the quick one), plus C to reduce token invalidation. If no, keep as is and tell users to log in while on the office network.

---

## 9. What this investigation could not determine

- The Call Tracker app's real behaviour (does it send the cookie? when does it re-login?) — source not on the server.
- How many logins were actually blocked — no log records them.
- Whether any specific stale user was blocked or simply stopped using the phone.
- `tabSessions.ipaddress` is empty for all rows, so active session IPs cannot be listed.
- The SFA app's exact header for later calls (inferred to be the token).

## 10. Questions for you

1. Should call-tracker users be able to log in from any network (like SFA users)?
2. Which people actually use both apps? (The 10 listed in section 4.)
3. Is the report of "signed off outside the WiFi" coming mainly from these dual-app users or from call-tracker-only users?
4. Can the Call Tracker app developers tell us whether the app sends a session cookie and what it does on a 401?

---

## 11. Reference

- Core: `apps/frappe/frappe/auth.py` (lines 171, 461-498, 627-733), `sessions.py:321-328`, `core/doctype/user/user.py:1395-1399`.
- SFA patch: `apps/sigzensfa/sigzensfa/auth_patches.py`, `sigzensfa/__init__.py:19`, `splashjetsfa/auth_patches.py`, `splashjetsfa/__init__.py`; logger `sigzensfa/mobile_api_logger.py`; login `sigzensfa/mobile_api/domains/api/auth.py`, keys `mobile_api/domains/api/helpers.py:71-82`.
- Call Tracker: `apps/splinh/splinh/api/call_tracking.py:578-600` (credentials), upload/sync endpoints below it.
- Data: `tabUser.restrict_ip`, `custom_is_mobile_access`, `tabVersion` (restrict_ip and api_secret history), `tabActivity Log`, `logs/mobile_api_calls.log*`.
- No secrets are included in this document.
