# Contact Merge (splinh), handoff, 19 Nov 2026

Contact list > Actions > **Merge Contacts** (2 to 4), and a **Similar Contacts** banner on the Contact form. Modelled on Lead Merge; everything is in splinh and stays local until the ERP go-live.

## Rules (owner decisions)
- **Only duplicates merge:** every selected Contact must share a phone number (digits compared) or an email with another one of the group. Name alone is not enough. Otherwise the dialog and the server refuse with the reason.
- **Different parties allowed:** the main Contact is linked to all parties; the dialog shows a warning line naming them.
- **Portal login:** if the Contacts have different logins the person chooses (the `User` row); the other login stays a User.
- **Opt-out:** `Unsubscribed` is a normal choice with a warning; the read-only Waba consent keeps the most restrictive value (No, then Unknown, then Yes).
- **Primary phone / email:** the main Contact's primaries stay; the others' numbers are added as extra numbers (exactly one primary of each kind).
- **After the merge:** Customers / Suppliers whose primary Contact is the survivor get mobile, email, first and last name refreshed; the survivor is renamed to "Full Name-FirstParty" when that differs (in a savepoint).
- **Who:** write permission on every selected Contact. **Undo:** none; Contact Merge Log keeps a full snapshot.

## Files
`custom/contact_merge.py` (rules), `api/contact_merge.py` (preview, merge, similar), `custom/contact_merge_links.py` (rename hooks: task text, Waba records, Phone Lookup), `custom/contact_merge_list.js` + `contact_merge_form.js` (Client Scripts "Contact List Merge Contacts" / "Contact Form Merge Contacts", installed by `_ensure_contact_merge` on migrate), `splinh/doctype/contact_merge_log`, `tests/test_contact_merge.py`. `custom/lead_merge_links.py::_waba` now takes a doctype (default Lead).

## Deploy
migrate, restart, clear-cache, hard refresh (or `bench execute splinh.custom.splinh_setup._ensure_contact_merge` for the Client Scripts only).
