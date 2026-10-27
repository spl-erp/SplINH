# Prompt for Claude in Chrome: add OPPORTUNITY and QUOTATION fields to the Sales Scorecard doctypes (copy the text inside the block)

```
You are working in the Frappe/ERPNext Desk UI of https://erp.splashjetink.com, logged in as a System
Manager. This is PRODUCTION: be careful, change only what is listed, do not touch any other doctype, do not
change any permission, and do NOT create, edit or delete any Sales Scorecard record. Exact fieldnames
matter: a developer's code reads and writes them, so after typing each Label check the "Fieldname" box shows
EXACTLY the name given below and correct it if not, before saving. Both doctypes are Custom doctypes built
in the UI: open them from the DocType list (Sales Scorecard Day first, then Sales Scorecard), edit, Save.

GOAL: track, per day and per month, the Opportunities and Quotations the person created and their amounts.
The rep plans them in the morning (Expected), a program counts what was created (Created) and works out Ach %.

STEP 1: DocType "Sales Scorecard Day" (child table). Add twelve fields, in this order, directly after the
  existing field payments_ach (do not change any existing field):
   Opportunities Expected        | opportunities_expected        | Int      | editable (NOT read only)
   Opportunities Created         | opportunities_created         | Int      | Read Only
   Opportunities Ach %           | opportunities_ach             | Percent  | Read Only
   Opportunity Amount Expected   | opportunity_amount_expected   | Currency | editable (NOT read only)
   Opportunity Amount Created    | opportunity_amount_created    | Currency | Read Only
   Opportunity Amount Ach %      | opportunity_amount_ach        | Percent  | Read Only
   Quotations Expected           | quotations_expected           | Int      | editable (NOT read only)
   Quotations Created            | quotations_created            | Int      | Read Only
   Quotations Ach %              | quotations_ach                | Percent  | Read Only
   Quotation Amount Expected     | quotation_amount_expected     | Currency | editable (NOT read only)
   Quotation Amount Created      | quotation_amount_created      | Currency | Read Only
   Quotation Amount Ach %        | quotation_amount_ach          | Percent  | Read Only
  Leave "In List View" UNTICKED on all twelve and do not change the grid's list view fields (a child grid
  shows at most 10 columns). Save.

STEP 2: DocType "Sales Scorecard" (parent). In the existing section "Month so far (automatic)", add twelve
  fields, all Read Only, directly after the payments fields (do not change any existing field):
   Opportunities Expected (Month)       | opportunities_expected_total      | Int
   Opportunities Created (Month)        | opportunities_created_total       | Int
   Opportunities Ach %                  | opportunities_ach                 | Percent
   Opportunity Amount Expected (Month)  | opportunity_amount_expected_total | Currency
   Opportunity Amount Created (Month)   | opportunity_amount_created_total  | Currency
   Opportunity Amount Ach %             | opportunity_amount_ach            | Percent
   Quotations Expected (Month)          | quotations_expected_total         | Int
   Quotations Created (Month)           | quotations_created_total          | Int
   Quotations Ach %                     | quotations_ach                    | Percent
   Quotation Amount Expected (Month)    | quotation_amount_expected_total   | Currency
   Quotation Amount Created (Month)     | quotation_amount_created_total    | Currency
   Quotation Amount Ach %               | quotation_amount_ach              | Percent
  Save.

CHECKS (do not create any scorecard record):
 a) Open the "Sales Scorecard Day" and "Sales Scorecard" definitions and confirm the 24 fieldnames exist
    exactly as above, with the right type and read-only setting.
 b) If any scorecard record exists, open ONE for VIEWING ONLY (do not save it): it must still open, the DILO
    tab grid must show its rows, and the Summary tab must show the new fields (empty or 0 is expected).
REPORT BACK: a table of the 24 fields (doctype, fieldname, type, read-only yes/no), and any error or deviation.
Do not do anything else.
```
