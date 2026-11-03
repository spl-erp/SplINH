# Prompt for Claude in Chrome: tidy the layout of the Sales Scorecard Day child table (copy the text inside the block)

```
You are working in the Frappe/ERPNext Desk UI of https://erp.splashjetink.com, logged in as a System
Manager. This is PRODUCTION. This job is LAYOUT ONLY: change the ORDER of fields, add Section Breaks and
Column Breaks, and choose which fields show as columns in the table grid. Do NOT rename any fieldname, do NOT
change any field type, label, Read Only, Hidden, Mandatory or default, do NOT delete any field, do NOT touch
any permission or any other doctype, and do NOT create, edit or delete any Sales Scorecard record. A program
reads and writes every field by its fieldname, so fieldnames must stay exactly as they are.

Open DocType "Sales Scorecard Day" (child table, custom doctype) from the DocType list, edit, Save at the end.
It has 48 fields today. Several rows of the fields table can be dragged by the handle on the left to reorder.

GOAL: (1) a rep or manager who opens one day row ("Editing Row") sees the fields in logical blocks with
headings, each block in two columns, instead of one long list. (2) the table grid (the list of days on the
Sales Scorecard form) shows, in ten narrow columns, what matters most: the plan, the SYSTEM count, the REP's
number and the Ach % for new and follow-up calls.

STEP 1. Row form order. Rearrange the fields into these blocks, in this order. Where a block name is in
quotes it is a new Section Break (Fieldtype "Section Break", Label = the quoted text; leave Fieldname as
auto-generated). "|" means a new Column Break (Fieldtype "Column Break", no label) that starts the right-hand
column of the block. Fields not listed in a block stay where they are relative to their own block.

  (existing first Section Break section_break_8tfh stays first)
  date, day, | no_target
  Section "New calls":
     plan_new, actual_new, ach_new,  |  new_calls_reported, new_calls_reported_set, new_calls_final
  Section "Follow-up calls":
     plan_followup, actual_followup, ach_followup,  |  followup_calls_reported, followup_calls_reported_set, followup_calls_final
  Section "Visits":
     plan_new_visits, plan_followup_visits,  |  new_visits_received, followup_visits_received
  Section "Orders and payments":
     orders_expected, order_amount_expected, payments_expected,  |  orders_received, order_amount_received, payments_received,  |  orders_ach, order_amount_ach, payments_ach
  Section "Opportunities":
     opportunities_expected, opportunity_amount_expected,  |  opportunities_created, opportunity_amount_created,  |  opportunities_ach, opportunity_amount_ach
  Section "Quotations":
     quotations_expected, quotation_amount_expected,  |  quotations_created, quotation_amount_created,  |  quotations_ach, quotation_amount_ach
  Section "Remarks and status":
     remarks, |  eod_submitted, eod_submitted_at, plan_saved_at, eod_saved_at
  last (hidden, leave as they are): target_new, target_followup

  A Section Break must sit BEFORE the first field of its block, and a Column Break between the columns.
  Do not leave an empty section.

STEP 2. Grid columns. In the same DocType, set "In List View" ON for exactly these ten fields and OFF for
every other field of this doctype (the grid allows at most 10 column widths; give each of the ten the
"Columns" value 1):
     date, no_target, plan_new, actual_new, new_calls_reported, ach_new,
     plan_followup, actual_followup, followup_calls_reported, ach_followup
  (So day, orders_expected and order_amount_expected, which were shown before, are no longer grid columns;
  they stay in the row form.)

STEP 3. Save the DocType. Reload the Desk page (hard refresh).

STEP 4. Verify (read only). Open the Sales Scorecard record "erp@splashjetink.com-2026-11-01" without
changing anything. Check that:
  a. the Daily table now shows ten columns, with the headings listed in step 2;
  b. on the row for 02-11-2026 the "New Calls (Rep)" column shows 5 and the "Follow-up Calls (Rep)" column
     shows 10, while "New Calls Actual" and "Follow-up Actual" show 0 and the Ach % columns show 33.33% and
     50.00%;
  c. clicking the edit icon of one row shows the blocks "New calls", "Follow-up calls", "Visits", "Orders and
     payments", "Opportunities", "Quotations", "Remarks and status", each with its fields in two or three
     columns, and no field is missing (compare with the list above).
Close the row without saving. If anything in the record has a "Not Saved" marker, reload and do not save.

REPORT BACK: the final order of all 48 fields with their section and column breaks, which fields have "In
List View" on, and the result of each check in step 4 (a, b, c). If a step could not be done, say which and why.
```
