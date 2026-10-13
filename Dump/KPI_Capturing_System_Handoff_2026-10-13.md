# KPI Capturing System — handoff for the production ERP (`erp.splashjetink.com`)

Written 13 Oct 2026. Purpose: let whoever deploys this on production (a person or an AI model with no earlier context) understand what was built, exactly how each number is defined, what it depends on, and what to check before and after switching it on. **Nothing in this document has been applied to production.** Everything marked *verified* was checked on UAT (`uat-splashjet.sigzencloud.com`); everything marked *must verify on production* was **not** checked there.

Related files in this folder: `Duplicate_Guard_UAT_Handoff_2026-10-12.md`, `Call_Recording_Analysis_2026-10-10.md`. The original requirement sheet is `KPI capturing.xlsx` (bench root, UAT box). The full build history is in the bench-root `CHANGELOG.md` (entries dated 2026-10-01).

---

## 0. TL;DR

1. **What it is:** one record per salesperson per day (**Sales KPI Entry**) that shows activity the system already knows (leads, calls, visits) as read-only numbers, plus a few manual fields the salesperson types in on a Web Form. A 3-layer dashboard lets managers see totals and drill down.
2. **Status:** built and verified on UAT (1 Oct 2026). On 13 Oct 2026 it was **moved out of the vendor's `splashjet` app into `splinh`** so it can be deployed with the rest of our work. Verified after the move: old and new code give identical dashboard output on real UAT data.
3. **Production has not got it.** Production's own `splashjet` app is a different, unrelated app, so the old location could never have been deployed there. Deployment = pull `splinh`, `bench migrate`, check the items in section 8.
4. **Biggest production risks:** (a) the Lead Qualification number depends on **Server Scripts** and on two Lead score fields that may not exist or may be named differently on production (section 8); (b) several modules the dashboard reads from (Insights, the SFA "Start-End Meeting" doctype) must exist there; (c) a User-save crash bug that exists on UAT may or may not exist on production.
5. **Not built yet** (waiting on policy decisions): the sales funnel / cost-centre group, Incoming Enquiry, assigned Leads/Opportunities, response time, call-duration buckets, Google Meet / WhatsApp chat recordings (section 9).

---

## 1. Business purpose and source of requirements

`KPI capturing.xlsx` (Sheet1) lists the daily KPIs wanted per salesperson, each marked *system generated* or *manual*. What was built covers these rows:

| Requirement row | Built as | Source |
|---|---|---|
| Lead Generation | `lead_generation_count` | system |
| Lead Score of generated leads | `lead_score` | system |
| Lead Qualification | `lead_qualification_count` | system |
| Lead Qualification Score | `lead_qualification_score` | system |
| Cold Call | `cold_call_count` | system (Call Log) |
| Followup Call | `followup_call_count` | system (Call Log) |
| WhatsApp Calling | `whatsapp_call_count` | system (Call Log) |
| Visit Existing / Visit New / Meeting Duration | `visit_existing_count`, `visit_new_count`, `meeting_duration_minutes` | system (SFA visits) |
| Office Meeting, Training, Customer Complaints, Taking/Giving Report, Google Meet, WhatsApp Chat, Other Activity | manual fields | typed on the Web Form |

Sheet2 of that workbook is an unrelated Lead-quality-score proposal; it is not part of this build.

---

## 2. What exists (component map)

| Layer | Component | Name / route |
|---|---|---|
| Data | DocType | **Sales KPI Entry** (module `SplINH`) |
| Data | Custom Field on Lead | `custom_lead_score_updated_on` (Datetime, hidden, read-only) |
| Input | Web Form | **`sales-kpi-entry`** at `/sales-kpi-entry` (published, login required) |
| Dashboard L1 | Workspace | **KPI Dashboard** (8 Number Cards, 3 Charts, 2 shortcuts) |
| Dashboard L1 | Script Reports | "KPI Cold vs Followup Breakdown", "KPI Visit Existing vs New Breakdown" |
| Dashboard L2 | Insights workbook | **KPI Dashboard** (4 queries: Lead, Call Log, Visits, Manual Entries) |
| Dashboard L3 | Desk Page | **`kpi-dashboard-detail`** → `/app/kpi-dashboard-detail` (`/desk/…` in v16) |
| Navigation | Workspace Sidebar `CRM` | shortcuts to the Workspace and to the detail page |

**Number Cards** (all prefixed `KPI - `): native count cards for *Lead Generation*, *Lead Qualification*, *WhatsApp Calling*; method-backed ("Custom") cards for *Cold Call*, *Followup Call*, *Visit Existing*, *Visit New*, *Meeting Duration (min)*. They are filtered to **the current week** (Monday start); that bound is recomputed on every migrate.
**Charts:** "KPI - Cold vs Followup Call" (donut), "KPI - Visit Existing vs New" (donut), "KPI - Lead Generation per day" (line).

---

## 3. Exact definitions of every number

All days are **server-time calendar days** (`entry_date` 00:00 to next 00:00). Verified against hand-run SQL on UAT.

| Field | Definition |
|---|---|
| **Lead Generation** | Leads where `lead_owner` = the user and `creation` falls in the day. |
| **Lead Score** | Average over those Leads of `(custom_export_profile_percent + custom_profile_percent) / 2`; a missing value counts as 0. Rounded to 2 decimals. |
| **Lead Qualification** | Leads owned by the user whose `custom_lead_score_updated_on` falls in the day. The timestamp is set **only when the combined score actually changes on an existing Lead** (creating a Lead does not count; that is Lead Generation). |
| **Lead Qualification Score** | Same average as Lead Score, over the qualified Leads. |
| **Cold Call** | `Call Log` rows with `owner` = user, `type = Outgoing`, `start_time` in the day, whose linked Lead (`custom_lead`) has status **Lead, Open, Replied or Interested**. (Replied/Interested are deliberately still "cold": the real cutoff is Opportunity.) |
| **Followup Call** | The user's other Outgoing calls that day: no linked Lead, or a Lead past those statuses. **Incoming calls count in neither.** |
| **WhatsApp Calling** | Call Logs with `owner` = user, `custom_source = 'WhatsApp'`, `start_time` in the day (any direction). |
| **Visit Existing** | `Start-End Meeting` rows with `owner` = user, `start_meeting` in the day, `id_type` **Customer or Opportunity**. (Opportunity is a provisional choice; only 6 such rows existed.) |
| **Visit New** | Same, `id_type` **Lead**. Other id types (e.g. Retailer) are not counted. |
| **Meeting Duration (min)** | Sum of `end_meeting − start_meeting` in minutes for the user's meetings that started that day and have an end time. Rounded to 1 decimal. |

The constants behind the call and visit buckets live in one place (`LEAD_COLD_STATUSES`, `VISIT_EXISTING_ID_TYPES`, `VISIT_NEW_ID_TYPES` in the doctype controller) and are imported by the Number Card methods and the detail page, so the three views cannot drift apart. **If a definition changes, change it there and update this section.**

---

## 4. Data model and access

**Sales KPI Entry** is named `"{user}-{entry_date}"`, which also enforces **one entry per user per day** (a second insert fails with a duplicate-name error). `user` and `entry_date` are required and read-only. The ten system-generated fields are Frappe **Virtual Fields**: no database columns, recomputed fresh whenever a record is opened, never editable. Only the manual fields are stored:

`office_meeting_duration`, `training_duration`, `customer_complaints_count`, `customer_complaints_duration`, `reporting_duration`, `google_meet_count`, `google_meet_duration`, `google_meet_notes`, `whatsapp_chat_count`, `whatsapp_chat_duration`, `whatsapp_chat_notes`, `other_activity_type`, `other_activity_count`, `other_activity_duration`, `other_activity_summary`.

**Permissions** (`track_changes` on):

| Role | Access |
|---|---|
| System Manager | full |
| Sales User, SFA Sales User | read / create / write, **own records only** (`if_owner`) |
| Sales Manager | read, report, export |

The **detail page** and its API method (`splinh.custom.kpi_dashboard_detail.get_dashboard_data`) are restricted to **Sales Manager and System Manager** with an explicit `frappe.only_for` check, independent of how the page was reached.

The **Web Form** shows system fields read-only and manual fields editable, laid out like ERPNext's own `issues` Web Form. `allow_multiple` and `allow_edit` are on, deleting is off.

---

## 5. How the dashboard is built (3 layers)

1. **Workspace "KPI Dashboard"**: shortcuts ("All Entries", "Fill Today's Entry (Web Form)"), the 8 Number Cards under "This Week", and the 3 charts under "Breakdown". Cold/Followup/Visit numbers cannot be plain Number Cards (they need a join and bucketing), so they are "Custom" cards backed by whitelisted functions in `kpi_dashboard_cards.py`.
2. **Insights workbook "KPI Dashboard"** (Insights v3): one query per source table, each per-row so a manager can drill to individual Leads, calls, visits and manual entries. It is **created once and never overwritten**, so hand edits in the Insights UI survive migrates.
3. **Desk Page `kpi-dashboard-detail`**: filters (date range, employee), summary tiles, three breakdown datasets and a per-(user, day) drill-down table that mirrors the doctype's grain. It uses four bulk `GROUP BY` queries (Lead, Call Log, Start-End Meeting, Sales KPI Entry) merged in Python, deliberately **not** one virtual-field computation per row (that would be an N+1 query storm over a team's date range). The page name is exactly 20 characters because Frappe truncates page names at 20.

---

## 6. Where the code lives (`apps/splinh/splinh/`)

| File | Role |
|---|---|
| `splinh/doctype/sales_kpi_entry/` | DocType JSON + controller with the virtual-field properties and the bucket constants |
| `splinh/web_form/sales_kpi_entry/` | Web Form |
| `splinh/page/kpi_dashboard_detail/` | Desk Page JSON + JS |
| `custom/kpi_dashboard_cards.py` | Method-backed Number Card functions |
| `custom/kpi_dashboard_detail.py` | The page's data method |
| `custom/kpi_dashboard_insights.py` | Creates the Insights workbook |
| `custom/splinh_setup.py` | `ensure_kpi_dashboard()` (cards, reports, charts, workspace, sidebar, page record, module unblocking), `ensure_kpi_dashboard_insights()`, `_ensure_lead_score_updated_on_field()` and the stamp logic inside the four Lead-score Server Scripts |

`splinh_setup.execute()` runs on every `bench migrate` (`after_migrate`); each step is idempotent and isolated, so a failing step is logged to the Error Log (`splinh_setup: <step> failed`) and never stops the others. **`bench install-app` alone creates nothing from `splinh_setup`; a migrate must follow.**

---

## 7. How the Lead Qualification stamp works (the one fragile part)

`custom_lead_score_updated_on` is written by Server Scripts managed from `splinh_setup.py`:

- **"Lead Profile Percent Score"** (DocType Event, Before Save): runs on every save. If the Lead already existed and the recomputed `custom_profile_percent` differs from the stored one, it stamps. `custom_profile_percent` is a **Data (text)** field, so the old value is cast with `cint()` first; without that cast every save looked like a change (a bug caught and fixed before going live).
- **"Lead Export Profile Score Backfill"** (Scheduler, hourly): the export score's Before-Save script deliberately only runs on creation (to protect an early-September historical snapshot), so ongoing recalculation, and its half of the stamp, happens in this hourly job.
- **"Lead Export Profile Score"** and **"Lead Profile Percent Backfill"** do not carry stamp code, by design.

Because these are Server Scripts, **Lead Qualification only works where Server Scripts are enabled** and where the two score fields exist.

---

## 8. Before deploying to production, verify (all unchecked on production)

1. **Server Scripts enabled?** Earlier project notes say Server Scripts were disabled site-wide on production; later notes show Lead scripts exist there. Check `frappe.utils.safe_exec.is_safe_exec_enabled()`. Enabling it is a bench-wide decision, not something to switch on silently.
2. **Do the Lead score fields exist?** `Lead-custom_export_profile_percent` and `Lead-custom_profile_percent` as Custom Fields. `ensure_lead_export_profile()` and `ensure_lead_profile_percent()` return immediately if they are missing, so no scripts and no stamp field would be created.
3. **Existing Lead-score scripts on production under other names?** The duplicate-guard handoff mentions "two profile-score scripts" on production. If they differ from the four names above, both sets would run on every Lead save and could overwrite each other. Compare names and contents first.
4. **Insights app (v3) installed**, with the "Site DB" data source and the tables `tabLead`, `tabCall Log`, `tabStart-End Meeting`, `tabSales KPI Entry` synced. If not, the workbook step logs an error and skips; the rest still works.
5. **SFA `Start-End Meeting` doctype** (from `sigzensfa`) with `id_type`, `start_meeting`, `end_meeting`. Without it the visit numbers and two cards/charts cannot work.
6. **Call Log fields** `custom_lead` and `custom_source`: created by `splinh_setup` (`ensure_call_tracking`), so present once splinh has migrated.
7. **Roles exist:** Sales User, SFA Sales User, Sales Manager. **Workspace Sidebar `CRM`** exists (the shortcuts are skipped if not).
8. **Module visibility:** the workspace now belongs to the **SplINH** module. `ensure_kpi_dashboard()` removes "SplINH" from the Block Modules list of every user holding Sales User, SFA Sales User or Sales Manager. This is a no-op where it is not blocked, but it can reveal other SplINH workspaces to those users (still gated by roles). On UAT users had the old module "Splashjet" blocked, which is why this exists. **This is a permission widening: confirm it is acceptable on production.**
9. **User-save crash:** on UAT, saving *any* User crashed because `splashjetsfa` imports a renamed Frappe function (`_add`). The module-unblocking code works around it with a raw delete plus cache clear instead of `User.save()`. Check whether production's SFA apps have the same bug; do not assume either way.
10. **Add a column to `tabLead` with idle transactions open?** The migrate adds a Custom Field to Lead (an `ALTER TABLE` on a ~300k-row table). Check for open consoles / idle transactions first, and run it off-peak.

---

## 9. Not built (waiting on decisions)

- **Sales funnel / cost-centre group** (Opportunity generated, Quoted, Sample request, New sample, New sales and value, Repeat/Secondary order number and value): blocked on cost-centre and assignment policy.
- **Incoming Enquiry**, **Incoming Calls**, **Assigned Leads/Opportunities by someone**, **Response time** (logic to be provided by Gaurav sir).
- **Call-duration buckets** (Not connected, 0–90 s, 90–300 s, 300+ s for Cold and Followup): shown in the sheet as "only display on dashboard"; not built.
- **Google Meet and WhatsApp chat recording / transcript, AI-generated MOM for manual items:** only the manual fields exist.
- **Retailer** visits: no records and no dropdown option; not handled.

---

## 10. Known behaviours worth knowing

- A day with only calls and no visits still gets a row on the detail page (the missing side shows 0). A day with no Web Form entry shows manual columns as 0 ("not filled in", not an error).
- Insights gotchas already fixed in the workbook code: a Decimal-vs-text column needs `try_cast('float64')`, and `date_diff()` silently drops the time of day (use `time_diff()` for meeting minutes).
- Cards and charts filter to the **current week**, recomputed at every migrate, not live. Between migrates the bound is the Monday of the week of the last migrate.
- The Sales User permission is `if_owner`: a salesperson sees only their own entries. Managers see everyone's through the Workspace, the detail page and the Insights workbook.

---

## 11. Rollout plan for production (suggested)

1. Complete section 8 (read-only checks) and decide items 1, 3 and 8.
2. Back up the database. Choose a quiet window.
3. Pull `splinh` (it already contains the KPI files), then `bench --site erp.splashjetink.com migrate`. Check Error Log for `splinh_setup:` failures.
4. Restart web and workers and clear cache, then confirm the live processes started after the files changed (`bench … verify-live-code <files>`).
5. **Verify numbers against hand-run SQL for one user and one day** (sections 3 and 7 give the exact filters). On UAT the five call/visit counters and the full dashboard payload matched the previous implementation exactly.
6. Check as three users: a Sales User (can open the Web Form, sees only their own entries), a Sales Manager (sees the Workspace and `/app/kpi-dashboard-detail`), and a user with neither (must not reach the detail data).
7. Pilot with two salespeople before announcing.

**Rollback:** unpublish the Web Form, delete or hide the Workspace, and disable the four stamp changes by restoring the previous Lead-score Server Script bodies. The Sales KPI Entry doctype and its data can stay; nothing else depends on it.

---

## 12. UAT-only leftovers

The vendor's `splashjet` app on UAT still contains the **old copy** of all of this (untracked files plus about 500 added lines in its setup file), and both apps run their setup on every migrate. The `splinh` copy runs last and wins, so the result is correct, but the old copy should be removed. That removal is pending an explicit decision, because it is vendor-owned code. **This does not apply to production**, where `splashjet` is unrelated.
