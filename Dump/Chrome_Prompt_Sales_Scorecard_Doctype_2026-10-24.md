# Prompt for Claude in Chrome: create the Sales Scorecard doctypes (copy the text inside the block)

```
You are working in the Frappe/ERPNext Desk UI of https://erp.splashjetink.com, logged in as a System
Manager. This is PRODUCTION: be careful, create only what is listed, do not edit any other doctype, do
not change any permission other than the ones stated, do not enable developer mode, and do NOT create
any Sales Scorecard record. Exact field names matter: a developer's code reads and writes these
fieldnames, so after typing each Label check the "Fieldname" box shows EXACTLY the name given below and
correct it if not (before saving the DocType). On production you MUST tick "Custom" on each new DocType
(otherwise Frappe refuses with "Not in Developer Mode").

GOAL: one scorecard per salesperson per MONTH (like an Excel workbook per person per month) with tabs
DILO (daily rows), WILO and MILO (placeholders for later). Automatic fields are READ ONLY (a program
fills them); manual fields are editable.

STEP 1: create the CHILD table doctype first. Awesomebar -> "New DocType".
  Name: Sales Scorecard Day      Module: SplINH (if not offered, pick the closest custom module)
  Tick: Custom, Is Child Table (Istable).   Do not tick Submittable.
  Add these fields in this order (Label | Fieldname | Type | notes):
   Date              | date                | Date     | Read Only, In List View, Reqd
   Day               | day                 | Data     | Read Only, In List View
   No Target         | no_target           | Check    | In List View  (editable)
   Target (New)      | target_new          | Int      | Read Only, In List View
   Plan (New)        | plan_new            | Int      | In List View  (editable)
   Actual (New)      | actual_new          | Int      | Read Only, In List View
   Ach % (New)       | ach_new             | Percent  | Read Only, In List View
   Target (Follow-up)| target_followup     | Int      | Read Only, In List View
   Plan (Follow-up)  | plan_followup       | Int      | In List View  (editable)
   Actual (Follow-up)| actual_followup     | Int      | Read Only, In List View
   Ach % (Follow-up) | ach_followup        | Percent  | Read Only, In List View
   Orders Expected   | orders_expected     | Int      | In List View  (editable)
   Orders Received   | orders_received     | Int      | In List View  (editable)
   Orders Ach %      | orders_ach          | Percent  | Read Only
   Payments Expected | payments_expected   | Currency | In List View  (editable)
   Payments Received | payments_received   | Currency | In List View  (editable)
   Payments Ach %    | payments_ach        | Percent  | Read Only
   Remarks           | remarks             | Small Text | (editable)
   Plan Saved At     | plan_saved_at       | Datetime | Read Only
   EOD Saved At      | eod_saved_at        | Datetime | Read Only
  Grid width note: a child grid shows at most 10 column-widths. Give every In-List-View field column width
  1 ("Columns" = 1). If Frappe will not allow all of them in the list view, keep these in the list view:
  date, day, no_target, target_new, plan_new, actual_new, ach_new, target_followup, plan_followup,
  actual_followup, and untick In List View on the rest (they stay editable in the row's edit form). Tell
  me exactly which fields ended up in the list view.
  Permissions: leave the child table's permissions empty (a child table inherits from its parent).
  Save.

STEP 2: create the PARENT doctype. "New DocType".
  Name: Sales Scorecard          Module: SplINH      Tick: Custom.  Not a child table, not submittable.
  Naming: Naming Rule = "Expression" and Auto Name = format:{user}-{month_start}
  Sort Field = month_start, Sort Order = DESC.  Title Field = employee_name.  Track Changes = on.
  Fields in this order (Label | Fieldname | Type | notes):
   [Tab Break]       Summary                 | summary_tab            | Tab Break
   User              | user                  | Link (Options: User)   | Reqd, In List View, In Standard Filter
   Month             | month_start           | Date                   | Reqd, In List View, In Standard Filter,
                                                                        description "First day of the month"
   Employee Name     | employee_name         | Data                   | Read Only
   [Column Break]
   Sales Person      | sales_person          | Link (Options: Sales Person) | Read Only
   Reporting Manager | reporting_manager     | Link (Options: User)   | Read Only
   [Section Break] label "Daily targets (set by the manager)" | targets_section
   Daily New Calls Target       | daily_new_calls_target       | Int | In List View
   [Column Break]
   Daily Follow-up Calls Target | daily_followup_calls_target  | Int | In List View
   [Section Break] label "Month so far (automatic)" | month_section
   New Calls Target (Month)        | new_calls_target_total        | Int      | Read Only
   New Calls Actual (Month)        | new_calls_actual_total        | Int      | Read Only
   New Calls Ach %                 | new_calls_ach                 | Percent  | Read Only
   Follow-up Calls Target (Month)  | followup_calls_target_total   | Int      | Read Only
   Follow-up Calls Actual (Month)  | followup_calls_actual_total   | Int      | Read Only
   Follow-up Calls Ach %           | followup_calls_ach            | Percent  | Read Only
   [Column Break]
   Orders Expected (Month)         | orders_expected_total         | Int      | Read Only
   Orders Received (Month)         | orders_received_total         | Int      | Read Only
   Orders Ach %                    | orders_ach                    | Percent  | Read Only
   Payments Expected (Month)       | payments_expected_total       | Currency | Read Only
   Payments Received (Month)       | payments_received_total       | Currency | Read Only
   Payments Ach %                  | payments_ach                  | Percent  | Read Only
   [Tab Break] DILO                  | dilo_tab               | Tab Break
   Daily                            | daily                  | Table (Options: Sales Scorecard Day)
   [Tab Break] WILO                  | wilo_tab               | Tab Break
   WILO Note                        | wilo_note              | HTML, Options: <p>Weekly scorecard: coming in the next phase.</p>
   [Tab Break] MILO                  | milo_tab               | Tab Break
   MILO Note                        | milo_note              | HTML, Options: <p>Monthly scorecard: coming in the next phase.</p>
  Permissions: ONLY the role System Manager at level 0 with Read, Write, Create, Delete, Report, Export,
  Print, Email, Share ticked. Remove any other default role row. (Managers will be added later by the owner.)
  Save.

STEP 3: add one field to the EXISTING doctype "Call Tracker Settings" using Customize Form
  (Customize Form -> choose "Call Tracker Settings"; it is a single doctype).
  Add a Section Break labelled "DILO (daily scorecard)" with fieldname dilo_section, below the last field,
  then a field: Label "DILO plan cut-off time", Fieldname dilo_plan_cutoff_time, Type Time, Default 12:00:00,
  Description "Until this time of day a rep can edit the day's plan in the app; the end-of-day figures stay
  editable until midnight." Update. If Customize Form refuses for any reason, skip it and tell me.

CHECKS (do not create any scorecard record):
 a) Open the Sales Scorecard list: it must open empty, with columns User, Month, Daily New Calls Target,
    Daily Follow-up Calls Target.
 b) Open the "Sales Scorecard" doctype definition and confirm all fieldnames above exist exactly.
 c) Open Sales Scorecard Day and confirm the same.
REPORT BACK: a table of every field (fieldname, type, read-only yes/no, in list view yes/no) for both
doctypes, which of the child fields ended up in the list view, whether the Call Tracker Settings field
was added, and any error or deviation you had to make. Do not do anything else.
```
