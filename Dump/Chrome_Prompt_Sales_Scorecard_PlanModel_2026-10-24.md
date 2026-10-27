# Prompt for Claude in Chrome: switch the Sales Scorecard to the rep's own plan (copy the text inside the block)

```
You are in the Frappe/ERPNext Desk UI of https://erp.splashjetink.com as a System Manager. PRODUCTION: change only
what is listed, nothing else. A developer's code uses the FIELDNAMES: never rename or delete a fieldname unless told;
labels, Hidden and List View flags can change. Do NOT save, create or delete any Sales Scorecard record.

CONTEXT: the manager's daily target is replaced by the REP's own morning plan. Plan = New calls, Follow-up calls,
Orders expected, Payments expected. The old target fields stay in the doctype but become HIDDEN until real targets are
designed later.

A) DocType "Sales Scorecard Day" (custom child table). Edit its Fields table:
 1. ADD these two fields (editable, NOT read-only):
      Label "New Calls Plan"    Fieldname plan_new       Type Int
      Label "Follow-up Plan"    Fieldname plan_followup  Type Int
 2. RELABEL (fieldname unchanged):
      no_target        -> "Off Day"
      actual_new       -> "New Calls Actual"
      ach_new          -> "New Calls Ach %"
      actual_followup  -> "Follow-up Actual"
      ach_followup     -> "Follow-up Ach %"
    (keep: date "Date", day "Day", orders_expected "Orders Expected", orders_received "Orders Received",
     orders_ach "Orders Ach %", payments_expected "Payments Expected", payments_received "Payments Received",
     payments_ach "Payments Ach %", remarks "Remarks", plan_saved_at "Plan Saved At", eod_saved_at "EOD Saved At")
 3. HIDE target_new and target_followup: tick "Hidden" and untick "In List View" on both (do not delete them).
 4. FIELD ORDER (drag or reorder the Fields table so it reads exactly):
      date, day, no_target, plan_new, actual_new, ach_new, plan_followup, actual_followup, ach_followup,
      orders_expected, orders_received, orders_ach, payments_expected, payments_received, payments_ach, remarks,
      plan_saved_at, eod_saved_at, then target_new, target_followup (hidden) at the end. The default
      section_break_8tfh may stay first.
 5. LIST VIEW: "In List View" ticked, Columns = 1, for EXACTLY these 10 fields and unticked for all others:
      date, day, no_target, plan_new, actual_new, ach_new, plan_followup, actual_followup, ach_followup,
      orders_expected
    Read Only must stay: date, day, actual_new, ach_new, actual_followup, ach_followup, orders_ach, payments_ach,
    plan_saved_at, eod_saved_at (and target_new, target_followup). EDITABLE: no_target, plan_new, plan_followup,
    orders_expected, orders_received, payments_expected, payments_received, remarks.

B) DocType "Sales Scorecard" (custom parent). Edit its Fields table:
 1. In the section "Month so far (automatic)" ADD two READ ONLY Int fields, placed right after the section's first
    column's last field and before the Column Break:
      Label "New Calls Plan (Month)"      Fieldname new_calls_plan_total
      Label "Follow-up Plan (Month)"      Fieldname followup_calls_plan_total
 2. HIDE (tick "Hidden", untick "In List View", do not delete): the section break targets_section
    ("Daily targets (set by the manager)") and the fields daily_new_calls_target, daily_followup_calls_target,
    new_calls_target_total, followup_calls_target_total.
 3. LIST VIEW of the parent should show: user, month_start only (untick In List View on the rest).
 4. Save the DocType.

CHECK (do not save any scorecard): open the existing record erp@splashjetink.com-2026-10-01, tab DILO. The grid headers
must read: Date, Day, Off Day, New Calls Plan, New Calls Actual, New Calls Ach %, Follow-up Plan, Follow-up Actual,
Follow-up Ach %, Orders Expected. On the Summary tab there must be no "Daily targets" section and the month section
must show New Calls Plan (Month) and Follow-up Plan (Month). Do not click Save on the record.

REPORT BACK: the final field list of both doctypes (fieldname, label, type, read-only yes/no, hidden yes/no, in list view
yes/no) and any deviation.
```
