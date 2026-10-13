# Duplicate Guard — full context for UAT testing, then replication on production

Written 12 Oct 2026 from a read-only investigation of production (`erp.splashjetink.com`). Nothing in this document was applied anywhere. Purpose: let you test and build everything on **UAT first** (`uat-splashjet.sigzencloud.com`, separate host), then repeat it on production with known numbers and known traps.

Facts marked **(verified)** were read from production code or data. Items marked **(inference)** or **(draft)** are not established and must be confirmed on UAT.

---

## 0. TL;DR

1. `duplicate_guard` (app v1.1.0, installed on prod) blocks or logs duplicate Leads, Customers, Contacts, Suppliers and Employees using a pre-normalised lookup table, `tabDuplicate Index`.
2. **On production it is switched off and its index is empty** (section 5). It currently does nothing.
3. **What you want:** keep the normal Lead save exactly as it is (no blocking on save), and add an **optional "Duplicate Check" button** that opens a small form (organisation, country, phones, email), calls the guard, then either shows a popup linking to the existing record or creates the Lead.
4. **Key discovery that makes this possible:** the guard's read-only probe `duplicate_guard.api.check_duplicates` ignores the master `Enabled` switch. So run it with `Enabled = off` (no blocking on save) but the three `Check ...` boxes **on** (so the probe and the index work). This must be proven on UAT (test T3).
5. **Biggest traps:** (a) rebuilding the index while the three `Check` boxes are off **deletes the index and writes nothing**; (b) the rebuild is slow (record by record); (c) a partial index makes the button say "no duplicate" wrongly; (d) running `bench` as a user who does not own `logs/cssutils.log` fails with a PermissionError.
6. UAT is a different host and may not have splinh or the same data. Section 8 lists what to compare first.

---

## 1. The requirement (in your words, as I understood it)

- When a salesperson fills the whole Lead form and saves, a duplicate guard firing at that moment is too late and annoying. **Keep that as it is.**
- **Add one small optional button, "Duplicate Check".** It opens a form with only the fields needed for duplicate checking. It calls the duplicate guard's API.
  - If a record already exists: show a popup with a **link to that record**.
  - If nothing exists: **create the Lead**.
- Test everything on UAT, then replicate on production.

Open details to decide are in section 11.

---

## 2. How duplicate_guard works

Source: `apps/duplicate_guard/duplicate_guard/` (own git repo; on prod HEAD `f879b7b`, working tree clean; the UAT-style bench on the prod box has an identical copy). Docs inside the repo: `README.md`, `docs/CONFIGURATION.md`, `docs/INSTALLATION.md`, `docs/ADMINISTRATOR.md`, `docs/TROUBLESHOOTING.md`.

### 2.1 Pieces

| File | Role |
|---|---|
| `hooks.py` | Wires document events: for Customer, Lead, Contact, Supplier, Employee (`validate`, `before_insert` for Customer/Lead, `after_insert`, `on_update`, `after_rename`, `on_trash`) plus cache-clearing on DocType / Custom Field changes. `after_install` and `after_migrate` set defaults and warn when the index is stale. |
| `core/validator.py` | `validate_document(doc)` — returns at once if the guard is not enabled or the doctype is not guarded; otherwise collects values, searches the index and either **raises** (Strict) or writes **Duplicate Report** rows (Migration). |
| `core/search.py` | `collect_entries(doc)` turns a record into normalised Name / Phone / Email values; `find_duplicates(entries, scopes, exclude)` runs **one** indexed SQL query. |
| `core/index.py` | Keeps `tabDuplicate Index` in step: `sync_document` (delete this record's rows, insert new ones), `rebuild(doctype)`, rename and delete handlers. |
| `core/metadata.py` | Finds the name, phone and email fields from DocType metadata, including child tables. `describe(doctype)` shows what was found. |
| `core/utils.py` | Settings access, scopes, phone-region logic, `get_check_types`. |
| `core/normalizer.py` | Text and phone/email normalisation (phonenumbers library when installed). |
| `api.py` | Whitelisted functions (section 2.5). |
| `core/audit.py` | Scans the index for duplicates that already exist and writes reports. |
| Doctypes | `Duplicate Guard Settings` (Single), `Duplicate Index`, `Duplicate Report`. |

### 2.2 What is compared (the "key fields")

**Any one match blocks. It is not a combination.** Values are normalised first (case and spaces ignored; phones to E.164 like `+918103858929`).

| DocType | Scope | Name | Phone | Email |
|---|---|---|---|---|
| **Lead** | Sales | `company_name` only (never the person's name) | `phone`, `phone_ext`, `mobile_no`, `whatsapp_no`, `fax` (found live on prod via `metadata.get_phone_fields`) | `email_id` |
| Customer | Sales | `customer_name` | — (its phones live on linked Contacts) | — |
| Contact | inherits the scope of every party it links to | — | phone grid | email grid |
| Supplier | Purchase | `supplier_name` | — | — |
| Employee | HR | `employee_name` (switchable) | yes | personal email only; company domains exempt; **active employees only** |

- Lead and Customer share **Sales**, so a Lead cannot have the same Organization Name as a Customer, and their Contacts cannot share a phone or email.
- Blank fields are ignored. The same number twice on the same record is fine.
- Phone region is resolved **per record from its `country` field**; only records without a country use the settings default (IN / 91 / 10 digits). **A probe must therefore include `country`** or a bare 10-digit number is read with the default region.
- **Not guarded:** Address.
- **Possible false clashes (untested):** `fax` and `phone_ext` are treated as phone numbers, so a short extension such as `101` shared by two Leads could clash. Test in UAT; the settings allow `Ignored Fields: Lead: fax, phone_ext` or `Phone Field Overrides: Lead: mobile_no, phone, whatsapp_no`.

### 2.3 Exclusions (why a converted Lead does not clash with its Customer)

- A record never clashes with itself.
- A **Customer** ignores its originating Lead (`Customer.lead_name`).
- A **Contact** ignores every party it links to, and the originating Lead of any linked Customer.
- A **Lead with status Converted** skips the check on save and is removed from the index (its phone and email now belong to the Customer's Contact).
- Inactive employees are not indexed.

### 2.4 Modes and settings (`Duplicate Guard Settings`)

| Field | Meaning |
|---|---|
| `enabled` | Master switch for **blocking/logging on save** (`validate_document` returns immediately when off). |
| `strict_mode` | Reject duplicates with a popup naming the existing record. |
| `migration_mode` | Do not block; write a **Duplicate Report** row instead. **Overrides Strict.** |
| `check_names`, `check_phones`, `check_emails` | Which value types are collected at all. **If all three are off, nothing is collected — see the trap in section 6.1.** |
| `default_country_code`, `default_region`, `national_number_length` | Phone defaults (91 / IN / 10). |
| `guarded_doctypes` | One per line (default: Customer, Lead, Supplier, Employee, Contact). |
| `function_scopes` | `Sales: Lead, Customer` / `Purchase: Supplier` / `HR: Employee`. |
| `employee_active_statuses`, `check_employee_names`, `employee_email_exempt_domains` | HR rules. |
| `name_field_overrides`, `phone_field_overrides`, `email_field_overrides`, `ignored_fields` | Override field discovery. Format: one doctype per line, e.g. `Lead: mobile_no, phone`. |

Strict-mode error text, for reference:
*"Phone Number +918103858929 is already in use. It belongs to Lead <name>. Conflicting field on this Lead: mobile_no."* (and *"Name … belongs to Customer … (via Contact …)"* for Contact matches).

### 2.5 API surface (`duplicate_guard/api.py`)

| Function | Notes |
|---|---|
| `check_duplicates(doctype, values)` | **Read-only probe.** Builds an unsaved document from `values`, collects entries, searches the index, returns `[{value_type, value, matched_doctype, matched_name, matched_field}]`. Whitelisted for any logged-in user (no role check). **Does not consult `enabled`**; it is gated only by the `check_*` boxes through `get_check_types`. Returns `[]` when those boxes are off. No exclusion list (so it also reports a match on the record you are editing, irrelevant for new records). |
| `rebuild_index(doctype, batch_size=2000)` | Deletes that doctype's index rows, then re-reads every record one by one (`frappe.get_doc`), commits per batch. |
| `rebuild_all_indexes(batch_size=2000)` | Does the above for every guarded doctype (order: Customer, Lead, Supplier, Employee, Contact) and stamps the index version (3). |
| `mark_index_current()` | Stamps the version so `bench migrate` stops warning. |
| `setup_existing_site(batch_size)` | `rebuild_all_indexes` **plus** an audit that writes Duplicate Report rows. |
| `audit_duplicates(limit, create_reports)` | Scans the index for existing duplicates. |
| `upsert_by_legacy_id`, `bulk_upsert_by_legacy_id` | Legacy-import helpers (needs a `legacy_id` custom field, which does not exist on prod; not relevant here). |

The Settings form has two buttons: **Audit Existing Data** (calls `audit_duplicates`) and **Rebuild Duplicate Index** (calls `rebuild_all_indexes` **inside a web request**).

### 2.6 `tabDuplicate Index` schema (verified)

`name` (12-char hash, PK), `value_type` (`Name`/`Phone`/`Email`), `normalized_value`, `scope`, `reference_doctype`, `reference_name`, `source_field`, plus the standard Frappe columns. InnoDB, utf8mb4, case-insensitive collation. Indexes: single-column on `value_type`, `normalized_value`, `scope`, `reference_doctype`, `reference_name`, `creation`, `modified`; composite **`dg_scope_type_value (scope, value_type, normalized_value)`** (used by every check) and **`dg_reference (reference_doctype, reference_name)`** (used to delete a record's rows).

Example row: `Phone | +919501613975 | Sales | Lead | 2843605 | phone`.

---

## 3. Behaviour matrix (what happens in each setting combination)

Use this as the UAT checklist for section 9. "Expected" is from reading the code; **verify each row**.

| `enabled` | `migration_mode` | `strict_mode` | `check_*` | Save a duplicate Lead | Probe (`check_duplicates`) | Index upkeep on save |
|---|---|---|---|---|---|---|
| off | any | any | **on** | Saved, no check | **Works** | Maintained (rows written) |
| off | any | any | **off** | Saved, no check | Returns `[]` | **Deletes the record's rows, writes none** (drift) |
| on | on | any | on | Saved + Duplicate Report row | Works | Maintained |
| on | off | on | on | **Blocked** with popup | Works | Maintained |
| on | off | off | on | Saved, no check | Works | Maintained |

The repo docs claim "the index keeps updating regardless of Enabled". That is true only while the `check_*` boxes are on.

**Your example (Lead "danish" 8103858929 exists; create "danish" 7880239897)** with the guard checking (Strict): saved, because the person's name is not compared and the phones differ. It is blocked only on the **same Organization Name**, **same phone in any of the 5 phone fields**, or **same email** — any one of them, even if the other two differ. With `enabled = off` (today) it is always saved.

---

## 4. The planned design: optional "Duplicate Check" button

### 4.1 Settings for "button-only mode"

- `enabled` = **off** (normal Lead save unchanged, nothing blocks)
- `check_names`, `check_phones`, `check_emails` = **on** (probe and index work)
- `migration_mode` = off, `strict_mode` = whatever (irrelevant while `enabled` is off)

Proof required on UAT: tests T3 and T4.

### 4.2 Flow

1. Button **"Duplicate Check"** on the Lead **list** view (optionally also on the new-Lead form).
2. Dialog with only what the check needs: **Organization Name**, **Country** (needed to read bare numbers correctly), **Mobile**, **Phone**, **WhatsApp**, **Email**. (Lead mandatory fields on prod are `company_name`, `country`, `territory`, `status` (default Lead), `first_name`; confirm on UAT.)
3. Click **Check** → server function → guard probe.
4. **Matches found:** popup listing each match: what matched (Name / Phone / Email), the value, record type, title, status, owner/assignee, and a **clickable link** — only if the user may open it; otherwise "already exists (owner X), ask your manager" (SFA Sales Users only see their own and their team's Leads, but the probe returns any match).
5. **No match:** the dialog reveals the remaining mandatory fields (**First Name**, **Territory**) and a **Create Lead** button; the Lead is created through the normal insert (all normal hooks run) and opened.
6. **Race protection:** the create step re-checks under a short lock so two people cannot both create the same Lead at the same moment.
7. **Customers are included** in the check (Sales scope), so an Organization Name that matches an existing Customer is reported as an existing customer.

### 4.3 Response shape (suggested)

```json
{"status": "duplicate", "matches": [
  {"match_on": ["Phone"], "value": "+918103858929", "doctype": "Lead", "name": "123456",
   "title": "ABC Industries", "record_status": "Open", "owner": "x@y", "assigned_to": ["a@b"],
   "can_open": true, "url": "/app/lead/123456", "via_contact": null}
]}
{"status": "clean"}
{"status": "created", "lead": "123457"}
```

### 4.4 Implementation options

**On UAT (splinh is not installed there) — build it entirely from the UI first:**
- *API Server Script* (method name e.g. `lead_duplicate_check`) that calls `frappe.call("duplicate_guard.api.check_duplicates", doctype="Lead", values=...)` (calling a whitelisted function with `frappe.call` works inside the Server Script sandbox on this Frappe version — verified earlier for other functions), then enriches the matches.
- *Client Script* on Lead (view **List**) that adds the button and the dialog.

**On production (later):** put the server function in the `splinh` app (e.g. `splinh/api/lead_precheck.py`) so it can use real Python, Phone Lookup and a lock; keep the Client Script in the UI. A brand-new module is imported fresh on first use; later edits to it need a web restart.

### 4.5 Draft sketches (UNTESTED — for UAT experiments only)

**Server function (draft, for the splinh app; adapt for a Server Script):**

```python
import frappe
from duplicate_guard.api import check_duplicates

FIELDS = ("company_name", "country", "mobile_no", "phone", "whatsapp_no", "email_id")

@frappe.whitelist(methods=["POST"])
def check(values):
    if not frappe.has_permission("Lead", "create"):
        frappe.throw("Not permitted", frappe.PermissionError)
    values = {k: v for k, v in frappe.parse_json(values).items() if k in FIELDS and v}
    matches = check_duplicates("Lead", values)          # [] if the check_* boxes are off
    out = []
    for m in matches:
        dt, name = m["matched_doctype"], m["matched_name"]
        # Contact matches: resolve to the party (see duplicate_guard.core.validator._contact_party)
        ...
        out.append({"match_on": [m["value_type"]], "value": m["value"], "doctype": dt, "name": name,
                    "can_open": bool(frappe.has_permission(dt, "read", doc=name)),
                    "url": f"/app/{frappe.scrub(dt).replace('_','-')}/{name}"})
    return {"status": "duplicate" if out else "clean", "matches": out}
```

**Client Script (draft, List view of Lead):** add the button with `listview.page.add_inner_button(__("Duplicate Check"), () => { ... })`, open a `frappe.ui.Dialog` with the six fields, call the method with `frappe.call`, and render the result with `frappe.msgprint`. **Important:** the sigzensfa app already defines `frappe.listview_settings['Lead']` (it adds a Sales Person filter). A Client Script must **wrap** that object's `onload` instead of replacing it, otherwise the Sales Person filter disappears. Test the load order on UAT.

---

## 5. Production state (verified 12 Oct 2026)

| Item | Value |
|---|---|
| Frappe / ERPNext | 16.12.1 / 16.10.1 |
| duplicate_guard | 1.1.0, installed, hooks live, all handlers import fine, no runtime errors from its own code |
| Settings | `enabled`, `strict_mode`, `migration_mode`, `check_names`, `check_phones`, `check_emails` **all 0**; last modified **10 Sep 2026 11:12** by Administrator |
| Settings history | Jul 28–29: toggled on/off and everything switched off. Sep 10 10:01: switched on (Migration mode + all checks) → Sep 10 11:12: everything off again (about 70 minutes). **The reason it was switched off was never recorded** (see section 11). |
| Index version stamp | 3 (current) |
| **`tabDuplicate Index`** | **20 rows only** (Contact: 11 phone, 9 email). Until 10 Oct ~13:06 it held a 79,117-row snapshot from 10 Sep (Lead 75,529: Phone 29,236 / Email 18,794 / Name 27,499; Customer Name 3,568; Contact 20). It was emptied afterwards; the index is not changing. **Cause not proven**; consistent with a rebuild run while the check boxes were off. |
| Duplicate Report | 1,388 rows, all **Open**, all created in 7 seconds on 10 Sep 10:05 by an audit; never reviewed |
| Data volume | Lead **299,029**, Customer **6,915**, Contact **304,805**, Supplier 872, active Employee 171 |
| Lead shape | Mandatory: `company_name`, `country`, `territory`, `status`, `first_name`. About 294,700 Leads have numeric names (legacy CRM ids). One Contact is auto-created per Lead (`CRM Settings.auto_creation_of_contact = 1`). |
| Shared Lead phones | 1,676 Lead phone numbers are shared by 3,458 Lead records (from the splinh Phone Lookup table). **In Strict mode editing those Leads would be blocked.** |
| Name duplicates | **Not measured** (a name-duplicate count over 300k Leads was too heavy to run on production). Measure on UAT or off-peak. |
| Lead → Customer mapping | 4,640 Customers now point at their Lead (`Customer.lead_name`) and those Leads are Converted. This was done with raw writes, so it did not run the guard's hooks. The guard excludes the originating Lead for such a Customer. |
| Other custom behaviour on Lead | Client Script "Lead - Phone Country Code from Lead Country", Server Scripts "Lead - Clear Bare Phone Country Code" (DocType Event) and two profile-score scripts; sigzensfa and splashjetsfa hooks run on Lead `before_insert` / `after_insert` / `on_update`. These all fire when the button creates a Lead. **Test that the phone-prefix scripts do not alter numbers in a way that defeats matching.** |
| splinh Phone Lookup | A separate, complete indexed table (332k rows) built from Leads and Contacts; **exists on production only**. It can serve as a phone safety net while the guard index is incomplete. |

---

## 6. Problems found (the list to reproduce or fix on UAT)

### 6.1 Rebuild with the check boxes off wipes the index (design flaw)
`index.rebuild` deletes the doctype's rows first, then writes rows from `collect_entries`. With `check_*` all off, `get_check_types` is empty, so nothing is written. The same holds for `sync_document` on every save: it deletes the record's rows and writes none. **Rule: tick the three check boxes and Save before any rebuild.** A code fix (make index upkeep ignore the check boxes) would remove the trap; decide whether to patch the app or just follow the rule.

### 6.2 The index is only a snapshot
Built once on 10 Sep (partial) and never maintained since, because the check boxes were off. New records, including the ~265,000 Leads imported after 10 Sep, were never indexed.

### 6.3 Customer phones need Contacts
Customer phones and emails live on Contacts. Unless Contacts are indexed, phone checks for Customers do nothing (only 20 Contacts were indexed).

### 6.4 The rebuild is slow and fragile
It reads each record with `frappe.get_doc` (full document with child tables). Expect **hours** for ~299k Leads + ~305k Contacts on prod (not measured; measure on UAT and scale). It has to run from `bench` (a web request would time out: the Settings-form "Rebuild Duplicate Index" button runs inside the web request). A half-finished rebuild leaves a partial index and no version stamp.

### 6.5 Permission error when running `bench` as the wrong user
On prod, running `bench ... execute` as `danish` fails with `PermissionError: logs/cssutils.log` because the log file belongs to `frappe`. Run as the user that owns the logs (e.g. `sudo -u frappe -H bash -c 'cd /home/frappe/frappe-bench && bench --site erp.splashjetink.com execute ...'`), or fix the file permissions. The trailing `NameError: duplicate_guard` is only a side effect of the same failure.

### 6.6 Converted Leads and raw writes
Anything that changes a Lead or Customer with `db_set`/raw SQL (like the Lead→Customer mapping) skips `sync_document`. After such bulk changes rebuild the affected doctype, or purge Converted Leads from the index.

### 6.7 Strict mode and legacy duplicates
With existing duplicates (1,676 shared Lead phones, name duplicates unmeasured), Strict mode would block normal edits of those records. The app's own design starts such sites in Migration mode, reviews the Duplicate Report, and only then goes strict.

### 6.8 Probe disclosure
`check_duplicates` is open to any logged-in user and returns the matched record name; the button must decide what to show people who cannot open the match (section 4.2 step 4).

### 6.9 Fuzzy matches are not detected
Matching is exact after normalisation: `ABC Industries` vs `ABC Ind.` is not a match. Fuzzy suggestions would be a separate feature.

---

## 7. How to sync the index safely (the procedure to prove on UAT)

1. In Duplicate Guard Settings tick **Check Names**, **Check Phone Numbers**, **Check Emails**; keep **Enabled** off; **Save**.
2. Confirm field discovery: `bench --site <site> execute duplicate_guard.core.metadata.describe --kwargs "{'doctype': 'Lead'}"`.
3. Take a database backup.
4. Run from the terminal (not the browser), with `nohup`, off-peak, as the user that owns the logs:
   `nohup bench --site <site> execute duplicate_guard.api.rebuild_all_indexes > /tmp/dg_rebuild.log 2>&1 &`
   One doctype at a time is possible: `execute duplicate_guard.api.rebuild_index --kwargs "{'doctype': 'Lead'}"` (use `Customer`, `Contact`, …). Note the start and end time of each doctype and the record count to compute records per second.
5. When it finishes the version is stamped automatically (for single-doctype runs call `duplicate_guard.api.mark_index_current`).
6. Verify (SQL below). Compare against the source tables.
7. After that the index stays current through the hooks; rebuild again only after raw-SQL bulk changes or after changing what gets indexed.

Verification SQL:

```sql
-- index contents
SELECT reference_doctype, value_type, COUNT(*) FROM `tabDuplicate Index` GROUP BY 1,2;
-- coverage: Leads with any phone vs Leads indexed for Phone
SELECT COUNT(*) FROM tabLead WHERE IFNULL(mobile_no,'')!='' OR IFNULL(phone,'')!='' OR IFNULL(whatsapp_no,'')!='';
SELECT COUNT(DISTINCT reference_name) FROM `tabDuplicate Index` WHERE reference_doctype='Lead' AND value_type='Phone';
-- Customers indexed vs total
SELECT COUNT(DISTINCT reference_name) FROM `tabDuplicate Index` WHERE reference_doctype='Customer';
SELECT COUNT(*) FROM tabCustomer;
-- Converted Leads must NOT be indexed (expect 0)
SELECT COUNT(DISTINCT di.reference_name) FROM `tabDuplicate Index` di JOIN tabLead l ON l.name=di.reference_name
WHERE di.reference_doctype='Lead' AND l.status='Converted';
-- rows that point at a record that no longer exists (expect 0)
SELECT COUNT(DISTINCT di.reference_name) FROM `tabDuplicate Index` di LEFT JOIN tabLead l ON l.name=di.reference_name
WHERE di.reference_doctype='Lead' AND l.name IS NULL;
```

Probe example (run after the rebuild):

`bench --site <site> execute duplicate_guard.api.check_duplicates --kwargs "{'doctype':'Lead','values':{'company_name':'ABC Industries','mobile_no':'9876543210','country':'India'}}"`

---

## 8. Before you start on UAT — compare these with production

UAT (`uat-splashjet.sigzencloud.com`) is a **separate host**. The bench named `bench-uat` on the production machine is not that environment (it has no site and no splinh). Check on the real UAT:

1. Frappe / ERPNext versions (prod: 16.12.1 / 16.10.1) and that `duplicate_guard` is installed at 1.1.0 (copy the app from the prod repo if not; the code is identical to the copy in the prod box's bench-uat folder).
2. Lead mandatory fields, naming series, CRM Settings (`auto_creation_of_contact`), the Lead Client/Server Scripts listed in section 5, and the sigzensfa/splashjetsfa hooks (does UAT have the Sales-Person list script that sets `frappe.listview_settings['Lead']`?).
3. Data volume: UAT is probably much smaller, so measure **records per second** and extrapolate to prod (299k Leads, 305k Contacts, 6.9k Customers).
4. Which user runs `bench` on UAT and who owns its `logs/` files (the cssutils permission trap).
5. Does UAT contain realistic duplicates (same phone on two Leads, same organisation name, a converted Lead and its Customer)? Create them if not.
6. UAT has no splinh Phone Lookup unless you install it; the Phone-Lookup safety net is prod-only.

---

## 9. UAT test plan

| # | Test | How | Expected | Why it matters |
|---|---|---|---|---|
| T1 | Baseline | Note Settings, index counts, a duplicate Lead save | Guard off: duplicate saves freely | Starting point |
| T2 | Rebuild trap | With `check_*` off run `rebuild_index` for Lead on a small set | Index rows for Lead deleted, none written | Confirms 6.1 (use a copy/small data) |
| T3 | **Button-only mode** | `enabled` off, `check_*` on, save Settings; run the probe on a known duplicate | Probe returns matches **even though Enabled is off** | The core assumption of the design |
| T4 | Save unaffected | With T3 settings, save a Lead that duplicates an existing one | Saves normally, no popup | Keeps the normal form as it is |
| T5 | Index upkeep | With T3 settings, create/edit/delete a Lead | Rows appear/refresh/disappear in `tabDuplicate Index` | Index stays current without Enabled |
| T6 | Rebuild + timing | Run section 7 procedure, one doctype at a time | Index complete; counts match section 7 SQL; record rate measured | Extrapolate prod duration |
| T7 | Rules per key | Probe: same company; same mobile; same whatsapp; same email; same phone in a different field; different everything | Match on any one; none when all differ | Confirms "any one key" |
| T8 | Person name only | Two Leads "danish" with different phones and no company match | No match | Confirms name is company-only |
| T9 | Phone formats | `8103858929`, `08103858929`, `+91 81038 58929`, other-country number with `country` set | Same normalised value; foreign numbers distinct | Country-aware matching |
| T10 | Lead vs Customer | Probe an Organization Name equal to a Customer | Match on the Customer | Sales scope |
| T11 | Customer phone via Contact | Probe a number that exists only on a Customer's Contact | Match (needs Contacts indexed) | 6.3 |
| T12 | Conversion | Convert a Lead to a Customer; probe its phone and name | Converted Lead not matched; Customer/Contact matched once | Exclusion rules |
| T13 | Raw-SQL change | Set a Lead to Converted with `db_set`; probe | Stale index row appears until rebuilt | 6.6 |
| T14 | `fax` / `phone_ext` | Two Leads sharing only a short extension | Observe whether it matches; try `Ignored Fields` | 2.2 caveat |
| T15 | Permissions | Probe as an SFA Sales User for a Lead they cannot open | Match returned; button must hide the link | 6.8 |
| T16 | Strict reference | Temporarily `enabled`+`strict` on UAT; save duplicates | Blocked with popup naming the record | Documents strict behaviour |
| T17 | Migration reference | `enabled`+`migration`; save duplicates | Saves + Duplicate Report row | Documents migration behaviour |
| T18 | Button UI | Build the Server Script + Client Script; try matches and clean path; confirm sigzensfa Sales Person filter still appears | Popup with link; clean path creates Lead | The feature |
| T19 | Create hooks | Create a Lead via the button | Same side effects as a normal create (assignment, Contact, scores) | No surprises |
| T20 | Race | Two browsers create the same Lead at once | Only one created | Lock works |
| T21 | Performance | Time the probe; time a Lead save with checks on; run a small import | Probe in milliseconds; save overhead small | Production readiness |
| T22 | Rollback | Switch `check_*` off again | Probe returns `[]`; button must handle it | Failure mode |

Record for production: how long T6 took per 10,000 records, the probe time (T21), and anything unexpected in T3–T5.

---

## 10. Replicating on production (after UAT passes)

1. Agree the final design (section 11 decisions) and freeze the Server Script / splinh function and Client Script.
2. Backup the database. Choose a quiet window.
3. Production-only checks first (read-only): index state (section 7 SQL), Settings values, free disk, `logs/` ownership.
4. Settings: `enabled` off, `check_*` **on**, Save.
5. Rebuild from the terminal as the correct user with `nohup` (section 7). Order: Customer → Contact → Lead (so Customer phones via Contacts are covered early), and purge Converted Leads (rebuild already skips them). Expect hours; watch the log.
6. Verify with the SQL in section 7. Compare Lead phone coverage to the splinh Phone Lookup table (complete): sample a few hundred numbers and confirm the probe agrees.
7. Deploy the server function and the Client Script; test with real users in a small group first.
8. Decide separately whether to move from "button only" to Migration mode and clean up the Duplicate Report (1,388 old rows plus the 1,676 shared phone numbers) before ever considering Strict.
9. After later raw-SQL bulk changes, rebuild the affected doctype.

Production-specific cautions: the box has 6 CPU / 23 GB RAM, one `long` worker and one `short` worker shared with call syncing and transcription, so avoid heavy rebuilds during call-sync peaks; keep running prod `bench` commands as the log owner; do not click the Settings-form rebuild button.

---

## 11. Decisions and open questions

1. **What went wrong on 10 Sep (and 28–29 Jul)?** The guard was switched off within an hour each time. If it blocked saves or slowed imports, the first fix differs. Please recall or check with whoever switched it off.
2. Button placement: Lead list only, or also on the new-Lead form?
3. For users who cannot open the existing record: show the owner's name, or only "exists — ask your manager"?
4. After a clean check: create the Lead and open the full form, or only confirm?
5. Include Customers in the check (recommended: yes)?
6. Patch the app so index upkeep ignores the `check_*` boxes (removes trap 6.1), or just follow the rule?
7. Should `fax` and `phone_ext` stay in the phone check?
8. Do you want fuzzy "similar name" suggestions later?
9. UAT scope: will UAT get the full copy of production data (needed for realistic timing) or a sample?

---

## 12. Quick reference

- Repo: `apps/duplicate_guard` (HEAD `f879b7b`), docs in `apps/duplicate_guard/docs/`.
- Settings: Desk → "Duplicate Guard Settings"; reports: `/app/duplicate-report?status=Open`.
- Useful commands (replace `<site>`): `... execute duplicate_guard.core.metadata.describe --kwargs "{'doctype':'Lead'}"` · `... execute duplicate_guard.api.check_duplicates --kwargs "{...}"` · `... execute duplicate_guard.api.rebuild_index --kwargs "{'doctype':'Lead'}"` · `... execute duplicate_guard.api.rebuild_all_indexes` · `... execute duplicate_guard.api.mark_index_current`.
- Production numbers used in this document: see section 5.
- Related files: the earlier handoff analyses are in the same folder (`Dump/`).
