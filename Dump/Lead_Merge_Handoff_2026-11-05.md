# Lead Merge (2026-11-05)

Lead list > select 2 to 4 Leads > Actions > **Merge Leads**. One main Lead stays; the others are deleted and everything connected to them moves to the main Lead.

## What the user sees
- A floating window: one column per Lead, the radio "Main Lead" on top (the Lead with most calls/contacts is suggested), then every field where the Leads differ ("Show all fields" shows the rest).
- Per field: pick the value that goes into the main Lead (radio). Untouched fields keep the main Lead's value, even when empty.
- Free-text fields also have **Append**: Lead 1 "hi" + Lead 2 "hey" -> "hi hey" (one-line text: space; multi-line text: new line). Not offered for email, phone/mobile/WhatsApp, GSTIN, pincode, website, record number, status, links and other non-text fields. A repeated text is added once. A one-line result over 140 characters blocks Merge.
- A "Result in main Lead" column shows what will be saved. Confirm warns that the other Leads are deleted.

## What moves
Calls (Call Log custom_lead and links), Contacts, Addresses, Customers (Customer.lead_name), Issues, Enquiry, Sample, Lead List / Lead Table / Prospect Lead / Distributors Lead rows, comments, ToDos, files. Child tables (Notes, Question Answer, tags, old CRM data) are combined into the main Lead, identical rows once. Phone Lookup and Duplicate Guard stay in step through their own Lead hooks.

## Rules and safeguards
- Who: anyone with write permission on every selected Lead (SFA team visibility still applies).
- 2 to 4 Leads; refused if a Lead changed after the window was opened; all Leads locked during the merge; everything is one transaction (a failure leaves nothing half-merged).
- The merge itself is Frappe's own `rename_doc(merge=True)`, called with `rebuild_search=False` (the default would queue a global-search rebuild of all Leads).
- The values are saved on the main Lead AFTER the merge (Lead.validate refuses an email another Lead still has).
- Lead's own rules still apply on that save: a Lead that has a Customer is always **Converted**, whatever status was chosen. The dialog tells the user which fields ended up different from the choice.
- Converted Leads can be merged; their Customers then point at the main Lead.
- Cannot be undone from the screen. Two copies are kept: the **Lead Merge Log** (System Manager) with who/when, choices, before/after of the main Lead, how many records pointed at each deleted Lead, and a JSON copy of each deleted Lead incl. child tables; and Frappe's own Deleted Document.

## Restore a mistaken merge (manual)
1. Lead Merge Log > open the row > `snapshot` has each deleted Lead as JSON; `changes` has the main Lead's values before the merge.
2. Re-create the Lead from its snapshot (drop `name`, `creation`, `modified`, child-row `name`s), then move back by hand what belonged to it (calls, contacts ...); `link_counts` says how many records of which doctype pointed at it.
3. Put the main Lead's old values back from `changes.fields[...].before`.

## Files
`splinh/custom/lead_merge.py` (rules, no database), `splinh/api/lead_merge.py` (get_merge_preview, merge_leads), `splinh/custom/lead_merge_list.js` (the dialog; wraps the Lead list onload like Duplicate Check), `splinh/splinh/doctype/lead_merge_log/`, `_ensure_lead_merge` in `custom/splinh_setup.py` (creates the Client Script "Lead List Merge Leads" on migrate), tests `tests/test_lead_merge.py` (34).

## Deploy
1. Commit and push. 2. The new doctype table (Lead Merge Log) already exists on production (loaded 2026-11-05). 3. The Client Script is created by `bench --site erp.splashjetink.com migrate` (after_migrate) or by `bench execute splinh.custom.splinh_setup._ensure_lead_merge`. 4. Graceful web reload, then `bench clear-cache` last; users hard-refresh once.
Rollback: disable/delete the Client Script "Lead List Merge Leads" (the menu item disappears); the API stays unused.

## Tested (2026-11-05, production, dummy data, rolled back)
30/30: preview, refusals (1 or 5 Leads, append on email/read-only, stale stamp, no Lead write role, team user outside visibility), merge of 4 Leads with a Customer, Address, Call Log, ToDo, Comment, Contacts and Notes: values chosen/appended, links moved, Contacts/Address survive, Phone Lookup cleaned, log + snapshot written, Deleted Document archive, about 6 s for 3 merges. Not tested: the dialog in a real browser.
