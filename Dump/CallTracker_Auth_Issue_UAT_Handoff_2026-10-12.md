# Call Tracker sign-offs — issue, findings and UAT plan

Written 12 Oct 2026 from a read-only investigation of production (`erp.splashjetink.com`, Frappe 16.12.1 / ERPNext 16.10.1). **Nothing here has been applied anywhere.** Purpose: give everyone the full context so the fix can be **built and tested on UAT first**, then replicated on production. Items are marked **Verified** (code or data read, in-process simulation, or a control comparison) or **Inference**. Draft code is **untested**.

Related files in the same folder: `IP_Restriction_Investigation_CallTracker_vs_SFA_2026-10-12.md` (detailed IP analysis), `Call_Recording_Analysis_2026-10-10.md`, `User_Device_Mapping_2026-10-10.xlsx`.

---

## 0. TL;DR

1. **Symptom:** Call Tracker users get signed off, and cannot sign in again unless they are on the office network. SFA app users work from any network.
2. **Three separate mechanisms combine** (all Verified):
   - **A. IP rule:** every enabled user (81 of 81) has `restrict_ip`. Frappe enforces it on password login and on cookie sessions, **not on API tokens**. The Call Tracker's login uses the standard `/api/method/login`, so **re-login is blocked off the office network**. The SFA app is exempt through a start-up patch limited to its own URLs and to users with "Enable Mobile App" ticked.
   - **B. The SFA hourly sweep (the main cause):** `sigzensfa.tasks.revoke_expired_mobile_sessions` runs **every hour at :02**. It **deletes the API secret and ends all sessions (desk too) of every user who holds an API secret but does not have "Enable Mobile App" ticked**. Call Tracker users get their secret from our `get_my_api_credentials` and almost none have the box ticked, so their token dies within the hour.
   - **C. Secret rotation and single session:** every `get_my_api_credentials` call and every SFA login replaces the user's `api_secret`; `deny_multiple_sessions = 1` ends other sessions on each new login.
3. **Evidence that B is real (control experiment):** for users **without** the box, **98 % of 4,887 call syncs happen within 1 hour of a fresh token issue** (median 1 minute, 99th percentile 1.1 h, **0 % beyond 6 h**). For users **with** the box ticked, tokens last weeks to months with no re-login.
4. **Proposed fix:** give the Call Tracker its **own credentials outside the User record** (a small custom doctype used through Frappe's native `Frappe-Authorization-Source` header) and a **token-only issue endpoint** that checks the password but creates **no session and runs no IP check**. The SFA sweep never touches it; SFA logins cannot rotate it; sessions cannot be killed; re-issue works from any network. Needs an app change.
5. **Plan:** reproduce the problem on UAT → build the fix there → test (section 8) → pilot on production with 2 users → roll out.

---

## 1. The business problem

- Call Tracker is the Android app that syncs calls and recordings to ERPNext (`splinh.api.call_tracking.push_calls`, `receive_call_recording`).
- Reported: a user must be on the company WiFi to stay logged in; when disconnected they are signed off and cannot sign in again with their ERPNext login.
- A second Android app (SFA, from the Sigzen apps `sigzensfa` / `splashjetsfa`) lets users log in without the WiFi/IP restriction. We needed to know how each is configured and why they differ.
- Impact seen in data: syncs and recording uploads arrive in short bursts right after logins, many users stop syncing for days, and 11 phones never upload any recording (partly unexplained, see section 7).

---

## 2. Authentication flows

### 2.1 Call Tracker (our app, `apps/splinh/splinh/api/call_tracking.py`)

1. Phone logs in with username and password at **`/api/method/login`** → IP-checked.
2. Phone calls **`get_my_api_credentials`** (needs the session). It keeps the `api_key`, **always generates a new `api_secret`**, saves the User (`call_tracking.py:578-600`).
3. Later calls use **`Authorization: token key:secret`** → not IP-checked by Frappe.

The phone can therefore keep syncing from any network **as long as its token survives**. It fails when it must **log in again**.

### 2.2 SFA app (Sigzen, read-only for us)

1. Login endpoint `sigzensfa.mobile_api.api.login` (`allow_guest`): requires `custom_is_mobile_access`, applies a device-lock OTP (`SFA Settings.allow_restiction = 1`), checks the password with `LoginManager.authenticate`, requires an SFA role, then `post_login()` → Frappe's IP check **(patched)** → `generate_keys` (new secret on every login).
2. The patch `apps/sigzensfa/sigzensfa/auth_patches.py`, installed when the package is imported (`sigzensfa/__init__.py:19`): *if the request path starts with `/api/method/sigzensfa.mobile_api.` and the user has `custom_is_mobile_access = 1`, skip the IP check.* `splashjetsfa` stacks a second patch for its own prefix.
3. The "ip_bypass_installed=False" lines in `logs/mobile_api_calls.log` are a **logging false alarm** (the logger only looks for sigzensfa's marker; splashjetsfa's wrapper sits on top). In an in-process simulation the SFA path was still allowed.

### 2.3 What "Enable Mobile App" is

User form tab **SigzenSFA Mobile App**, field `custom_is_mobile_access`. It (a) allows SFA login, (b) uses one **licence seat** (26 of 30 used; quota `total_license` 30, valid until 1 Apr 2027; cannot be ticked when full), (c) enables the IP exemption on SFA URLs, and (d) **unticking it or disabling the user immediately revokes their API secret and sessions**.

---

## 3. Frappe core facts (Verified from `apps/frappe/frappe`)

| Authentication path | IP check? | Where |
|---|---|---|
| Password login `/api/method/login` | **Yes** | `auth.py:171` (`post_login`), after the password is accepted |
| Resumed `sid` cookie session, every request | **Yes** | `sessions.py:328` |
| **API token** `Authorization: token key:secret` | **No** | `validate_auth` → `validate_api_key_secret` (`auth.py:627-733`) |
| OAuth / Bearer | **No** | `auth.py:645-682` |

- `validate_ip_address` (`auth.py:461-498`): splits `restrict_ip`, accepts if the request IP **starts with** any entry (plain prefix, not CIDR); IPv6 never matches an IPv4 list; request IP is the first `X-Forwarded-For` entry. Failure: HTTP 401 *"Access not allowed from this IP Address: <ip>"*. Only bypasses: realtime URLs, users with an empty list, 2FA flags (both off here).
- A blocked login with the correct password writes **no Activity Log row**; a blocked cookie request returns 401 and deletes the browser cookies but leaves the server session row.
- **Token requests carry no session**, so they pass from any IP. Without a `sid` cookie the request resumes as Guest, which has no restriction.
- Frappe natively supports credentials on **another doctype**: the header **`Frappe-Authorization-Source: <DocType>`** (`auth.py:695-716`). The doctype needs `api_key`, `api_secret` (Password), `enabled` and `user` fields; Frappe then calls `set_user(<that user>)`. **It checks only the token document's `enabled` flag, not the User's** (important for the design).
- Live System Settings: `session_expiry` 170:00, **`deny_multiple_sessions` 1**, `enable_two_factor_auth` 0, `allow_consecutive_login_attempts` 10, `allow_login_after_fail` 60 (lock for 60 s after 10 failures).

---

## 4. Production data (Verified, 12 Oct 2026)

### 4.1 IP restriction census

- 81 of 81 enabled System Users have `restrict_ip`; **79** share `183.177.126.223,117.247.52.23,122.169.40.250`; `dummy@splashjetink.com` has those 3 + `113.20.17.68`; `support@sigzen.com` only `113.20.17.68`.
- 26 users have "Enable Mobile App" ticked (all with `restrict_ip` set).
- History (Version log): 2 Apr Administrator bulk-set 2 IPs on 104 users; 28 Jul and 5 Aug lists cleared for about 38 users (SFA users) and restored 3 Aug / 5 Aug / 16 Sep; **4 Oct 17:xx Administrator appended `122.169.40.250` for ~136 users**; no bulk change since.

### 4.2 SFA users reach the server from outside the IPs

`logs/mobile_api_calls.log*` (16,874 lines, 9–12 Oct): **14,722 lines (87 %) from IPs outside the user's own list**, almost all 200/201/204, dozens of mobile-carrier IPs per user, **no IP errors**. All 20 users in that log have `restrict_ip` set and the flag ticked. Simulation (SFA apps imported, request from `8.8.8.8`): a flagged user is **allowed** on the SFA login path and **blocked** on `/api/method/login`, `splinh…push_calls` and `get_my_api_credentials` when the IP check runs (cookie session); a user without the flag is blocked everywhere.

### 4.3 Call Tracker users

- 22 call-tracker-only users (no flag) and **10 dual-app users** (flag ticked): asm.north, asm.south, asm2.south, em.sales, rsm.central, rsm.east, rsm.south, rsm.south1, sm.sales, tl.west.
- **Zero** successful logins from outside the 3 office IPs for the 22 call-tracker-only users since 1 Oct (Activity Log). 603 "Invalid login credentials" failures since 1 Oct, mostly from office IPs (password problems), a few from mobile-carrier IPs.
- Users who still log in at the office but stopped syncing: tl.west (last call 3 Oct), temp@gmail.com, exports.af, exports.am, exports@ (5 Oct), exports.eu (6 Oct), sales.east, bd2.west (8 Oct), asm.north (9 Oct).

### 4.3b The hourly sweep (the main finding)

`sigzensfa/tasks.py`, registered hourly in `sigzensfa/hooks.py` (Scheduled Job Type `pvvqi7n36i`):

```python
# A — users still holding a token who are no longer authorised
for name in frappe.get_all("User",
        filters=[["api_secret", "is", "set"]],
        or_filters=[["custom_is_mobile_access", "!=", 1], ["enabled", "!=", 1]], pluck="name"):
    revoke_mobile_credentials(name)

def revoke_mobile_credentials(user):
    frappe.db.delete("__Auth", {"doctype": "User", "name": user, "fieldname": "api_secret"})
    frappe.db.set_value("User", user, "api_secret", "", update_modified=False)
    clear_sessions(user=user, force=True)      # ends cookie AND desk sessions
```

- Verified it **runs hourly** (Scheduled Job Log: 06:02, 07:02 … 11:02 on 12 Oct, all Complete); scheduler is running.
- Verified the current state: **21 of 22 call-tracker-only users hold no API secret; all 10 dual-app users do.**
- Rule B of the same job revokes everyone with the flag when the licence grace period passes (valid until 1 Apr 2027; not active now).

### 4.4 The control experiment

Test: for every SIM Call Log since 5 Oct, time since the same user's last token issue (Version rows with `api_secret` changes).

| Group | Syncs | Time since last token issue |
|---|---|---|
| **Without** the SFA box (token swept hourly) | 4,887 | median **1 min**, p75 11 min, p90 39 min, **p99 1.1 h**, **0 % beyond 6 h**; 98 % within 1 h |
| **With** the box ticked (control) | 2,762 | **no token issue in the last two months** — their old tokens keep working |

About 20 % of the unswept group's syncs fall after the next :02 boundary but still within ~1 h of a login (queued backlogs from a few users; does not change the conclusion).

### 4.5 Other data points

- Syncs and recording uploads come in **bursts right after logins**; recording upload latency is bimodal (~1 min or ~20 h late); 15–30 `api_secret` rotations per day.
- **Not explained by this issue:** 11 phones that sync calls but never upload recordings; calls ≥ 170 s never recorded (see the recording analysis). Those are separate app-side problems.

---

## 5. Root-cause summary

| Layer | Mechanism | Effect | Status |
|---|---|---|---|
| **B (primary)** | SFA hourly sweep deletes the token and sessions of users without the SFA box | Call Tracker token lives ≤ ~1 h → 401 → forced re-login | **Verified** (code, schedule, state, control experiment) |
| **A** | IP rule on `/api/method/login` for non-SFA paths | The forced re-login is blocked off the office network → "cannot sign in again" | **Verified** |
| **C** | Secret rotation (`get_my_api_credentials`, SFA login) and `deny_multiple_sessions` | The 10 dual-app users invalidate their own Call Tracker token whenever they use SFA | **Verified mechanism**, effect on these users **inference** |
| App behaviour | Does the app send the `sid` cookie? What does it do on 401? | Determines exactly how users experience it | **Inference** — app source is not on the server |

Relaxing only the IP rule would **not** fix it (the sweep still deletes tokens); fixing only the sweep would still leave re-login blocked off-network.

---

## 6. Proposed solution (design)

### 6.1 Principle

Keep the Call Tracker's credentials **out of `User.api_secret`**, so the SFA sweep and SFA logins never see them, and make the **token issue** step independent of the IP rule and of sessions.

### 6.2 Components

1. **Custom doctype `Call Tracker Token`** (built from the UI on UAT; no code): fields `user` (Link User, reqd), `api_key` (Data, unique, indexed), `api_secret` (**Password**), `enabled` (Check, default 1), `device_id` (Data), `expires_on` (Date, optional), `last_issued_on` / `last_used_on` (Datetime, informational). One row per (user, device).
2. **Issue endpoint** `issue_call_tracker_token(usr, pwd, device_id)` — whitelisted, `allow_guest`, POST:
   - Checks the password with Frappe's own `LoginManager().authenticate(user, pwd)` — the same pattern the SFA login uses. This also gives the built-in failed-attempt lockout (10 failures → 60 s) and rejects disabled users.
   - Requires an allowed role (roles that can create Call Logs: Call Tracker User, Sales User, Sales Co-ordinator, Regional Sales Manager (RSM), Exports Sales User, Exports Team Lead).
   - **Does not call `post_login()`**, so: no IP check, **no session**, no cookie, desk login from outside the office stays blocked.
   - Creates or replaces the token for that (user, device) with a fresh secret; other devices and SFA are unaffected. Returns `api_key`, `api_secret` and the source name.
   - Records the attempt (success/failure, IP, device) somewhere queryable — today blocked logins leave no trace.
3. **Request format** from the app on every call:
   ```
   Authorization: token <api_key>:<api_secret>
   Frappe-Authorization-Source: Call Tracker Token
   ```
   `push_calls` and `receive_call_recording` stay unchanged; Frappe sets the user from the token's `user` field, so the existing `if_owner` permissions and Call Log ownership keep working.
4. **User-disable safeguard (required):** Frappe checks only the token's `enabled`, so a disabled User's token would keep working. Add a Server Script (DocType Event on User, after save) or a daily reconcile that sets the user's tokens to `enabled = 0` when the User is disabled; optionally honour `expires_on`.
5. **Old path:** `get_my_api_credentials` must **stop being used** (it sets `User.api_secret`, which makes the user a sweep target again and the sweep would end their desk sessions). Keep it temporarily for old app versions, then remove or make it refuse.
6. **Optional hardening:** device binding (store a device id, refuse reuse from another device), a small rate limit on the endpoint, `expires_on` (e.g. 90 days with re-issue), an admin report of tokens and last use.

### 6.3 Draft sketches (UNTESTED — for UAT only)

Issue endpoint (draft, new module e.g. `splinh/api/call_tracker_auth.py`; a brand-new module is imported fresh on first use, later edits to it need a web restart):

```python
import frappe
from frappe.auth import LoginManager

ALLOWED_ROLES = {"Call Tracker User", "Sales User", "Sales Co-ordinator",
                 "Regional Sales Manager (RSM)", "Exports Sales User", "Exports Team Lead"}
TOKEN_DOCTYPE = "Call Tracker Token"

@frappe.whitelist(allow_guest=True, methods=["POST"])
def issue_call_tracker_token(usr, pwd, device_id):
    if not (usr and pwd and device_id):
        frappe.throw("Missing credentials", frappe.AuthenticationError)
    lm = LoginManager()                    # constructed outside /login: nothing is logged in (same pattern as sigzensfa)
    lm.authenticate(user=usr, pwd=pwd)     # password check + lockout; raises on failure
    user = lm.user
    if not (set(frappe.get_roles(user)) & ALLOWED_ROLES):
        frappe.throw("Not permitted", frappe.PermissionError)
    # NOTE: no lm.post_login() -> no IP check, no session, no cookie
    name = frappe.db.get_value(TOKEN_DOCTYPE, {"user": user, "device_id": device_id})
    doc = frappe.get_doc(TOKEN_DOCTYPE, name) if name else frappe.new_doc(TOKEN_DOCTYPE)
    doc.update({"user": user, "device_id": device_id, "enabled": 1})
    if not doc.api_key:
        doc.api_key = frappe.generate_hash(length=15)
    secret = frappe.generate_hash(length=15)
    doc.api_secret = secret
    doc.flags.ignore_permissions = True
    doc.save()
    return {"api_key": doc.api_key, "api_secret": secret, "auth_source": TOKEN_DOCTYPE}
```

User-disable safeguard (draft, Server Script, DocType Event "After Save" on User):

```python
if not doc.enabled:
    for t in frappe.get_all("Call Tracker Token", filters={"user": doc.name}, pluck="name"):
        frappe.db.set_value("Call Tracker Token", t, "enabled", 0)
```

Things to verify on UAT while building: that `LoginManager()` can be constructed in a guest request like this, that the lockout works, that `Frappe-Authorization-Source` passes through nginx untouched, that Password-field secrets verify via Frappe's `get_decrypted_password`, and the exact permission needed to save the token doc.

---

## 7. Options considered

| Option | Verdict |
|---|---|
| **A. Clear/widen `restrict_ip` for call-tracker users** | Fixes only layer A, not the sweep; also opens their desk login from anywhere. Not enough alone. |
| **B. Tick "Enable Mobile App" for call-tracker users** | Not viable: 4 seats left for 22 users, and it also grants SFA access. |
| **C. Ask Sigzen to limit the sweep** (only users who ever had the box ticked) | Good long-term; depends on a vendor; do in parallel, not as the only fix. |
| **D. Pause the hourly job** | Removes SFA licence enforcement; not ours to disable. Acceptable only as a short **UAT-only** experiment. |
| **E. Our own SFA-style login (replicate the IP bypass patch in splinh)** | Works, but adds a third import-time patch stacked on two others (fragile) and still stores the secret on the User, so the sweep keeps deleting it. |
| **F (recommended). Separate credentials + token-only issue endpoint** | Fixes layers A, B and C together without patching Frappe or touching Sigzen apps. Needs the app change. |
| **G. Do nothing structural** | Users must be on the office network and re-login hourly. |

---

## 8. UAT test plan

UAT is `uat-splashjet.sigzencloud.com` on a **separate host**. The `bench-uat` folder on the production machine is **not** that environment. Splinh is **not** installed on UAT, so decide first how the new module gets there (install splinh, or a small test app) — see section 10.

### 8.1 Parity checks (before any test)

1. Frappe / ERPNext versions match production (16.12.1 / 16.10.1) and `sigzensfa` + `splashjetsfa` are installed **with the same auth patches** (check `frappe.auth.validate_ip_address` markers, `custom_is_mobile_access` field, "SigzenSFA Mobile App" tab).
2. The scheduler is on and the hourly job `sigzensfa.tasks.revoke_expired_mobile_sessions` exists and is enabled (Scheduled Job Type). Without it the main problem cannot be reproduced.
3. Users have `restrict_ip` set like production (3 IPs); `deny_multiple_sessions` = 1; Call Log permissions for the call-tracker roles (custom DocPerms) exist.
4. The user running `bench` on UAT owns `logs/` (otherwise the `cssutils.log` PermissionError seen on production appears).
5. Create test users: **U-ct** (call-tracker role, no SFA box), **U-both** (call-tracker + SFA box ticked), **U-sfa** (SFA only).

### 8.2 Reproduce the current problem first

| # | Test | Expected (today's behaviour) |
|---|---|---|
| R1 | For U-ct, run `get_my_api_credentials` (logged-in session) and call an endpoint with the token | Works |
| R2 | Run the sweep (`bench --site <uat> execute sigzensfa.tasks.revoke_expired_mobile_sessions`, **UAT only**) or wait for :02 | U-ct's secret is deleted; token → 401; open desk session ends |
| R3 | Repeat for U-both | Token and sessions untouched |
| R4 | Login as U-ct at `/api/method/login` with request IP not in `restrict_ip` (use the local gunicorn port with header `X-Forwarded-For: 8.8.8.8`) | 401 "Access not allowed from this IP Address"; **no Activity Log row** |
| R5 | Token request with the same spoofed IP | Works (no IP check) |
| R6 | U-both logs in to SFA, then Call Tracker token is used | Call Tracker token now 401 (secret rotated) |
| R7 | SFA login for U-sfa from a non-allowed IP on the SFA path | Works; same user on `/api/method/login` → 401 |

### 8.3 Fix tests (after building section 6)

| # | Test | Expected |
|---|---|---|
| F1 | Issue a token for U-ct from a **non-allowed IP** | Success; **no session, no cookie** in the response |
| F2 | Call `push_calls` / `receive_call_recording` with the token + `Frappe-Authorization-Source` header from a non-allowed IP | Works; Call Log owner = U-ct; `if_owner` permissions apply |
| F3 | Run the sweep again | **Token survives**; U-ct's desk session (if any) is not affected |
| F4 | U-both logs in to SFA (rotates `User.api_secret`) | Call Tracker token **unchanged** |
| F5 | Issue again from a second device id | Second token created; first device's token still valid; same device re-issue replaces only that device's secret |
| F6 | Wrong password ×11 | Lockout after 10 failures (60 s); failures are recorded |
| F7 | User without an allowed role | Refused |
| F8 | Disable the User | Their tokens become `enabled = 0` and calls return 401 |
| F9 | Delete or disable a token | Calls return 401 |
| F10 | Token sent **without** the source header | 401 (looked up on User, no match) |
| F11 | Desk login for U-ct from a non-allowed IP | **Still blocked** (desk security unchanged) |
| F12 | Token used against desk/API endpoints beyond Call Tracker (e.g. a list API) | Behaves like any user token today; record what is reachable (security note) |
| F13 | Old path `get_my_api_credentials` | Confirm it still sets `User.api_secret` and gets swept; decide to retire |
| F14 | Real phone with a dev build of the app against UAT, off the office WiFi, leave it for ≥ 3 hours including at least two sweeps | Syncs and recording uploads keep working |
| F15 | Performance and logging | Issue endpoint latency, rows written per issue, attempt log content |

### 8.4 Rollback test

Remove the new header/endpoint usage in the dev build and confirm the app falls back to the old path; confirm deleting the doctype rows does not affect SFA or desk users.

---

## 9. Production rollout (after UAT passes)

1. Create the `Call Tracker Token` doctype (UI, no migrate) and the disable-safeguard Server Script.
2. Deploy the issue endpoint in splinh as a **new module** (imported fresh on first use; no restart for the first deploy, restart for later edits).
3. **App release** by the app team: call the issue endpoint at login, store key/secret, send both headers on every request, handle 401 by re-issuing (needs the stored password or a prompt — to be agreed).
4. **Pilot with 2 users** (e.g. bd1.east, bd2.south) for 2–3 days; compare syncs per hour and hours-since-token with section 4.4.
5. Roll out to the rest; stop using `get_my_api_credentials`; ask Sigzen (in parallel) to limit their sweep.
6. Monitor: the control query below, the attempt log, Call Log arrival times, tokens per user.

Production-specific cautions: one `long` worker handles call inserts and transcription; the SFA sweep stays active for SFA users; do not edit Sigzen apps; keep `bench` runs as the log owner.

### Monitoring SQL (read-only)

```sql
-- who currently holds a User API secret (swept) among call-tracker users
SELECT u.name, IFNULL(u.custom_is_mobile_access,0) AS sfa_box,
  (SELECT COUNT(*) FROM __Auth a WHERE a.doctype='User' AND a.name=u.name AND a.fieldname='api_secret') AS has_user_secret
FROM tabUser u WHERE u.name IN (SELECT DISTINCT owner FROM `tabCall Log`);
-- hourly sweep runs
SELECT creation, status FROM `tabScheduled Job Log`
WHERE scheduled_job_type=(SELECT name FROM `tabScheduled Job Type` WHERE method LIKE '%revoke_expired_mobile_sessions%')
ORDER BY creation DESC LIMIT 10;
-- new tokens and last use (after the fix)
SELECT user, device_id, enabled, modified FROM `tabCall Tracker Token` ORDER BY modified DESC;
```

---

## 10. Decisions and open questions

1. **Policy:** may call-tracker users log in from any network (like SFA users)? The fix above assumes yes for the token issue step; desk login stays IP-restricted.
2. **How does the new module get onto UAT?** UAT has no splinh. Options: install splinh (pulls in its other features and migrations) or build the endpoint in a small test app. Developing in a splinh branch keeps UAT = production code path.
3. **App team:** does the Call Tracker app send the `sid` cookie? What does it do on a 401? Does it store the password? Can it send the extra header and call a new issue endpoint?
4. **Which users use both apps?** The 10 listed in section 4.3 — confirm with them.
5. **Token lifetime:** no expiry, or 90 days with re-issue? Device binding required?
6. **Sigzen:** will they limit the sweep (only users who ever had the SFA box)? Can they stop the SFA login rotating a secret that already exists?
7. **Security review points:** tokens work from any IP (same as today for both apps); a token holds the user's API access; the SFA login response reportedly echoes a `current_password` field (agent-reported, not verified by us — worth the Sigzen team checking).

---

## 11. What could not be determined

- The Call Tracker app's real behaviour (cookie vs token, re-login logic) — source not on the server.
- How many logins were actually blocked — blocked logins leave no log row and nginx logs are unreadable.
- Whether any specific stale user was blocked or simply stopped using the phone.
- `tabSessions.ipaddress` is empty for all rows (no per-session IP).

---

## 12. Reference

- Core: `apps/frappe/frappe/auth.py` (171, 461-498, 627-733, 695-716), `sessions.py:321-328`, `core/doctype/user/user.py:1395-1399`.
- SFA: `apps/sigzensfa/sigzensfa/tasks.py` (hourly sweep), `hooks.py` (scheduler registration), `auth_patches.py`, `__init__.py:19`, `events/user.py` (licence enforcement, immediate revoke), `mobile_api/domains/api/auth.py` (login), `mobile_api/domains/api/helpers.py:71-82` (`generate_keys`), `mobile_api_logger.py`; splashjetsfa `auth_patches.py`.
- Call Tracker: `apps/splinh/splinh/api/call_tracking.py:578-600` (credentials), `push_calls`, `receive_call_recording`.
- Data: `tabUser.restrict_ip`, `custom_is_mobile_access`, `tabVersion` (restrict_ip and api_secret history), `tabActivity Log`, `__Auth`, `tabScheduled Job Log`, `logs/mobile_api_calls.log*`.
- Memory note for future sessions: `calltracker-auth-ip-and-sfa-sweep.md`.
