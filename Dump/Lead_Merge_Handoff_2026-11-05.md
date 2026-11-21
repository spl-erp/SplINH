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

## More places to start a merge (2026-11-13)
- **Duplicate Check result:** when the check finds two or more **Leads**, a "Merge these Leads" button opens the same dialog with them (first four; merge again afterwards for more). Not shown for Customers or Contacts.
- **Lead form banner:** "N other Lead(s) share this Lead's phone number / email / company name: ..." with a **Merge** button (this Lead is suggested as the main one). Shown only to users who can edit the Lead; never for a new or unsaved Lead. Server: `splinh.api.lead_duplicates.similar_leads` (read-only; phone through Phone Lookup, email, company name through Duplicate Guard's name index; only Leads the user may read; about 7 ms).
- One dialog file (`custom/lead_merge_list.js`) is installed as two Client Scripts: **Lead List Merge Leads** (List) and **Lead Form Merge Leads** (Form, dialog + `custom/lead_merge_form.js`), created by `_ensure_lead_merge` on migrate. Other scripts call `window.splinh_lead_merge.open(names, {main})`.
- Also fixed in the merge itself (`custom/lead_merge_links.py`, Lead rename hooks): task text that Frappe drops when the same person is assigned on both Leads is kept as a comment on the main Lead; Waba conversations, locked Recipient Sets, campaign recipients and notification references follow the Lead when Waba is installed.
- Deploy: `bench --site <site> migrate` (or `bench --site <site> execute splinh.custom.splinh_setup._ensure_lead_merge`), then clear-cache and a hard refresh. Rollback: disable the Client Script "Lead Form Merge Leads"; the Duplicate Check button hides itself when the dialog is not loaded.
