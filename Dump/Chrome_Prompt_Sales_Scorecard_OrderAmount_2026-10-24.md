# Prompt for Claude in Chrome: add ORDER AMOUNT fields to the Sales Scorecard doctypes (copy the text inside the block)

```
You are working in the Frappe/ERPNext Desk UI of https://erp.splashjetink.com, logged in as a System
Manager. This is PRODUCTION: be careful, change only what is listed, do not touch any other doctype, do not
change any permission, and do NOT create, edit or delete any Sales Scorecard record. Exact fieldnames
matter: a developer's code reads and writes them, so after typing each Label check the "Fieldname" box shows
EXACTLY the name given below and correct it if not, before saving. Both doctypes are Custom doctypes built
in the UI: open them from the DocType list (Sales Scorecard Day first, then Sales Scorecard), edit, Save.

GOAL: add "Order Amount" (rupees of orders expected / received in a day) next to the existing Orders count
fields. The fields are filled by a program except where noted.

STEP 1: DocType "Sales Scorecard Day" (child table). Add three fields:
   Order Amount Expected | order_amount_expected | Currency | editable (NOT read only)
   Order Amount Received | order_amount_received | Currency | editable (NOT read only)
   Order Amount Ach %    | order_amount_ach       | Percent  | Read Only
  Position: order_amount_expected directly after orders_expected; order_amount_received directly after
  orders_received; order_amount_ach directly after orders_ach. Do not change any existing field.
  List view / grid: a child grid shows at most 10 columns. Currently the list view fields are: date, day,
  no_target, plan_new, actual_new, ach_new, plan_followup, actual_followup, ach_followup, orders_expected.
  Put order_amount_expected in the list view (column width 1) and, to stay at 10, untick "In List View" on
  the field no_target ("Off Day") only (it stays in the row's edit form). Tell me exactly which fields are in
  the list view at the end.
  Save.

STEP 2: DocType "Sales Scorecard" (parent). In the existing section "Month so far (automatic)", add three
  fields, all Read Only, directly after the orders totals / orders_ach (before the payments fields):
   Order Amount Expected (Month) | order_amount_expected_total | Currency
   Order Amount Received (Month) | order_amount_received_total | Currency
   Order Amount Ach %            | order_amount_ach             | Percent
  Do not change any existing field. Save.

CHECKS (do not create any scorecard record):
 a) Open "Sales Scorecard Day" and "Sales Scorecard" definitions and confirm the six fieldnames exist exactly
    as above, with the right type and read-only setting.
 b) Open the existing scorecard record erp@splashjetink.com-2026-10-01 for VIEWING ONLY (do not save it):
    it must still open, the DILO tab grid must show the rows, and the Summary tab must show the new fields
    (empty or 0 is expected).
REPORT BACK: a table of the six fields (fieldname, type, read-only yes/no, in list view yes/no), the final
list-view fields of Sales Scorecard Day, and any error or deviation. Do not do anything else.
```
