# Call Tracker: per-user "Record Calls" toggle (for the app team)

Written for the Android app developers. Backend is written and tested; **it goes live after a server reload (we will tell you)**. Do not rely on it before then.

## 1. What it is
Each user has a tick box **Record Calls (INH)** in the ERP (default ON). Unticking it **pauses recording for that rep until it is ticked again**. While paused, the rep's **call logs keep syncing as normal; only the recording upload stops.**

Important rule: the decision is made on **when the call started**, not on the switch's state when the upload happens.
- A call that started **before** the pause may still upload (it was made while recording was on), even if its upload happens after the pause began.
- A call that started **during** a pause must **never** be uploaded, **not even after the switch is turned back on**.

## 2. How the app learns it
New GET (token allowed, same two headers as the other calls): `/api/method/splinh.api.app_status.get_my_app_config`
```json
{"message": {"record_calls": true,
  "paused_periods": [{"from": "2026-10-20 14:00:00", "to": "2026-10-21 09:30:00"},
                     {"from": "2026-10-22 11:00:00", "to": null}],
  "server_time": "2026-10-22T17:26:31"}}
```
- `to: null` means still paused. Times are IST, the same clock as call start times. Only periods from the last 45 days are listed.
- The **heartbeat response** now also carries the same three keys (`record_calls`, `paused_periods`, `server_time`) next to `ok`, so you do not need an extra call if you already send the heartbeat.
- Read it on app open and with each heartbeat. If the phone is offline or not up to date, the server still protects (section 4).

## 3. What the app does
- Before uploading a recording, compare **the call's start time** with `paused_periods`. If it falls inside a period (`from` <= start < `to`, or `to` is null) do **not upload it and drop it from the queue**: it must never be uploaded later.
- Recordings of calls that started outside every period upload as normal, including ones that were queued before a pause.
- Applies to SIM and WhatsApp recordings alike.
- Do not delete or change anything on the phone itself; the phone's own recorder is not under our control. Only the upload is.
- Do not count dropped items in "recordings failed/pending" in the heartbeat.

## 4. Server safety net
If the app still sends a recording for a call that started inside a pause, `receive_call_recording` answers **HTTP 200** with
`{"status": "skipped", "reason": "recording paused", "call_log_name": "..."}` and stores **nothing**. Treat `skipped` as done: remove the item from the queue and do not retry. (Old builds that ignore this still do not get their paused recordings stored.)

## 5. Other things to know
- Already-stored recordings are not deleted when a rep is paused.
- Pausing/resuming is done by an administrator on the User form (tab Splashjet INH) or in bulk from the User list. The ERP keeps a list of every pause (who, from, to).
- When a rep is paused, the Call Log still syncs; only the recording is missing, so "recording missing" for those calls is expected.

## 6. Test list (when we say it is live)
1. Switch is ON: recordings upload as today.
2. Administrator turns it OFF: `get_my_app_config` shows `record_calls: false` and an open period (the heartbeat answer too).
3. Make a call while paused: the call log syncs, no recording uploads, no error on the phone.
4. Turn it back ON: new calls record; the call made during the pause still does **not** upload.
5. A call made just before the pause whose recording was still queued: uploads.
6. Phone offline during a pause and online after the resume: the paused call's recording is dropped (by period, or by the server `skipped`).
