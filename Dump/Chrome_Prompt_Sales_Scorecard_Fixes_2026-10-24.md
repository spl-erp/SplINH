# Prompt for Claude in Chrome: tidy the Sales Scorecard Day table (copy the text inside the block)

```
You are in the Frappe/ERPNext Desk UI of https://erp.splashjetink.com as a System Manager. PRODUCTION: change
only what is listed, nothing else. A developer's code uses the FIELDNAMES, so never change a fieldname that is
kept; only the labels, list-view flags and the two deletions below.

Open the DocType "Sales Scorecard Day" (custom child table) and edit its Fields table:

1) DELETE these two fields (they duplicate the manager's target): plan_new and plan_followup.

2) RENAME LABELS (fieldname stays the same):
   target_new       -> "New Calls Target"
   actual_new       -> "New Calls Actual"
   ach_new          -> "New Calls Ach %"
   target_followup  -> "Follow-up Target"
   actual_followup  -> "Follow-up Actual"
   ach_followup     -> "Follow-up Ach %"
   (keep: date "Date", day "Day", no_target "No Target", orders_expected "Orders Expected", orders_received
   "Orders Received", orders_ach "Orders Ach %", payments_expected "Payments Expected", payments_received
   "Payments Received", payments_ach "Payments Ach %", remarks "Remarks", plan_saved_at "Plan Saved At",
   eod_saved_at "EOD Saved At")

3) LIST VIEW (the grid shows at most 10 column widths; give each of these Columns = 1). Tick "In List View" for
   EXACTLY these 10 fields and untick it for all others:
   date, day, no_target, target_new, actual_new, ach_new, target_followup, actual_followup, ach_followup,
   orders_expected
   Read-only must stay as it is: date, day, target_*, actual_*, ach_*, orders_ach, payments_ach, plan_saved_at,
   eod_saved_at are READ ONLY; no_target, orders_expected, orders_received, payments_expected,
   payments_received and remarks stay EDITABLE.

4) Save the DocType. Do not create, edit or delete any Sales Scorecard record (a test record
   erp@splashjetink.com-2026-10-01 exists; leave it alone).

Then open that existing test scorecard, tab DILO, and confirm the grid headers read: Date, Day, No Target, New
Calls Target, New Calls Actual, New Calls Ach %, Follow-up Target, Follow-up Actual, Follow-up Ach %, Orders
Expected. Do not click Save on the scorecard.

REPORT BACK: the final list of fields of Sales Scorecard Day (fieldname, label, type, read-only yes/no, in list
view yes/no) and any deviation.
```
