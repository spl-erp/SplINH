# Call Review API: backend is live (for app 1.4.0)

Live on production (`erp.splashjetink.com`) since 15 Oct 2026, 12:57. Everything in your requirements document is implemented as written, with the changes listed in section 2.

## 1. Endpoint (unchanged from your placeholder)
`POST /api/method/splinh.api.call_tracking.submit_call_review`
- Auth: `Authorization: token <key>:<secret>` (same as `push_calls`). JSON body.
- Body, exactly as you send it today:
```json
{
  "call_log_name": "M-54f82f836657c65b-536_1791867215350",
  "call_drop_stage": "No Money",
  "next_stage": "Follow up",
  "next_stage_action": "Call back on Monday with revised quote",
  "call_category": "Follow Up Call",
  "reviewed_at": "2026-10-15 10:31:07",
  "device_id": "54f82f836657c65b"
}
```
- Success: HTTP 200, `{"message": {"status": "ok"}}`, or `"status": "updated"` when that call already had a review (an edit).
- Idempotent: the same call sent again overwrites the values (last write wins), no duplicates.

## 2. What changed from your document: ONE WORD
The Next Stage option is spelled **`Proposal / Quotation`** (the typo is fixed). Change that one string in the app. The old `Proposal / Quatation` is now rejected with 422.

Allowed values (exact spelling, case-sensitive; spaces at the ends are trimmed):
- `call_drop_stage` (required): `Not Connected`, `No 2-Way Connection`, `No Need`, `No Trust`, `No Money`, `No Discount`, `Converted`
- `next_stage` (optional, `""` allowed): `Meeting`, `Follow up`, `Proposal / Quotation`, `Sample`
- `call_category` (**new, optional**): `New Call` or `Follow Up Call` (exact spelling, case-sensitive). Left out or `null`: the stored value is not touched. `""`: clears it. Anything else: 422.
- `next_stage_action`: free text, trimmed, cut at 500 characters. Line breaks are kept.
- `reviewed_at`: phone local time `YYYY-MM-DD HH:mm:ss` (IST). An unreadable, pre-2020 or far-future value is replaced with the server time; the review is still saved.
- `device_id`: accepted and ignored.

The lists are read from the ERP picklists, so if the business changes them later the values the server accepts change with them (the optional options endpoint is not built).

## 3. Status codes (as in your contract)
| Situation | HTTP | Your app should |
|---|---|---|
| Saved | 200 | done |
| Call Log does not exist yet | 404 | retry with your back-off |
| Not the owner of that Call Log | 403 | hold and keep queued |
| Bad or missing token | 401 | hold 20 min |
| Invalid value (unknown stage, missing `call_drop_stage`, text where a value is expected) | **422** | mark failed, do not retry |
| Server error | 5xx | retry with back-off |

Invalid values return 422 (not Frappe's default 417), so your "do not retry" path applies.

## 4. Where the data lands (Call Log)
`Call Drop Stage`, `Next Stage`, `Call Category`, `Next Action`, and a new read-only `Reviewed At` (the phone time). Writing a review does not change the Call Log's Modified time and creates no version history.

## 5. One thing to know about older data
Before 15 Oct the Call Drop Stage dropdown had no empty first option, so ERP filled **"Not Connected"** on every new call (about 1,480 rows). That is fixed for new calls (the field now starts empty). Old rows were left as they are, so an old "Not Connected" does not mean a review was sent. A real review is recognisable by a filled `Reviewed At`.

## 6. Tests done before handoff
- A review is stored on a real Call Log and a repeat overwrites it; the other user gets 403; a missing log gets 404; a bad stage, a missing stage and the old spelling get 422; no version rows and no Modified change (run on production and rolled back, nothing kept).
- Without a token 403; with a bad token 401; GET 403.
- No review has been received from a phone yet. Please run your acceptance checklist (sections 7 and 8 of your document) on a real device and tell us if anything differs.

Not changed: call sync, recordings, heartbeat.


---

# Round 2: options endpoint, my-reviews endpoint, overwrite protection

All three use the same token auth as `push_calls` and only ever touch the calling user's own Call Logs. Not live until the web server is reloaded (we will tell you).

## A. `GET /api/method/splinh.api.call_tracking.get_call_review_options`
```json
{"message": {
  "call_drop_stages": [{"value": "Not Connected", "label": "Not Connected"}, ...],
  "next_stages":      [{"value": "...", "label": "..."}, ...],
  "call_categories":  [{"value": "New Call", "label": "New Call"}, {"value": "Follow Up Call", "label": "Follow Up Call"}],
  "not_connected_value": "Not Connected",
  "server_time": "2026-10-15T16:19:37"
}}
```
- Read live from the ERP picklists: exact spelling and order, blank option dropped, `label` = `value`. The lists belong to the ERP (edited in the UI), so **do not hardcode them**; the list you saw in the first document is no longer the live one.
- `not_connected_value` is `"Not Connected"` as long as that is one of the drop stages; if the ERP ever removes it the value is `null` and the app should skip the automatic missed-call review.

## B. `GET /api/method/splinh.api.call_tracking.get_my_call_reviews?date=YYYY-MM-DD` (default today)
```json
{"message": {"reviews": [
  {"call_log_name": "M-...", "call_drop_stage": "...", "next_stage": "...", "next_stage_action": "...",
   "call_category": "New Call", "reviewed_at": "2026-10-15 10:31:07",
   "reviewed_by": "rep@splashjetink.com", "review_source": "app"}
 ], "server_time": "2026-10-15T16:19:37"}}
```
- `date` is the **call's date** (calls that started that day), so a review saved later, or in the ERP the next day, still comes back under its call.
- Only calls with a real review (`reviewed_at` set). The ~1,480 old rows that were pre-filled with "Not Connected" by the dropdown default are excluded.
- `review_source` is `app` or `erp`. Ordered by call start time, capped at 500. A bad `date` gives 422. Unreviewed fields are `""`.
- Speed: about 2 ms for the busiest user today (105 calls, 44 reviews). A new database index on (owner, call start time) keeps it fast as the table grows. Network time dominates.

## C. `submit_call_review` overwrite protection
New optional body fields: `force` (true/false) and `auto` (true/false). Everything else is unchanged.
- **`auto: true`** (the automatic missed-call review): if a real review already exists, nothing is written and the answer is **200 `{"status": "skipped"}`**. Otherwise it is stored like any review. It must still carry a valid `call_drop_stage`.
- **Review made in the ERP** (`review_source` = `erp`) and the request has no `force: true`: **HTTP 409**, and the body is the current review in exactly the same shape as a `reviews[]` item plus `"status": "conflict"`:
```json
{"message": {"status": "conflict", "call_log_name": "...", "call_drop_stage": "...", "next_stage": "...",
 "next_stage_action": "...", "call_category": "...", "reviewed_at": "...", "reviewed_by": "manager@...", "review_source": "erp"}}
```
  With `force: true` the app's review replaces it. A review made by the app is always overwritten without `force`.
- Every write sets `reviewed_by` (the calling user) and `review_source = "app"`. A review edited in the ERP form is stamped `erp`, the editing user and the time, automatically.
- Call Category stays optional on submit (left out = stored value untouched).

## Answers to your four questions
1. Paths: `GET .../splinh.api.call_tracking.get_call_review_options` and `GET .../splinh.api.call_tracking.get_my_call_reviews`.
2. Call Log fields: `custom_reviewed_by` (User) and `custom_review_source` (`app` / `erp`).
3. Yes: the 409 body matches a `reviews[]` item (plus `status`), and the old default "Not Connected" rows are excluded from B.
4. About 2 ms of server time for roughly 100 calls (measured on the busiest user today), so effectively the network round trip.
