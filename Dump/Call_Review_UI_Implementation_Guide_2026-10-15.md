# Call Review: implementation guide for the app UI team

Status: backend code is written and tested; **it goes live after a server reload (we will tell you the moment it does)**. Build against this document now; run section 8 once it is live. The detailed API contract is in `Call_Review_API_Handoff_for_App_Team_2026-10-15.md`; this guide says what to build and how each response should look to the rep.

## 1. What the rep does
After a call the rep answers up to four things about it. The answers go to the ERP's Call Log for that call; managers see them there and can edit them.

| Field (body key) | Picker / input | Required | Source of the choices |
|---|---|---|---|
| Call Drop Stage (`call_drop_stage`) | single choice | **yes** | `call_drop_stages` from the options endpoint |
| Next Stage (`next_stage`) | single choice | no | `next_stages` |
| Call Category (`call_category`) | single choice | no | `call_categories` (today: New Call, Follow Up Call) |
| Next Action (`next_stage_action`) | multi-line text, max 500 chars | no | free text |

Missed and unanswered calls are **not** prompted: the app sends `Not Connected` by itself (section 4).

## 2. Never hardcode the lists
The ERP owns the picklists and changes them in its UI. Fetch them from `GET .../get_call_review_options` (cache on the phone, refresh on app open and about once an hour) and render exactly what comes back: `value` is what you send, `label` is what you show, order as given. If the fetch has never succeeded, show the review screen disabled with a retry, not a stale built-in list. `not_connected_value` is the string to send for automatic reviews; if it is `null`, do not send automatic reviews at all.

## 3. Screens and states (one review per call)
1. **Review form** (opens after a SIM or WhatsApp call ends, and from the call tile for editing): the four inputs above. Save is enabled only when Call Drop Stage is chosen.
2. **Call tile status** on Home / Calls: `Reviewed` (with the stage text), `Not reviewed`, `Sending...`, `Couldn't send`, or `Reviewed in ERP` (see below). Source of truth is the server: after sync, a call that is in `get_my_call_reviews` is Reviewed.
3. **Edit**: tapping a reviewed call reopens the form with the current values; saving sends the same call again (last write wins).

## 4. Sending (`POST submit_call_review`, same token as `push_calls`)
Body: `call_log_name`, `call_drop_stage`, `next_stage`, `call_category`, `next_stage_action`, `reviewed_at` (phone local time `YYYY-MM-DD HH:mm:ss`), `device_id`, and the two optional flags `auto` and `force`.
- **`call_log_name`**: SIM call = `M-{device_id}-{device_call_id}`. WhatsApp call = `WA-{device_id}-{correlation_id}` using the exact `correlation_id` string sent to `ingest_whatsapp_call` (it contains `|` and `:`; that is fine in a JSON body).
- **Send omitted fields as absent or `""`**: absent `call_category` leaves the stored value untouched, `""` clears it.
- **Automatic review** for missed / unanswered calls: `call_drop_stage = not_connected_value`, `auto: true`, no other fields.
- **`force: true`** only after the rep explicitly confirms an overwrite (section 6).

Responses and what the UI does:
| Response | Meaning | UI |
|---|---|---|
| 200 `ok` / `updated` | saved | tile = Reviewed |
| 200 `skipped` | automatic review ignored because a real review exists | tile = Reviewed (no message); the next list refresh shows the real values |
| 404 | the Call Log is not in the ERP yet | stay `Sending...`, retry with your back-off |
| 401 | bad or expired token | hold all sending ~20 min, keep queued, no attempts burned |
| 403 | not this user's call | hold like 401 |
| **409** | **review already made in the ERP** | conflict dialog (section 6); do not retry automatically |
| **422** | a value is not allowed (list changed since the phone cached it, text where a value is expected) | mark `Couldn't send`, refresh the options, ask the rep to re-pick; do not retry automatically |
| 5xx / timeout | server problem | retry with back-off |

## 5. Syncing what the server has (`GET get_my_call_reviews?date=YYYY-MM-DD`)
Call on app open and every few minutes while the app is in use, for today (and for the day shown if the rep browses back). Response `{"reviews": [...], "server_time": "..."}`; each item: `call_log_name, call_drop_stage, next_stage, next_stage_action, call_category, reviewed_at, reviewed_by, review_source` (`app` or `erp`).
- `date` is the **call's** day, so a review made later (or in the ERP the next day) still appears under its call.
- Only calls with a real review are returned. A call that is not in the list is simply not reviewed; old rows that were once pre-filled "Not Connected" are not in it.
- Merge by `call_log_name`: the server item wins over local state **unless** the phone has a newer unsent edit for that call (then keep the local one and show Sending).
- Use `server_time` rather than the phone clock if you compare times.

## 6. The ERP-edit conflict (409)
A manager can edit a review in the ERP. If the rep's phone later sends a different review for that call, the server refuses (409) and returns the ERP's current review in the same shape as a list item (plus `"status": "conflict"`).
UI: show a dialog such as "This call was already reviewed in the ERP by {reviewed_by} at {reviewed_at}: {drop stage}, {next stage}, {next action}." with two buttons: **Keep ERP review** (drop the pending change, show the server values) and **Overwrite with mine** (resend with `force: true`). The automatic `Not Connected` must never be forced: it is silently dropped when a real review exists.

## 7. Offline and ordering rules
- Save locally first, send in the background; the review never blocks call sync, recordings or the heartbeat.
- A review can reach the server before its Call Log exists (calls are created by a background job): 404 is normal then; retry.
- Keep one pending review per call; a newer save replaces the older unsent one.
- An offline phone must not undo a later ERP review: that is exactly what `auto` and the 409 are for, so always send `auto: true` for the automatic review.

## 8. Test plan once it is live (run on real phones, SIM and WhatsApp)
1. Options fetch returns the three lists and `not_connected_value`; the pickers show them in order; change a list in the ERP, refresh on the phone, see the change with no app release.
2. Review a connected call: tile shows Reviewed; the Call Log in the ERP shows the four fields, Reviewed At, Reviewed By = the rep, Review Source = app.
3. Edit the same call: values replaced, no duplicate.
4. Review with only Call Drop Stage (all optional fields empty): saved.
5. Missed call: automatic `Not Connected` with `auto`; Call Log shows it with Source = app.
6. Missed call that the rep or a manager already reviewed for real: the automatic one returns `skipped`; the real review stays.
7. Edit a review in the ERP form (change Next Action): Review Source becomes erp. Then from the phone save a change to that call: conflict dialog with the ERP values; Keep ERP -> no change; Overwrite -> the ERP shows the phone's values and Source = app.
8. Airplane mode: save, reconnect, it arrives once; saving the same review twice leaves one set of values.
9. Call not yet in the ERP (send right after the call): 404 then success on retry.
10. Wrong token: 401, the review stays queued and sends after sign-in.
11. A stale list value (remove an option in the ERP, send the old one): 422, the options refresh, the rep re-picks.
12. WhatsApp call review: stored on the WA call.
13. Today's list after a reinstall / second phone: reviews come back from `get_my_call_reviews`.
14. Call sync, recordings and the heartbeat behave as before.

## 9. Things the ERP side already did (so you do not need to)
Fields and database index exist; old pre-filled "Not Connected" rows are excluded from the list; call drop stage is stored even though only ERP managers can edit it in the ERP form (the API writes it on the rep's behalf, only for the rep's own calls).
