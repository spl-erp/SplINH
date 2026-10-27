# Call Tracker app: DILO (Day in the Life Of) - API and field contract for the app team (plan model)

Written for the app developers. Screens and design are yours; this document fixes the data, the rules and the API. **Status: the backend code is ready; the endpoints go live after the next server reload (we will tell you). Until then use the sample responses below as mocks.** Both endpoints use the same two headers as every other Call Tracker call:
`Authorization: token <api_key>:<api_secret>` and `Frappe-Authorization-Source: Call Tracker Token`. (Both methods are on the token's allowed list.)

**What changed against the first draft:** there is no manager target any more. The **rep sets their own plan every morning** (new calls, follow-up calls, orders expected, payments expected) and the day is measured against that plan. The rep can also mark today as an **Off Day**. Manager approval of plans is on hold (not in this version). Real targets will come later as a separate layer.

## 1. What DILO is
A daily scorecard per salesperson: the **plan** the rep saves in the morning, the **actual** numbers, and an **Ach %**.
| Item | Source | Who edits |
|---|---|---|
| New calls (first time): plan, actual, Ach % | plan typed by the rep; **actual is automatic** (counted from the rep's reviewed calls) | plan: rep; actual: nobody |
| Follow-up calls: plan, actual, Ach % | same | same |
| Orders expected / received (numbers) | typed by the rep | rep |
| Order amount expected / received (rupees) | typed by the rep | rep |
| Payments expected / received (amounts in rupees) | typed by the rep | rep |
| Off Day (leave, holiday) | tick set by the rep, today only | rep |
| Remarks / reason for gap | typed by the rep | rep (optional) |

## 2. Rules the server applies (so you do not have to)
- **Actual New / Follow-up calls** = the rep's Call Logs of that day (call start date, IST) whose Call Category is **"New Call"** / **"Follow Up Call"**. Direction and duration do not matter. A call with no category (for example the automatic "Not Connected" review of an unanswered call) counts as nothing.
- **Therefore the review screen must make Call Category mandatory for every connected call** (change in the app's existing review feature).
- Only reviews already uploaded to the server are counted; reviews waiting offline on the phone count when they arrive (you may show a local "n reviews pending" note).
- **A day "has a plan"** when the rep has saved a plan for it and the day is not an off day. A day without a plan shows **"No plan"**: Ach % is `null` and `plan.saved` is false. The rep starts every day empty (nothing is copied from yesterday).
- **Order amount** is the rupee value of the orders (separate from the order count). Expected is in the plan group (until the cut-off), received in the end-of-day group (until midnight).
- **Opportunities and Quotations (added 2026-10-26)** work like the call counts. The rep plans four numbers in the morning (plan group, same cut-off): `opportunities_expected`, `opportunity_amount_expected`, `quotations_expected`, `quotation_amount_expected`. The server counts what the person **created** that day (the record's creator): Opportunities by `opportunity_amount`; Quotations while draft or submitted (not cancelled) by `rounded_total`. Ach % = created / expected x 100, uncapped, `null` without a saved plan, a 0 plan or on an off day; it does not wait for the end of day. Amounts are read live, so editing a record later changes its day. New keys are `plan.opportunities`, `plan.opportunity_amount`, `plan.quotations`, `plan.quotation_amount`, the same four under `actual` and `ach`, under `month.plan`, `month.actual` and `month.ach`, and per day `<name>_expected` / `<name>_created` / `<name>_ach` in `month.days`. Until the fields exist on the server these keys come back as 0 / `null` and the four save keys are ignored.
- **Updated records and talk time (added 2026-10-26, display-only, read-only, nothing to save):**
  - `opportunities_updated` / `quotations_updated` = records the rep saved any change on that day, counted once per record however many saves; records created that same day are not counted (they are Created); cancelled Quotations are left out; counted whatever the creator. It comes from the change log of Opportunity and Quotation (Track Changes), so it only counts from the day that was switched on and starts at 0. Comments and assignments do not count.
  - Talk time = `duration` of the rep's answered calls (duration > 0) of that day by Call Category, New Call and Follow Up Call separately, either direction. Internal and uncategorised calls count in neither. Each block is `{"seconds", "calls", "avg_seconds"}` (average in whole seconds, 0 when there are no calls).
  - Where: today `actual.opportunities_updated`, `actual.quotations_updated`, `talk.new_calls`, `talk.followup_calls`; month to date (all days up to today, not only days with a plan) `month.actual.opportunities_updated`, `month.actual.quotations_updated`, `month.talk.new_calls`, `month.talk.followup_calls` (month average = month seconds / month calls); every entry of `month.days` has `opportunities_updated`, `quotations_updated`, `new_talk_seconds`, `new_talk_calls`, `followup_talk_seconds`, `followup_talk_calls` (the app works the daily average out). All whole numbers; the keys are `null` only if the server could not compute them.
- **Ach % of orders, order amount and payments is `null` until the end-of-day figures of that day are saved** (`eod.saved` false), so the app can show "Not entered"; once saved it is received / expected (0.0 if nothing received). Calls Ach % needs only the saved plan.
- **All-zero plan:** a save with all plan values 0 is accepted: the day has a plan (`plan.saved` true) and Ach % is `null` for every measure whose plan is 0.
- **Ach %** (calls) = actual / plan x 100, **not capped** (160 is shown as 160). Orders = received / expected x 100; payments the same with amounts. Two decimals. `null` when there is no plan, the plan value is 0, or the day is an off day.
- **Off Day:** the rep switches it on from the app, **today only**, and may switch it off again until midnight. No plan is expected, the day is left out of the month plan and the month Ach %. Calls made on an off day still count as actual (month actual).
- **Locks** (server clock, IST, today only):
  - **Plan group** = `plan_new_calls`, `plan_followup_calls`, `orders_expected`, `order_amount_expected`, `payments_expected`, `opportunities_expected`, `opportunity_amount_expected`, `quotations_expected`, `quotation_amount_expected`: editable today, **until the cut-off** (`locks.plan_cutoff`, default 12:00:00). It can be saved several times before the cut-off (the last save wins). **After the cut-off a plan cannot be entered or changed in the app (no late plan).**
  - **End-of-day group** = `orders_received`, `payments_received`, `remarks`, and the `off_day` switch: editable today until midnight.
  - Any past day is read-only. A future day cannot be saved.
- **Month totals:** month plan, orders, order amount and payments = sums over the days that have a plan (off days and no-plan days are left out). Month Ach % = actual on those days / month plan (`null` while the month plan is 0). Calls made on days without a plan still appear in the month actual.
- Everything is the **caller's own** data. Any signed-in user gets this month's scorecard created on their first read or save; `no_scorecard` (404) now only means a past month with no record.

## 3. API 1: read the day and the month
`GET /api/method/splinh.api.scorecard.get_my_dilo?date=YYYY-MM-DD` (date optional, default today; any date of any month)
```json
{
  "status": "ok",
  "date": "2026-10-24",
  "day": "Saturday",
  "is_off_day": false,
  "plan":    {"new_calls": 15, "followup_calls": 20, "orders_expected": 3, "order_amount_expected": 200000.0, "payments_expected": 150000.0, "saved": true},
  "actual":  {"new_calls": 14, "followup_calls": 17},
  "ach":     {"new_calls": 93.33, "followup_calls": 85.0, "orders": 66.67, "order_amount": 75.0, "payments": 50.0},
  "eod":     {"orders_received": 2, "order_amount_received": 150000.0, "payments_received": 75000.0, "remarks": "1 order slipped; PO awaited", "saved": true},
  "locks":   {"plan_editable": true, "eod_editable": true, "plan_cutoff": "12:00:00"},
  "month": {
    "month_start": "2026-10-01",
    "plan":    {"new_calls": 315, "followup_calls": 420},
    "actual":  {"new_calls": 280, "followup_calls": 350},
    "ach":     {"new_calls": 88.89, "followup_calls": 83.33, "orders": 70.0, "order_amount": 72.5, "payments": 62.5},
    "orders":  {"expected": 40, "received": 28},
    "order_amount": {"expected": 2000000.0, "received": 1450000.0},
    "payments":{"expected": 1200000.0, "received": 750000.0},
    "days": [
      {"date": "2026-10-01", "day": "Thursday", "is_off_day": false, "has_plan": true,
       "plan_new": 15, "actual_new": 14, "ach_new": 93.33,
       "plan_followup": 20, "actual_followup": 17, "ach_followup": 85.0,
       "orders_expected": 3, "orders_received": 2, "orders_ach": 66.67,
       "order_amount_expected": 200000.0, "order_amount_received": 150000.0, "order_amount_ach": 75.0,
       "payments_expected": 150000.0, "payments_received": 75000.0, "payments_ach": 50.0,
       "remarks": "1 order slipped; PO awaited"}
      /* one entry per day of the month, oldest first */
    ]
  },
  "server_time": "2026-10-24T11:20:05"
}
```
Types: counts are integers; payments are numbers in rupees; Ach % are numbers (can exceed 100) or `null` (show "-"); dates `YYYY-MM-DD`; times `HH:mm:ss`. A day without a saved plan: `plan.saved` false, plan values 0, `ach.*` `null` -> show "No plan". `month.days` also lists days with no data.

**Removed:** the old `target`, `is_target_day`, `month.target` and per-day `target_new` / `target_followup` / `is_target_day` keys are no longer sent.

Errors: `404 {"status":"no_scorecard"}` (a past month with no record); `503 {"status":"not_ready"}`; `401`/`403` as for the other endpoints.

## 4. API 2: save the plan, the off-day switch or the end-of-day figures
`POST /api/method/splinh.api.scorecard.save_my_dilo` JSON body (all keys optional; only the keys you send change; `date` defaults to today and only today is accepted):
```json
{
  "date": "2026-10-24",
  "plan_new_calls": 15, "plan_followup_calls": 20, "orders_expected": 3, "order_amount_expected": 200000, "payments_expected": 150000,
  "off_day": false,
  "opportunities_expected": 4, "opportunity_amount_expected": 200000, "quotations_expected": 5, "quotation_amount_expected": 300000,
  "orders_received": 2, "order_amount_received": 150000, "payments_received": 75000, "remarks": "1 order slipped; PO awaited"
}
```
Success: `200` with the **same body as the read call**. Values: integers >= 0 for the counts and orders, numbers >= 0 for the order amounts and payments, remarks up to 500 characters, `off_day` true/false; omit a key (or send `null`) to leave it unchanged. Saving any plan field marks the day as having a plan. Unknown keys are ignored.
| HTTP | `status` | Meaning and what to do |
|---|---|---|
| 403 | `locked` | A sent field is locked (plan group after the cut-off, off-day or end-of-day fields on a past day). Body lists `locked_fields` and `reason`. Show it and refresh the day. Do not retry. |
| 422 | `invalid` | A value is not allowed (negative, fractional count, not a number, future date). Body has `field`. |
| 404 | `no_scorecard` | No scorecard for this user. |
| 503 | `not_ready` | Feature switched off or not set up. Retry later. |
| 401 / 403 (no `status`) | | Credential ended or not allowed, same as everywhere else. |

## 5. When to call and screens
- **Open the DILO screen:** `get_my_dilo` for today. Refresh every 2 to 5 minutes while visible, and after each review upload (actual counts move with reviews).
- **Morning:** the rep enters the four plan numbers and saves (one Save for the group; may save again before the cut-off). Disable them when `locks.plan_editable` is false and show "No plan" if `plan.saved` is false after the cut-off. Do not rely on the phone clock; use `server_time`.
- **Off Day switch:** visible on today's card; sends `off_day`. Allowed until midnight, also after the cut-off. When on, show the day as off and hide Ach %.
- **Evening:** orders received, payments received, remarks; Save.
- Save locally first and retry on network errors; a repeat of the same save is harmless.
- Show a month-to-date summary and the list of previous days from `month.days`.

## 6. What the app team must change or build
1. **Review screen:** Call Category mandatory on connected calls.
2. **DILO screen:** plan inputs for the two call counts (new: replaces the old read-only target), Off Day switch, "No plan" state, Ach % from the plan (uncapped), month summary, previous days.
3. **Offline:** show last data with a "last updated" time; queue saves.
4. The deprecated keys are already removed from the server answers.

## 7. Test plan (when we say it is live)
1. Fresh morning: nothing saved -> `plan.saved` false, Ach `null` ("No plan").
2. Save the four plan numbers before the cut-off -> saved, Ach % computed from them; change them again -> last value wins.
3. After the cut-off: saving any plan field returns 403 `locked`; end-of-day fields and the off-day switch still work.
4. Save received and remarks -> orders and payments Ach % update.
5. Off Day on -> Ach `null`, the day leaves the month plan; off again -> back.
6. Review a connected call as "New Call" -> New actual +1 at the next refresh; "Follow Up Call" -> Follow-up +1; unconnected call -> no change.
7. Past day -> 403 on any save; future day -> 422; negative or text -> 422 with `field`.
8. A user outside the Sales Person tree -> this month's scorecard is created on first read (no sales person or manager on it); a past month with no record -> 404 `no_scorecard`.
9. Month summary and previous days equal the sum of the daily rows (days without a plan left out of the plan and Ach %).

## 8. Submit day (End of day lock held by the server)
- **Field:** a tick `eod_submitted` on every day row in the ERP (plus the optional time `eod_submitted_at`). Until it exists the server sends no `eod.submitted` and ignores `submit_eod`, so the app shows no Submit button.
- **Read:** `eod.submitted` (true/false) and `eod.submitted_at` (`YYYY-MM-DDTHH:mm:ss` or null) in `get_my_dilo`; every `month.days` entry has `eod_submitted`.
- **Submit:** `save_my_dilo` with `"submit_eod": true`, optionally with the End of day figures in the same call. The server validates and saves the figures, ticks `eod_submitted`, sets the time, marks the End of day as saved (so orders / order amount / payments Ach % become real numbers) and answers 200 with the usual full body (`eod.submitted: true`). Allowed on a day without a plan. Today only: a past day is 403 `locked` (`locked_fields: ["submit_eod"]`), a future day 422.
- **Repeat:** `submit_eod` on an already submitted day is a 200 with the unchanged body, also when the call carries the same figures (an app retry). Different figures: 403 below.
- **Lock:** once submitted, a save carrying `orders_received`, `order_amount_received`, `payments_received` or `remarks` with a value different from the stored one is refused: `403 {"status":"locked","locked_fields":[...],"reason":"End of day already submitted"}`. The plan group, `off_day` and everything else are unaffected.
- **Reopen:** only in the ERP (System Manager): untick `eod_submitted`; the time is cleared. Every phone shows it at its next refresh.
