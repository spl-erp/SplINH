# Call Tracker app 1.5: one consolidated brief (copy the text inside the block)

```
You are the Android developer (or AI pair) for the "Call Tracker" app of Splashjet. The ERP backend
(Frappe/ERPNext v16, site erp.splashjetink.com) has been extended. Implement ALL of the following in
app build 1.5, then run the end-to-end test list at the bottom and report back. Do not change anything
that already works (call sync, WhatsApp ingestion, recording upload, heartbeat) except where stated.
All times are IST, "YYYY-MM-DD HH:mm:ss". All calls are HTTPS to https://erp.splashjetink.com.

==================================================================================
PART A. SIGN-IN THAT WORKS OUTSIDE THE OFFICE (replaces login + get_my_api_credentials)
==================================================================================
Why: the old flow (/api/method/login, then get_my_api_credentials) fails outside the office IPs and the
old key is wiped every hour by another system. The new flow gives the app its OWN credential, valid
until the rep logs out (or it is unused 30 days, the rep's password changes, the user is disabled, or an
administrator disables that phone). KEEP the old flow only as a fallback for old builds; build 1.5 must
use ONLY the new flow.

A1. Step 1: POST /api/method/splinh.api.call_tracker_auth.app_login_start   (no headers, no cookies)
    JSON: usr (email), pwd, device_id (the same Android ID used in call names: 6-64 chars of
    letters/digits/._:- ), device_model, app_version.
    Answers:
      200 {"status":"ok","api_key","api_secret","user","full_name","device_id","signed_in_via":"office|otp|existing",
           "unused_token_expires_after_days":30}  -> store key+secret securely (Keystore). Signed in.
      200 {"status":"otp_required","challenge_id","expires_in":600,"sent_to_approvers":N,"message"}
           -> new phone outside the office. The code is NOT sent to the rep; it goes ONLY to the
              administrators, who read it to the rep. Show a screen: "Ask the administrator for the sign-in
              code" with a 6-digit field and a "Get a new code" button (repeats Step 1). Keep challenge_id.
      401 wrong username/password -> show "Wrong username or password".
      403 {"status":"not_allowed"} -> "This account cannot use the Call Tracker app"; do not retry.
      422 {"status":"invalid_device"} -> bug in device_id.
      429 {"status":"too_many_requests"} or generic rate limit -> "Too many tries, wait a few minutes".
      503 {"status":"disabled|no_approver|mail_failed"} -> "Sign-in is not available right now, contact the administrator".
A2. Step 2: POST /api/method/splinh.api.call_tracker_auth.app_login_verify
    JSON: challenge_id, otp (6 digits), device_id (same as step 1).
      200 {"status":"ok", ...same body as A1...}
      401 {"status":"otp_invalid","attempts_left":n}  (5 tries in total)
      403 {"status":"device_mismatch"}
      410 {"status":"otp_expired"} (valid 10 minutes) -> go back to Step 1 for a new code
      429 {"status":"otp_locked"}   -> go back to Step 1
A3. EVERY other call to the server (push_calls, ingest_whatsapp_call, receive_call_recording,
    submit_call_review, get_call_review_options, get_my_call_reviews, heartbeat, get_my_app_config,
    app_logout) must send BOTH headers:
        Authorization: token <api_key>:<api_secret>
        Frappe-Authorization-Source: Call Tracker Token
    - NEVER send a "sid" cookie. Use a cookie-less HTTP client or clear the cookie jar; a cookie brings
      back the office-IP check and the call is refused.
    - Do not pass a "cmd" parameter. Only the 9 methods above are allowed; anything else returns 403.
A4. 401 on a normal call = the credential was ended (password changed / unused 30 days / disabled by the
    administrator). Ask the rep to sign in again; KEEP all unsent calls, recordings and reviews queued.
    403 = this user is no longer allowed; show a message, keep data queued.
A5. Logout button: POST /api/method/splinh.api.call_tracker_auth.app_logout (both headers), then delete
    the stored key+secret. This is the only normal way the credential ends. No silent logouts.
A6. Do not store the rep's password. Keep only the token. Never log the secret.
A7. One phone per rep by default: approving a new phone disables the old one (its next call gets 401).
A8. After signing in, call get_my_app_config and get_call_review_options once.

==================================================================================
PART B. CALL REVIEW (the rep reviews each call)  [backend already live]
==================================================================================
After a call ends the rep answers up to four things; the app sends them in a SEPARATE call (never blocks
push_calls). Missed / unanswered calls are not prompted: send the automatic "Not Connected" (see B3).
Fields: call_drop_stage (REQUIRED), next_stage, call_category, next_stage_action (free text, max 500).

B1. NEVER hardcode the picklists. GET /api/method/splinh.api.call_tracking.get_call_review_options ->
    {"message":{"call_drop_stages":[{"value","label"}],"next_stages":[...],"call_categories":[...],
                "not_connected_value":"Not Connected"|null,"server_time"}}
    Cache; refresh on app open and about hourly. Render in the given order; send "value". If never loaded,
    disable the review screen with a retry. If not_connected_value is null, do NOT send automatic reviews.
B2. POST /api/method/splinh.api.call_tracking.submit_call_review  JSON:
      call_log_name   SIM call:  M-{device_id}-{device_call_id}
                      WhatsApp:  WA-{device_id}-{correlation_id}  (exactly the correlation_id sent to ingest_whatsapp_call;
                                 it contains "|" and ":", fine in JSON)
      call_drop_stage, next_stage ("" if none), call_category (omit = leave unchanged, "" = clear),
      next_stage_action, reviewed_at (phone local time), device_id, auto (bool), force (bool)
    Answers: 200 {"status":"ok"|"updated"|"skipped"}; 404 Call Log not in the ERP yet -> retry with back-off
      (1,3,10,30,60 min, max 8 tries / 24 h); 401 hold sending 20 min, keep queued; 403 not this user's call,
      hold; 409 conflict (see B4) -> do NOT retry; 422 invalid value -> mark failed, refresh the options,
      ask the rep to re-pick, do NOT retry automatically; 5xx/timeout -> retry with back-off.
B3. Automatic review for a missed/unanswered call: call_drop_stage = not_connected_value, auto=true, nothing
    else. If a real review already exists the server answers 200 "skipped": treat as done.
B4. 409 = someone edited the review in the ERP. Body = the current review: {"status":"conflict",
    call_log_name, call_drop_stage, next_stage, next_stage_action, call_category, reviewed_at, reviewed_by,
    review_source}. Show: "This call was already reviewed in the ERP by {reviewed_by} at {reviewed_at}:
    ..." with two buttons: "Keep ERP review" (drop my pending change) and "Overwrite with mine" (resend
    with force=true). Never force an automatic review.
B5. Sync what the server has: GET /api/method/splinh.api.call_tracking.get_my_call_reviews?date=YYYY-MM-DD
    (default today; "date" = the CALL's day) -> {"message":{"reviews":[{call_log_name, call_drop_stage,
    next_stage, next_stage_action, call_category, reviewed_at, reviewed_by, review_source("app"|"erp")}],
    "server_time"}}. Call on open and every few minutes in use. Merge by call_log_name; server wins unless the
    phone has a newer unsent edit. Only real reviews are returned; a call not in the list is "not reviewed".
B6. Tile states: Reviewed / Not reviewed / Sending / Couldn't send / Reviewed in ERP. Edit = reopen and
    resubmit (last write wins). Save locally first; one pending review per call.

==================================================================================
PART C. RECORDING TOGGLE (the administrator can PAUSE recording uploads per rep)
==================================================================================
Meaning: each user has a switch "Record Calls". OFF = recording is PAUSED for that rep until it is turned
ON again. Call logs keep syncing; only the recording UPLOAD stops. The decision is by WHEN THE CALL STARTED:
  - a call that started BEFORE the pause may still upload (even if queued and uploaded after the pause began);
  - a call that started DURING a pause must NEVER be uploaded, not even after the switch is turned back on.
C1. GET /api/method/splinh.api.app_status.get_my_app_config -> {"message":{"record_calls":bool,
    "paused_periods":[{"from":"YYYY-MM-DD HH:mm:ss","to":"..."|null}],"server_time"}}  ("to": null = still
    paused; last 45 days). The heartbeat response (C3) carries the same keys, so no extra call is needed if
    you already send the heartbeat. Read it on app open and with every heartbeat.
C2. Before uploading a recording, compare the CALL's start time with paused_periods. If from <= start < to
    (or to is null) do NOT upload and DROP it from the queue permanently. Recordings of calls outside every
    period upload as normal, including ones queued before a pause. Applies to SIM and WhatsApp recordings.
    Do not count dropped items as "failed/pending" in the heartbeat. Do not touch the phone's own recorder.
C3. Server safety net: receive_call_recording answers 200 {"status":"skipped","reason":"recording paused",
    "call_log_name"} and stores NOTHING for a call made during a pause. Treat "skipped" as DONE: remove the
    item, never retry.

==================================================================================
PART D. UNCHANGED (must keep working exactly as today)
==================================================================================
push_calls, ingest_whatsapp_call (calls sync), receive_call_recording (multipart "audio_file"), heartbeat
POST /api/method/splinh.api.app_status.heartbeat (all fields optional: device_id, device_model,
android_version, app_version, last_sync_at, perm_call_log, perm_audio, perm_notifications, battery_exempt,
rec_pending, rec_failed, rec_last_error; response now also has record_calls/paused_periods/server_time).
All of them now use the two headers from A3.

==================================================================================
RETURN TO US (before testing) - answer these in writing
==================================================================================
1. Does the app treat 200 {"status":"skipped"} from receive_call_recording as done (drop, no retry)?
2. The COMPLETE list of server endpoints the app calls today (anything outside the 9 in A3 gets 403).
3. Did the old build send a "sid" cookie? Confirm 1.5 sends none.
4. Did the old build store the password and silently re-login? Confirm 1.5 stores only the token.
5. Which HTTP client and where is the token stored?

==================================================================================
END-TO-END TEST LIST (run with backend team on a pilot phone; backend will enable the switch, tick the
test rep and set the approver email before you start)
==================================================================================
 1. At the office, new phone: app_login_start -> "ok" (signed_in_via "office"), no code asked. Calls sync.
 2. Mobile data (leave office Wi-Fi), same phone, new install: app_login_start -> "otp_required"; the
    administrator gets an email with the 6-digit code; wrong code -> 401 otp_invalid with attempts_left;
    right code -> "ok" (signed_in_via "otp"). The REP gets no email.
 3. On mobile data: push_calls, heartbeat, recordings, reviews all work with the two headers.
 4. Sign in again on the same phone: no code (signed_in_via "existing").
 5. Leave the phone for 3+ hours (several hourly marks): still signed in and syncing.
 6. Call any other ERP URL with the token (for example /api/resource/Lead): 403.
 7. Logout button: app_logout -> next call 401; signing in again works.
 8. Administrator changes the rep's password: next call 401; sign in again with the new password (and a
    code if outside the office).
 9. Review: open options; review a connected call; edit it; missed call sends the automatic review;
    airplane mode then reconnect sends once; wrong value path (422) refreshes options.
10. Review conflict: administrator edits the review in the ERP; the phone edits it -> 409 dialog; Keep ERP /
    Overwrite both behave.
11. Recording toggle: administrator turns Record Calls OFF -> config shows record_calls false and an open
    period (also in the heartbeat answer). Make a call: call log syncs, NO recording uploads, no error.
12. Turn it ON: new calls record and upload; the call made during the pause still does NOT upload.
13. A call made just before the pause with its recording still queued: uploads.
14. Phone offline during a pause, online after the resume: the paused call's recording is dropped.
15. Existing flows (call sync, WhatsApp, heartbeat) unchanged.
Report for each step: pass/fail, the HTTP status and the "status" word received, and any log lines.
```
