# Prompt for Claude in Chrome: add VISIT and REP-OWNED CALL COUNT fields to the Sales Scorecard doctypes (copy the text inside the block)

```
You are working in the Frappe/ERPNext Desk UI of https://erp.splashjetink.com, logged in as a System
Manager. This is PRODUCTION: be careful, change only what is listed, do not touch any other doctype, do not
change any permission, and do NOT create, edit or delete any Sales Scorecard record. Exact fieldnames
matter: a developer's code reads and writes them, so after typing each Label check the "Fieldname" box shows
EXACTLY the name given below and correct it if not, before saving. Both doctypes are Custom doctypes built
in the UI: open them from the DocType list (Sales Scorecard Day first, then Sales Scorecard), edit, Save.

GOAL: (1) the rep types visits (planned in the morning, received at the end of the day), numbers only.
(2) the rep can overwrite the New / Follow-up call count at the end of the day. The table keeps THREE numbers
per day for dashboards: the system count (existing actual_new / actual_followup, do not touch), the rep's
number (new fields, "reported") and the final number the Ach % uses (new fields, "final", filled by a program).
An Int column cannot be empty, so "the rep typed nothing" is a tick box next to each rep number.

STEP 1: DocType "Sales Scorecard Day" (child table). Add ten fields, in this order, directly after the
  existing field quotation_amount_ach (do not change any existing field):
   New Visits Plan                    | plan_new_visits             | Int   | editable (NOT read only)
   Follow-up Visits Plan              | plan_followup_visits        | Int   | editable (NOT read only)
   New Visits Received                | new_visits_received         | Int   | editable (NOT read only)
   Follow-up Visits Received          | followup_visits_received    | Int   | editable (NOT read only)
   New Calls (Rep)                    | new_calls_reported          | Int   | editable (NOT read only)
   New Calls Overwritten by Rep       | new_calls_reported_set      | Check | editable (NOT read only)
   New Calls Final                    | new_calls_final             | Int   | Read Only
   Follow-up Calls (Rep)              | followup_calls_reported     | Int   | editable (NOT read only)
   Follow-up Calls Overwritten by Rep | followup_calls_reported_set | Check | editable (NOT read only)
   Follow-up Calls Final              | followup_calls_final        | Int   | Read Only
  Leave "In List View" UNTICKED on all of them and do not change the grid's list view fields (a child grid
  shows at most 10 columns). Leave every default value empty/0. Save.

STEP 2: DocType "Sales Scorecard" (parent). In the existing section "Month so far (automatic)", add two
  fields, both Read Only, directly after the existing field followup_calls_actual_total (do not change any
  existing field, do not rename the existing "Actual (Month)" labels):
   New Calls Final (Month)           | new_calls_final_total       | Int
   Follow-up Calls Final (Month)     | followup_calls_final_total  | Int
  Save.

CHECKS (do not create any scorecard record):
 a) Open the "Sales Scorecard Day" and "Sales Scorecard" definitions and confirm the 12 fieldnames exist
    exactly as above, with the right type and read-only setting.
 b) If any scorecard record exists, open ONE for VIEWING ONLY (do not save it): it must still open, the DILO
    tab grid must show its rows, and the Summary tab must show the two new month fields (0 is expected).
REPORT BACK: a table of the 12 fields (doctype, fieldname, type, read-only yes/no), and any error or deviation.
Do not do anything else.
```
