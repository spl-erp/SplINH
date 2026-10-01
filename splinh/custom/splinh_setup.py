"""
Local SplashJet customisations that must survive a database restore.

Background: UAT is periodically refreshed from a production backup, which wipes
everything that lives only as database records (Custom Fields, Server/Client
Scripts, Kanban Boards, Workspaces, Property Setters...). Anything configured
by hand therefore has to be rebuilt by hand, every single time.

Everything in this module is idempotent and is re-applied by the `after_migrate`
hook, so a restore followed by `bench migrate` puts it all back automatically.

Each section is individually guarded: a failure is logged but never allowed to
break a migration.

To re-apply manually:
    bench --site <site> execute splinh.custom.splinh_setup.execute
"""

import json

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from splinh.custom.call_monitoring_insights import ensure_call_monitoring_insights

# Plain fields on the Lead itself. 90 points here + 10 for the Contact's
# designation (below) = 100. Mirrored in the Client Script - keep both in step.
LEAD_SCORE_POINTS = {
	"company_name": 5,
	"first_name": 15,
	"mobile_no": 15,
	"whatsapp_no": 10,
	"custom_work_email__owner": 15,
	"custom_marketing_keywords": 5,
	"country": 5,
	"website": 5,
	"custom_customer_type": 5,
	"custom_printer_model_info": 5,
	"custom_business_vertical": 5,
}

# The last 10 points are NOT a field on Lead. The job title lives on the linked
# Contact, as `Contact.designation`, reached through Frappe's Dynamic Link child
# table (Contact.links -> link_doctype "Lead"). Lead has no Link field to Contact.
# Lead.job_title was used before and was wrong: it is a separate, differently
# populated field (3,232 leads vs 3,621 with a Contact designation).
LEAD_SCORE_DESIGNATION_POINTS = 10

# The Contact is created moments AFTER the Lead, so the designation can never be
# read during the Lead's own Before Save. Those 10 points are therefore awarded by
# the hourly backfill below, not at creation.
#
# NB: there is deliberately no historical cutoff here. One existed while the
# formula was still job_title-based, protecting a ~27k-lead early-Sept snapshot
# from being overwritten. Once the formula itself changed (job_title -> Contact
# designation, custom_segment -> custom_business_vertical), keeping that cutoff
# would have left the field meaning two different things depending on a record's
# age - so on 2026-09-22 every Lead was rescored once, by hand, against the
# current formula, and the pre-rescore values were saved to
# lead_score_snapshot_2026-09-22.csv at the bench root for reversibility.

WORK_STATUS_PROJECT = "Work Status"
WORK_STATUS_BOARD = "Work Status"
# NB: the domain is splashjet-ink.com (hyphenated) for these accounts. An earlier
# non-hyphenated address silently matched nothing and quietly dropped a team member.
# Seed only. Used ONCE, to populate the Work Status project's Users table the first
# time that project is created. After that the project's Users table is the source of
# truth for who the team is, so people can be added or removed in the UI
# (Projects > Work Status > Users) without anyone editing this file.
WORK_STATUS_TEAM_SEED = ["erp@splashjet-ink.com", "mis@splashjet-ink.com", "lms.content@splashjet-ink.com"]

TASK_STATUS_COLORS = {
	"Open": "Orange",
	"Working": "Blue",
	"Pending Review": "Purple",
	"Overdue": "Red",
	"Completed": "Green",
	"Cancelled": "Gray",
}


def execute():
	"""Entry point - called from the `after_migrate` hook and safe to re-run."""
	for step in (
		ensure_customer_code_naming,
		ensure_customer_code_field_schema,
		ensure_lead_export_profile,
		ensure_lead_profile_percent,
		ensure_work_status_tool,
		ensure_issue_tracker,
		ensure_call_tracking,
		_ensure_call_recording_player,
		_ensure_call_manager_review_fields,
		ensure_call_log_phone_matching,
		ensure_call_monitoring_dashboard,
		ensure_call_monitoring_insights,
		ensure_call_monitoring_detailed_page,
		ensure_user_change_notifications,
	):
		try:
			step()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"splinh_setup: {step.__name__} failed",
				message=frappe.get_traceback(),
			)


# ---------------------------------------------------------------- Customer Code


def ensure_customer_code_naming():
	"""Typed Customer Code becomes the customer ID; blank falls back to the C-series.

	Three things are required together - miss any one and it silently breaks:
	  1. the Document Naming Rule only fires when the code is genuinely blank
	  2. the doctype's Auto Name reads the field
	  3. Selling Settings must be "Auto Name", or ERPNext's own Customer.autoname()
	     short-circuits and never reaches (2).
	"""
	_set_property("Customer", None, "autoname", "field:custom_customer_code", "Data", for_doctype=True)
	_set_property("Customer", None, "naming_rule", "By fieldname", "Data", for_doctype=True)

	if frappe.db.get_single_value("Selling Settings", "cust_master_name") != "Auto Name":
		settings = frappe.get_single("Selling Settings")
		settings.cust_master_name = "Auto Name"
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)

	rule_name = frappe.db.get_value("Document Naming Rule", {"document_type": "Customer"}, "name")
	if not rule_name:
		return

	rule = frappe.get_doc("Document Naming Rule", rule_name)
	blank_condition = None
	for row in rule.conditions:
		if row.field == "custom_customer_code":
			blank_condition = row
	if not blank_condition:
		blank_condition = rule.append("conditions", {"field": "custom_customer_code"})
	if blank_condition.condition == "=" and blank_condition.value == "" and not rule.disabled:
		return

	blank_condition.condition = "="
	# A genuinely empty string. The Desk UI refuses this (the field is mandatory),
	# so it can only be set with ignore_mandatory - hence this code existing at all.
	blank_condition.value = ""
	rule.disabled = 0
	rule.flags.ignore_mandatory = True
	rule.save(ignore_permissions=True)



def ensure_customer_code_field_schema():
	"""Customer.custom_customer_code's schema correction (not reqd, unique,
	max 8 chars) - re-expressed as our own Property Setter so this no longer
	depends on Sigzen's custom_field_admin.py fixture (where the field is
	originally defined) or a patches.txt-triggered rerun of Sigzen's own
	patch module. A Property Setter at doctype_or_field="DocField" overrides
	the base field definition regardless of which app defined it.
	"""
	_set_property("Customer", "custom_customer_code", "reqd", "0", "Check")
	_set_property("Customer", "custom_customer_code", "unique", "1", "Check")
	_set_property("Customer", "custom_customer_code", "length", "8", "Int")

# ------------------------------------------------------------- Lead score


def ensure_lead_export_profile():
	"""Score a Lead's data completeness - but only for NEW leads.

	Historical leads carry scores from a one-off snapshot taken in early Sept.
	Re-scoring them on edit would silently rewrite ~27k real records, so the
	script deliberately skips anything that already exists in the database.
	"""
	if not frappe.db.exists("Custom Field", "Lead-custom_export_profile_percent"):
		return

	points_literal = json.dumps(LEAD_SCORE_POINTS, indent=4)
	server_script = f'''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
# Only scores leads that do not yet exist in the database, so the historical
# early-Sept snapshot on existing leads is never rewritten.
if not doc.name or not frappe.db.exists("Lead", doc.name):
    POINTS = {points_literal}
    percent = 0
    for fieldname, value in POINTS.items():
        if doc.get(fieldname):
            percent += value

    # The Contact's designation is worth {LEAD_SCORE_DESIGNATION_POINTS}. It is read through the Dynamic Link
    # child table because Lead has no Link field to Contact. At creation the
    # Contact does not exist yet, so this scores 0 here and the hourly backfill
    # ("Lead Export Profile Score Backfill") adds the points once it does.
    if doc.name:
        links = frappe.get_all("Dynamic Link",
            filters={{"link_doctype": "Lead", "link_name": doc.name, "parenttype": "Contact"}},
            fields=["parent"], limit=1)
        if links:
            if frappe.db.get_value("Contact", links[0]["parent"], "designation"):
                percent += {LEAD_SCORE_DESIGNATION_POINTS}

    if percent >= 90:
        percent_range = "90% +"
    elif percent >= 75:
        percent_range = "75% - 90%"
    elif percent >= 50:
        percent_range = "50% - 75%"
    elif percent >= 25:
        percent_range = "25% - 50%"
    else:
        percent_range = "0% - 25%"
    doc.custom_export_profile_percent = percent
    doc.custom_export_profile_percent_range = percent_range
'''

	_upsert_server_script(
		"Lead Export Profile Score",
		{
			"script_type": "DocType Event",
			"reference_doctype": "Lead",
			"doctype_event": "Before Save",
			"script": server_script,
			"disabled": 0,
		},
	)

	# Hourly backfill. Two jobs: award the designation points once the Contact
	# exists, and score leads created while the script was missing (the production
	# restore wiped it, and 80 leads were created in that window scoring 0).
	#
	# Deliberately loads each Lead with get_doc and re-runs the SAME arithmetic as
	# above rather than a separate SQL version - two implementations of one formula
	# drift apart silently. get_doc also resolves custom_business_vertical, which is
	# a Table MultiSelect and cannot be read with frappe.get_all at all.
	backfill_script = f'''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
POINTS = {points_literal}
BATCH = 500
WINDOW_HOURS = 2

# Every Lead is in scope. There was a cutoff here that protected an early-Sept
# snapshot on ~30k historical leads; it was dropped once the formula itself changed
# (job_title -> Contact designation, custom_segment -> custom_business_vertical),
# because keeping it would have left the field meaning two different things
# depending on how old the record was. The historical rows were backfilled once,
# by hand, against a saved CSV snapshot.
#
# Candidates are leads touched in the last WINDOW_HOURS hours, from either side of
# the relationship:
#   1. the Lead itself was edited;
#   2. the Lead's linked CONTACT was edited - adding a designation to a Contact
#      does not touch the Lead row at all, so without this the last 10 points
#      would never land for a Contact filled in later.
# Deliberately NOT "leads whose score is 0": once everything is scored, a lead that
# legitimately scores 0 would match that filter forever and be reloaded every hour.
since = frappe.utils.add_to_date(None, hours=-WINDOW_HOURS)
candidates = []
for row in frappe.get_all("Lead", filters={{"modified": [">", since]}},
        fields=["name"], limit_page_length=BATCH, order_by="modified asc"):
    candidates.append(row["name"])

recent_contacts = []
for row in frappe.get_all("Contact", filters={{"modified": [">", since]}},
        fields=["name"], limit_page_length=BATCH, order_by="modified asc"):
    recent_contacts.append(row["name"])
if recent_contacts:
    for row in frappe.get_all("Dynamic Link",
            filters={{"link_doctype": "Lead", "parenttype": "Contact", "parent": ["in", recent_contacts]}},
            fields=["link_name"], limit_page_length=0):
        if row["link_name"] and row["link_name"] not in candidates:
            candidates.append(row["link_name"])

updated = 0
for name in candidates:
    lead = frappe.get_doc("Lead", name)
    percent = 0
    for fieldname, value in POINTS.items():
        if lead.get(fieldname):
            percent += value

    links = frappe.get_all("Dynamic Link",
        filters={{"link_doctype": "Lead", "link_name": name, "parenttype": "Contact"}},
        fields=["parent"], limit=1)
    if links:
        if frappe.db.get_value("Contact", links[0]["parent"], "designation"):
            percent += {LEAD_SCORE_DESIGNATION_POINTS}

    if percent >= 90:
        percent_range = "90% +"
    elif percent >= 75:
        percent_range = "75% - 90%"
    elif percent >= 50:
        percent_range = "50% - 75%"
    elif percent >= 25:
        percent_range = "25% - 50%"
    else:
        percent_range = "0% - 25%"

    # Write only on a real change, and without touching `modified` - otherwise every
    # run would bump 500 leads up the "recently modified" list and re-select them.
    if lead.custom_export_profile_percent != percent or lead.custom_export_profile_percent_range != percent_range:
        frappe.db.set_value("Lead", name, {{
            "custom_export_profile_percent": percent,
            "custom_export_profile_percent_range": percent_range,
        }}, update_modified=False)
        updated += 1
'''

	_upsert_server_script(
		"Lead Export Profile Score Backfill",
		{
			"script_type": "Scheduler Event",
			"event_frequency": "Hourly",
			"script": backfill_script,
			"disabled": 0,
		},
	)

	client_script = f'''// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
// Live preview while creating a Lead. Gated on new documents so existing leads
// keep their historical score.
//
// The preview is a LOWER BOUND: the last {LEAD_SCORE_DESIGNATION_POINTS} points come from the linked
// Contact's designation, and on an unsaved Lead no Contact exists yet. So a fully
// completed new Lead previews at 90 and settles at 100 within the hour, once the
// Contact is created and the hourly backfill picks it up.
const EXPORT_POINTS = {json.dumps(LEAD_SCORE_POINTS, indent=2)};

function ws_range(percent) {{
  if (percent >= 90) return "90% +";
  if (percent >= 75) return "75% - 90%";
  if (percent >= 50) return "50% - 75%";
  if (percent >= 25) return "25% - 50%";
  return "0% - 25%";
}}

function ws_score(frm) {{
  if (!frm.is_new()) return;
  let percent = 0;
  for (const fieldname in EXPORT_POINTS) {{
    if (frm.doc[fieldname] && frm.doc[fieldname].length) percent += EXPORT_POINTS[fieldname];
  }}
  frm.set_value("custom_export_profile_percent", percent);
  frm.set_value("custom_export_profile_percent_range", ws_range(percent));
}}

frappe.ui.form.on("Lead", {{
{chr(10).join(f"  {f}: ws_score," for f in LEAD_SCORE_POINTS)}
}});
'''

	_upsert(
		"Client Script",
		"Lead Export Profile Score Preview",
		{"dt": "Lead", "view": "Form", "enabled": 1, "script": client_script},
	)


# ---------------------------------------------------- Lead profile percent (domestic)

# Sibling to LEAD_SCORE_POINTS above. That formula (custom_export_profile_percent) is
# a different, independently-agreed weighting for export leads; this one is a second,
# separate field (custom_profile_percent - already existed on Lead, unpopulated) that
# mirrors a legacy vtiger CRM query for DOMESTIC (non-export) accounts:
#
#   UPDATE vtiger_account/vtiger_accountscf/vtiger_accountaddress SET profile_percent =
#     LEAST(SUM(weighted, filled-field flags), 100) WHERE region <> 'Export'
#
# Mapped field-by-field against the vtiger query, in the SAME order it lists them, with
# the user (2026-09-23): confirmed live and awaiting sign-off ("with sir") before this
# is treated as final. Two rows are NOT plain filled-field checks and are handled
# separately below:
#   - vtiger `phone` (3 pts)             -> mobile_no OR whatsapp_no (either counts)
#   - vtiger `latitude_longitude` (5 pts) -> custom_latitude AND custom_longitude (both required)
#   - vtiger `contact_name` (5 pts)      -> the LINKED CONTACT's name, read through Dynamic
#     Link exactly like the designation lookup in ensure_lead_export_profile - so it is
#     also only awardable once the Contact exists, hence its own hourly backfill below.
# One row is unmapped and deliberately NOT scored: vtiger `addresslevel8a` (2 pts) has no
# known equivalent field on Lead. Max achievable is therefore 98, not 100, until that is
# resolved. `lead_name` (row 1) is confirmed READ-ONLY and mirrors `first_name` - verified
# against live data - so scoring it is equivalent to scoring first_name a second time, not
# an independent signal of a filled company/account name as the vtiger column implied.
# `email2` (row 22) had no confirmed field; `custom_work_email__owner` is a best guess
# pending confirmation, kept separate from `email_id` (used for `email1`, row 11).
LEAD_PROFILE_POINTS = {
	"lead_name": 2,  # row 1 (accountname) - see caveat above, mirrors first_name
	"custom_city": 2,  # row 3 (addresslevel5a) - guess, unconfirmed
	"territory": 5,  # row 4
	"custom_state": 5,  # row 5 (addresslevel2a) - guess, unconfirmed
	"custom_address": 5,  # row 6 (addresslevel7a) - guess, unconfirmed
	"country": 1,  # row 7 (addresslevel1a) - guess, unconfirmed
	"custom_geotag": 5,  # row 8
	"email_id": 5,  # row 11 (email1)
	"custom_keyword": 1,  # row 12
	"industry": 5,  # row 13 (profiling_industry) - unconfirmed
	"custom_segment": 5,  # row 14
	"custom_product_sold_by_customer": 5,  # row 15
	"custom_customer_type": 5,  # row 16 (business_type) - no direct equivalent, best guess
	"custom_profile_photo_owner": 3,  # row 18 (photos)
	"custom_linkedin_profile": 3,  # row 19
	"custom_facebook_profile": 4,  # row 20
	"custom_insta_profile": 4,  # row 21
	"custom_work_email__owner": 5,  # row 22 (email2) - best guess, unconfirmed
	"mobile_no": 5,  # row 23
	"whatsapp_no": 10,  # row 24
}
LEAD_PROFILE_PHONE_POINTS = 3  # row 9 (phone): mobile_no OR whatsapp_no
LEAD_PROFILE_LOCATION_POINTS = 5  # row 10 (latitude_longitude): both fields required
LEAD_PROFILE_CONTACT_NAME_POINTS = 5  # row 17 (contact_name): via linked Contact, backfilled


def ensure_lead_profile_percent():
	"""Domestic-lead profile-completeness score, mirroring a legacy vtiger CRM formula.

	See the module-level comment above LEAD_PROFILE_POINTS for the full field mapping,
	its caveats, and what is still pending sign-off.
	"""
	if not frappe.db.exists("Custom Field", "Lead-custom_profile_percent"):
		return

	points_literal = json.dumps(LEAD_PROFILE_POINTS, indent=4)
	server_script = f'''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
# Domestic-lead profile score. See LEAD_PROFILE_POINTS in splinh_setup.py for the full
# vtiger-to-Lead field mapping and its unconfirmed rows.
POINTS = {points_literal}
percent = 0
for fieldname, value in POINTS.items():
    if doc.get(fieldname):
        percent += value

# row 9: phone -> either mobile or WhatsApp number counts
if doc.get("mobile_no") or doc.get("whatsapp_no"):
    percent += {LEAD_PROFILE_PHONE_POINTS}

# row 10: latitude_longitude -> both halves required, a lone coordinate is not a location
if doc.get("custom_latitude") and doc.get("custom_longitude"):
    percent += {LEAD_PROFILE_LOCATION_POINTS}

# row 17: contact_name, via the linked Contact (Dynamic Link - Lead has no direct Link to
# Contact). At creation the Contact does not exist yet, so this scores 0 here and the
# hourly "Lead Profile Percent Backfill" adds the points once it does.
if doc.name and frappe.db.exists("Lead", doc.name):
    links = frappe.get_all("Dynamic Link",
        filters={{"link_doctype": "Lead", "link_name": doc.name, "parenttype": "Contact"}},
        fields=["parent"], limit=1)
    if links:
        if frappe.db.get_value("Contact", links[0]["parent"], "full_name"):
            percent += {LEAD_PROFILE_CONTACT_NAME_POINTS}

doc.custom_profile_percent = percent
'''

	_upsert_server_script(
		"Lead Profile Percent Score",
		{
			"script_type": "DocType Event",
			"reference_doctype": "Lead",
			"doctype_event": "Before Save",
			"script": server_script,
			"disabled": 0,
		},
	)

	# Hourly backfill for the contact-name points only - the rest are already correct at
	# save time. Deliberately a separate script from "Lead Export Profile Score Backfill"
	# even though both look up the same Dynamic Link: this formula is independently
	# unconfirmed pending sign-off, and keeping it apart means a change to one score's
	# backfill can never silently affect the other's.
	backfill_script = f'''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
POINTS = {points_literal}
BATCH = 500
WINDOW_HOURS = 2

since = frappe.utils.add_to_date(None, hours=-WINDOW_HOURS)
candidates = []
for row in frappe.get_all("Lead", filters={{"modified": [">", since]}},
        fields=["name"], limit_page_length=BATCH, order_by="modified asc"):
    candidates.append(row["name"])

recent_contacts = []
for row in frappe.get_all("Contact", filters={{"modified": [">", since]}},
        fields=["name"], limit_page_length=BATCH, order_by="modified asc"):
    recent_contacts.append(row["name"])
if recent_contacts:
    for row in frappe.get_all("Dynamic Link",
            filters={{"link_doctype": "Lead", "parenttype": "Contact", "parent": ["in", recent_contacts]}},
            fields=["link_name"], limit_page_length=0):
        if row["link_name"] and row["link_name"] not in candidates:
            candidates.append(row["link_name"])

updated = 0
for name in candidates:
    lead = frappe.get_doc("Lead", name)
    percent = 0
    for fieldname, value in POINTS.items():
        if lead.get(fieldname):
            percent += value
    if lead.get("mobile_no") or lead.get("whatsapp_no"):
        percent += {LEAD_PROFILE_PHONE_POINTS}
    if lead.get("custom_latitude") and lead.get("custom_longitude"):
        percent += {LEAD_PROFILE_LOCATION_POINTS}

    links = frappe.get_all("Dynamic Link",
        filters={{"link_doctype": "Lead", "link_name": name, "parenttype": "Contact"}},
        fields=["parent"], limit=1)
    if links:
        if frappe.db.get_value("Contact", links[0]["parent"], "full_name"):
            percent += {LEAD_PROFILE_CONTACT_NAME_POINTS}

    if lead.custom_profile_percent != percent:
        frappe.db.set_value("Lead", name, "custom_profile_percent", percent, update_modified=False)
        updated += 1
'''

	_upsert_server_script(
		"Lead Profile Percent Backfill",
		{
			"script_type": "Scheduler Event",
			"event_frequency": "Hourly",
			"script": backfill_script,
			"disabled": 0,
		},
	)

	client_script = f'''// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
// Live preview while creating a Lead. The contact-name points cannot show here (the
// Contact does not exist until after the Lead is saved), so this is a LOWER BOUND -
// it settles up to {LEAD_PROFILE_CONTACT_NAME_POINTS} points higher within the hour.
const PROFILE_POINTS = {json.dumps(LEAD_PROFILE_POINTS, indent=2)};

function wsp_score(frm) {{
  if (!frm.is_new()) return;
  let percent = 0;
  for (const fieldname in PROFILE_POINTS) {{
    if (frm.doc[fieldname] && frm.doc[fieldname].length) percent += PROFILE_POINTS[fieldname];
  }}
  if (frm.doc.mobile_no || frm.doc.whatsapp_no) percent += {LEAD_PROFILE_PHONE_POINTS};
  if (frm.doc.custom_latitude && frm.doc.custom_longitude) percent += {LEAD_PROFILE_LOCATION_POINTS};
  frm.set_value("custom_profile_percent", percent);
}}

frappe.ui.form.on("Lead", {{
{chr(10).join(f"  {f}: wsp_score," for f in LEAD_PROFILE_POINTS)}
  mobile_no: wsp_score,
  whatsapp_no: wsp_score,
  custom_latitude: wsp_score,
  custom_longitude: wsp_score,
}});
'''

	_upsert(
		"Client Script",
		"Lead Profile Percent Preview",
		{"dt": "Lead", "view": "Form", "enabled": 1, "script": client_script},
	)


# -------------------------------------------------------- Work Status tool


def ensure_work_status_tool():
	"""Daily work tracking for the internal team: one project, a Kanban board and a dashboard."""
	_ensure_task_assigned_to_field()
	_ensure_work_status_project()
	_ensure_work_status_board()
	_ensure_assignment_sync_script()
	_ensure_work_status_dashboard()
	_ensure_team_roles()
	_ensure_team_onboarding_script()
	_unblock_modules(get_work_status_team(), ["Projects"])
	_ensure_sidebar_entry()


def _ensure_task_assigned_to_field():
	if not frappe.db.exists("Custom Field", "Task-custom_assigned_to"):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Task",
				"fieldname": "custom_assigned_to",
				"label": "Assigned To",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "status",
				"allow_in_quick_entry": 1,
				"in_list_view": 1,
				"in_standard_filter": 1,
				"is_system_generated": 0,
			}
		).insert(ignore_permissions=True)

	# Make daily logging fast, and keep "Is Template" out of Quick Entry - ticking it
	# forces status to Template, which hides the task from the board entirely.
	for fieldname, prop, value in (
		("status", "allow_in_quick_entry", "1"),
		("priority", "allow_in_quick_entry", "1"),
		("exp_end_date", "allow_in_quick_entry", "1"),
		("exp_end_date", "in_list_view", "1"),
		("description", "allow_in_quick_entry", "1"),
		("is_template", "allow_in_quick_entry", "0"),
	):
		_set_property("Task", fieldname, prop, value, "Check")


def get_work_status_team():
	"""Current team members, read from the Work Status project's Users table.

	Everything that follows from being on the team - the Projects User role, having
	the Projects and Support modules unblocked, membership of the placeholder
	providers - is derived from this one list, so adding a row in the UI is all that
	is needed to onboard someone. Falls back to the seed only while the project does
	not exist yet.
	"""
	project = frappe.db.get_value("Project", {"project_name": WORK_STATUS_PROJECT}, "name")
	members = (
		frappe.get_all("Project User", filters={"parent": project}, pluck="user")
		if project
		else WORK_STATUS_TEAM_SEED
	)
	return [u for u in dict.fromkeys(members) if u and frappe.db.exists("User", u)]


def _ensure_work_status_project():
	if frappe.db.exists("Project", {"project_name": WORK_STATUS_PROJECT}):
		return
	company = frappe.db.get_value("Company", {}, "name")
	if not company:
		return
	project = frappe.new_doc("Project")
	project.project_name = WORK_STATUS_PROJECT
	project.status = "Open"
	project.is_active = "Yes"
	project.company = company
	project.notes = "Daily work tracking. One Task per work item; move it across the board as it progresses."
	for user in WORK_STATUS_TEAM_SEED:
		if not frappe.db.exists("User", user):
			frappe.log_error(
				title="splinh_setup: seed team user not found",
				message=f"{user} is listed in WORK_STATUS_TEAM_SEED but has no User record.",
			)
			continue
		# welcome_email_sent pre-set: ERPNext would otherwise try to email every
		# project member, and outgoing mail is not configured on this site.
		project.append("users", {"user": user, "welcome_email_sent": 1, "view_attachments": 1})
	project.insert(ignore_permissions=True)


def _ensure_work_status_board():
	project = frappe.db.get_value("Project", {"project_name": WORK_STATUS_PROJECT}, "name")
	if not project:
		return

	if not frappe.db.exists("Kanban Board", WORK_STATUS_BOARD):
		from frappe.desk.doctype.kanban_board.kanban_board import quick_kanban_board

		quick_kanban_board("Task", WORK_STATUS_BOARD, "status", project=project)

	board = frappe.get_doc("Kanban Board", WORK_STATUS_BOARD)
	board.filters = json.dumps([["Task", "project", "=", project]])
	board.private = 0
	board.show_labels = 1
	for column in board.columns:
		# "Template" is driven by the is_template flag, never by real work - hide it.
		column.status = "Archived" if column.column_name == "Template" else "Active"
		if column.column_name in TASK_STATUS_COLORS:
			column.indicator = TASK_STATUS_COLORS[column.column_name]
	board.save(ignore_permissions=True)


def _ensure_assignment_sync_script():
	script = '''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
# Mirrors the "Assigned To" field into ERPNext's native assignment so the team
# also gets ToDos and notifications. Only acts on an actual change, and only
# removes the assignment it previously created - manual collaborators are left alone.
new_user = doc.custom_assigned_to
before = doc.get_doc_before_save()
old_user = None
if before:
    old_user = before.custom_assigned_to

if old_user != new_user:
    if old_user:
        old_todos = frappe.get_all("ToDo", filters={
            "reference_type": "Task", "reference_name": doc.name,
            "allocated_to": old_user, "status": "Open",
        }, limit=1)
        if old_todos:
            frappe.call("frappe.desk.form.assign_to.remove",
                        doctype="Task", name=doc.name, assign_to=old_user)
    if new_user:
        existing = frappe.get_all("ToDo", filters={
            "reference_type": "Task", "reference_name": doc.name,
            "allocated_to": new_user, "status": "Open",
        }, limit=1)
        if not existing:
            frappe.call("frappe.desk.form.assign_to.add",
                        doctype="Task", name=doc.name,
                        assign_to=[new_user], description=doc.subject)
'''
	_upsert_server_script(
		"Task Assigned To Sync",
		{
			"script_type": "DocType Event",
			"reference_doctype": "Task",
			"doctype_event": "After Save",
			"script": script,
			"disabled": 0,
		},
	)


def _ensure_team_onboarding_script():
	"""Apply team membership the moment someone is added in the UI.

	get_work_status_team() reads the project's Users table, but the role grant and
	the module unblock that follow from it would otherwise only land on the next
	migrate. This closes that gap, so adding a row and saving the project really is
	all that is needed - no code edit, no waiting, no second trip to the User form.

	Tightly guarded: it returns immediately for every project except Work Status,
	and it only ever adds. It never removes a role or re-blocks a module, so taking
	someone off the project does not strip access they may need elsewhere; that
	stays a deliberate manual act.
	"""
	script = '''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
# Gives a newly added Work Status member what they need to actually see the tool:
# the Projects User role, and the Projects/Support modules off their block list.
# Without the unblock they would see nothing at all, whatever roles they hold.
if doc.project_name == "Work Status":
    for row in doc.users:
        if not row.user:
            continue
        user_doc = frappe.get_doc("User", row.user)
        changed = False
        if not frappe.db.exists("Has Role", {"parent": row.user, "role": "Projects User"}):
            user_doc.append("roles", {"role": "Projects User"})
            changed = True
        keep = []
        for blocked in user_doc.block_modules:
            if blocked.module in ("Projects", "Support"):
                changed = True
            else:
                keep.append(blocked)
        user_doc.block_modules = keep
        if changed:
            # No explicit cache clear: User.on_update() already calls
            # frappe.clear_cache(user=...), and clear_cache is not exposed to
            # Server Scripts anyway. That invalidation matters - a stale cached
            # User doc keeps reporting the old block list and the fix looks inert.
            user_doc.flags.ignore_permissions = True
            user_doc.save()
'''
	_upsert_server_script(
		"Work Status Team Onboarding",
		{
			"script_type": "DocType Event",
			"reference_doctype": "Project",
			"doctype_event": "After Save",
			"script": script,
			"disabled": 0,
		},
	)


def _ensure_work_status_dashboard():
	"""Number cards, charts and a workspace, all scoped to the Work Status project."""
	project = frappe.db.get_value("Project", {"project_name": WORK_STATUS_PROJECT}, "name")
	if not project:
		return

	project_filter = json.dumps([["Task", "project", "=", project]])
	card_names = []
	for status in ("Open", "Working", "Pending Review", "Overdue", "Completed"):
		label = f"Work Status - {status}"
		_upsert(
			"Number Card",
			label,
			{
				"label": label,
				"type": "Document Type",
				"document_type": "Task",
				"function": "Count",
				"filters_json": json.dumps(
					[["Task", "project", "=", project], ["Task", "status", "=", status]]
				),
				"is_public": 1,
				"show_percentage_stats": 0,
				"module": "Projects",
			},
		)
		card_names.append(label)

	chart_names = []
	for suffix, group_by, chart_type in (
		("Tasks by Status", "status", "Donut"),
		("Tasks by Assignee", "custom_assigned_to", "Bar"),
		("Tasks by Priority", "priority", "Donut"),
	):
		label = f"Work Status - {suffix}"
		chart = _upsert(
			"Dashboard Chart",
			label,
			{
				"chart_name": label,
				"chart_type": "Group By",
				"document_type": "Task",
				"group_by_type": "Count",
				"group_by_based_on": group_by,
				"type": chart_type,
				"filters_json": project_filter,
				"is_public": 1,
				"module": "Projects",
			},
		)
		# `currency` gets auto-populated and makes the widget render plain counts
		# as money ("Rs 2.00" for 2 tasks). Clear it.
		if chart.currency:
			frappe.db.set_value("Dashboard Chart", chart.name, "currency", None, update_modified=False)
		chart_names.append(label)

	workspace = _upsert(
		"Workspace",
		"Work Status",
		{
			"label": "Work Status",
			"title": "Work Status",
			"type": "Workspace",
			"public": 1,
			"module": "Projects",
			"icon": "project",
		},
	)
	workspace.number_cards = []
	workspace.charts = []
	workspace.shortcuts = []
	workspace.append(
		"shortcuts",
		{
			"type": "DocType", "link_to": "Task", "label": "Work Status Board",
			"doc_view": "Kanban", "kanban_board": WORK_STATUS_BOARD, "color": "Blue",
		},
	)
	workspace.append(
		"shortcuts",
		{
			"type": "DocType", "link_to": "Task", "label": "All Tasks",
			"doc_view": "List", "stats_filter": json.dumps({"project": project}), "color": "Grey",
		},
	)
	# The block's reference is matched against the child row's *label*, not the
	# card/chart name - they must be identical or the block renders blank.
	for name in card_names:
		workspace.append("number_cards", {"number_card_name": name, "label": name})
	for name in chart_names:
		workspace.append("charts", {"chart_name": name, "label": name})

	content = [
		{"id": "wshdr1", "type": "header", "data": {"text": '<span class="h4"><b>Team Work Status</b></span>', "col": 12}},
		{"id": "wssc1", "type": "shortcut", "data": {"shortcut_name": "Work Status Board", "col": 6}},
		{"id": "wssc2", "type": "shortcut", "data": {"shortcut_name": "All Tasks", "col": 6}},
		{"id": "wshdr2", "type": "header", "data": {"text": '<span class="h4"><b>At a glance</b></span>', "col": 12}},
	]
	for i, name in enumerate(card_names, start=1):
		content.append({"id": f"wsnc{i}", "type": "number_card", "data": {"number_card_name": name, "col": 4}})
	for i, name in enumerate(chart_names, start=1):
		content.append({"id": f"wsch{i}", "type": "chart", "data": {"chart_name": name, "col": 6}})
	workspace.content = json.dumps(content)
	workspace.save(ignore_permissions=True)


def _ensure_team_roles():
	"""Projects User is what actually grants access to Task - Projects Manager has no Task permission."""
	for user in get_work_status_team():
		if frappe.db.exists("Has Role", {"parent": user, "role": "Projects User"}):
			continue
		user_doc = frappe.get_doc("User", user)
		user_doc.add_roles("Projects User")


def _ensure_sidebar_entry():
	"""Surface the workspace in the Projects sidebar.

	This ERPNext build drives navigation from per-module `Workspace Sidebar`
	records rather than the Workspace tree, so a custom workspace is otherwise
	unreachable except by direct URL. The sidebar is a standard fixture, so this
	entry is re-added here after every migrate rather than edited once by hand.
	"""
	if not frappe.db.exists("Workspace Sidebar", "Projects"):
		return
	sidebar = frappe.get_doc("Workspace Sidebar", "Projects")
	for item in sidebar.items:
		if item.link_to == "Work Status" and item.link_type == "Workspace":
			return
	sidebar.append(
		"items",
		{"label": "Work Status", "type": "Link", "link_type": "Workspace", "link_to": "Work Status", "icon": "chart"},
	)
	rows = sidebar.items
	rows.insert(1, rows.pop())  # sit directly under "Home"
	for index, row in enumerate(rows, start=1):
		row.idx = index
	sidebar.save(ignore_permissions=True)


# ---------------------------------------------------------------- Issue tracker

# Placeholder providers/modules so the pipeline is demonstrable. Seeded ONLY when
# no providers exist yet, so real data entered later is never overwritten.
PLACEHOLDER_MARKER = "PLACEHOLDER"
PLACEHOLDER_PROVIDERS = ["SigZen", "TallyTact"]
PLACEHOLDER_MODULES = [
	("ERP Core", "SigZen"),
	("SFA Mobile App", "SigZen"),
	("Helpdesk", "SigZen"),
	("Tally Integration", "TallyTact"),
]

ISSUE_STATUSES = [
	"Open", "In Progress", "Replied", "With Provider",
	"Provider Responded", "On Hold", "Resolved", "Closed", "Cancelled",
]

# Layout-only custom fields: a section and a column to hold the three issue-tracker
# fields, instead of leaving them stacked at the bottom of the already-crowded
# right-hand column of the stock header section. Pure containers - no data.
ISSUE_LAYOUT_BREAKS = [
	{
		"fieldname": "custom_issue_routing_section",
		"label": "Affected Modules & Routing",
		"fieldtype": "Section Break",
		# Last field of the stock header section, so the new section opens directly
		# below it and above the stock "Details" section.
		"insert_after": "issue_split_from",
		"collapsible": 0,
	},
	{
		"fieldname": "custom_issue_routing_cb",
		"fieldtype": "Column Break",
		"insert_after": "custom_raised_from",
	},
]

# Properties re-asserted on every run. Keyed by fieldname; only differing values are
# written, so this is a genuine no-op once applied.
ISSUE_FIELD_LAYOUT = {
	# Left column - what the person reporting the issue fills in.
	"custom_modules": {
		"insert_after": "custom_issue_routing_section",
		"description": "Which parts of the system this is about. This is what decides "
		"which provider the issue goes to.",
		"allow_in_quick_entry": 1,
	},
	"custom_raised_from": {
		"insert_after": "custom_modules",
		# Anyone on this form is, by definition, on the Desk. The mobile intake and
		# IT-raised-on-behalf-of cases override it explicitly.
		"default": "Desk",
		"description": "How this reached us. Leave it on Desk if you are filling in this form.",
	},
	# Header section, immediately above the Customer / Lead pair it switches between.
	"custom_relates_to": {
		"insert_after": "subject",
		"default": "Customer",
		"description": "Is this about a customer or a lead? The picker below changes to match.",
	},
	# Right column - derived, and read by IT rather than written by the reporter.
	"custom_solution_provider": {
		"insert_after": "custom_issue_routing_cb",
		# Kept read-only: the "Issue Route To Provider" Server Script (Before Save)
		# sets it server-side, where read_only is not enforced. See the note in
		# _ensure_issue_form_layout().
		"read_only": 1,
		"description": "Set automatically from the modules selected - you do not need to "
		"fill this in. It stays blank if the modules belong to two different providers, "
		"which is the signal for IT to split the issue.",
	},
}


def ensure_issue_tracker():
	"""Org-wide reporting of ERP issues, triaged by IT, escalated to the owning provider."""
	_ensure_issue_fields()
	_ensure_issue_form_layout()
	_ensure_issue_party_switch()
	_ensure_issue_statuses()
	_ensure_reporter_permission()
	_ensure_issue_routing_script()
	_seed_providers_and_modules()
	# Same trap as the Work Status board: "Support" is in the block list of the very
	# people who triage, so without this the Issue workspace is invisible to them.
	_unblock_modules(get_work_status_team(), ["Support"])
	_ensure_issue_workflow()
	_ensure_issue_pipeline_page()
	_ensure_issue_pipeline_page_sidebar_entry()
	_ensure_issue_dashboard()


# ------------------------------------------------------ Issue workflow enforcement

# "IT team" in the signed-off flow maps to the `Support Team` role: it is already the
# only role with full read/write/create/delete on Issue (see the Custom DocPerm in
# _ensure_reporter_permission's sibling check above), and there is no separate
# "IT Team" role anywhere on this site. There is also no "Solution Provider User"
# role, so the two provider-side transitions ("vendor marks fixed" / "vendor hands
# back") are gated on Support Team too - a human on the IT side records the vendor's
# outcome on their behalf, same as every other IT-side transition.
ISSUE_WORKFLOW_NAME = "Issue Workflow"
ISSUE_IT_ROLE = "Support Team"
# Frappe Workflow's `allowed` field takes a Role, not an ownership condition, so
# "only the reporter" is expressed as the broadest role (`All`, which every user
# already holds and which already carries the if_owner grant on Issue) plus a
# `condition` expression evaluated against `doc`. `owner` - not `raised_by` - is what
# the existing `All` / if_owner Custom DocPerm already keys reporter access on, so
# using the same field here keeps "can this user act on it" and "can this user see
# it" answering the same question about the same field.
ISSUE_REPORTER_CONDITION = "doc.owner == frappe.session.user"

# One `Workflow State` per ISSUE_STATUSES value. `style` is a fixed 6-value Select
# (Primary/Info/Success/Warning/Danger/Inverse) - there is no literal "teal" or "gray"
# option, so the signed-off palette (gray/blue/amber/teal/green/red) is approximated
# as Inverse/Info/Warning/Primary/Success/Danger, one style value per palette colour.
ISSUE_STATE_STYLES = {
	"Open": "Inverse",  # gray - queue
	"In Progress": "Info",  # blue - IT working
	"Replied": "Info",  # blue - IT working (waiting on reporter)
	"With Provider": "Warning",  # amber
	"Provider Responded": "Warning",  # amber
	"On Hold": "Info",  # blue - IT-owned, paused. Not one of the 8 diagram states -
	# added so it is not an orphaned, unreachable value in ISSUE_STATUSES.
	"Resolved": "Primary",  # standing in for teal - no teal option exists
	"Closed": "Success",  # green
	"Cancelled": "Danger",  # red
}

# Deviation from the plan's literal "0 = open ... 1 = Closed, 2 = Cancelled": Issue is
# NOT a submittable doctype (`frappe.get_meta("Issue").is_submittable == 0`, confirmed
# live). Frappe's own `Workflow.validate_docstatus()` treats doc_status 0/1/2 as
# draft/submitted/cancelled and HARD-BLOCKS exactly the transitions this pipeline
# needs the moment a non-zero value is used: "Submitted cannot convert back to draft"
# rejected Closed(1) -> In Progress (Reopen), and "Cannot cancel before submitting"
# rejected every Open/In Progress/...(0) -> Cancelled(2) row - all 6 Cancel rows and
# the Reopen row, confirmed by hitting both errors while building this. Those
# doc_status semantics only exist to protect submit/cancel doctypes; Issue's own
# lifecycle is entirely carried by `status` (a plain Select), not `docstatus`. Every
# state therefore gets doc_status 0, which disables that guardrail entirely and lets
# `status` alone drive the pipeline - which is what actually happens on Issue today.
ISSUE_STATE_DOC_STATUS = {status: 0 for status in ISSUE_STATUSES}

# (state, action, next_state, allowed_role, condition). Frappe's Workflow Transition
# `state` field is a Link to Workflow State, not a wildcard/Select - confirmed on the
# live Workflow Transition meta - so "Cancel from any active state" is NOT one row;
# it is one row per cancellable source state (6 rows, below). On Hold's in/out pair
# is likewise not one of the signed-off 14: it is added here so the 9th status is
# reachable at all, gated to IT like every other internal-only move.
ISSUE_WORKFLOW_TRANSITIONS = [
	("Open", "Pick Up", "In Progress", ISSUE_IT_ROLE, None),
	("Open", "Hand Off to Provider", "With Provider", ISSUE_IT_ROLE, None),  # skip triage
	("In Progress", "Ask for Info", "Replied", ISSUE_IT_ROLE, None),
	("Replied", "Reply", "In Progress", "All", ISSUE_REPORTER_CONDITION),
	("In Progress", "Hand Off to Provider", "With Provider", ISSUE_IT_ROLE, None),
	("With Provider", "Vendor Fixed", "Provider Responded", ISSUE_IT_ROLE, None),
	("Provider Responded", "Send Back to Provider", "With Provider", ISSUE_IT_ROLE, None),  # fix failed
	("In Progress", "Resolve", "Resolved", ISSUE_IT_ROLE, None),
	("Provider Responded", "Verify Fix", "Resolved", ISSUE_IT_ROLE, None),
	("Resolved", "Not Fixed", "In Progress", "All", ISSUE_REPORTER_CONDITION),
	("Resolved", "Confirm Close", "Closed", "All", ISSUE_REPORTER_CONDITION),
	("Closed", "Reopen", "In Progress", "All", ISSUE_REPORTER_CONDITION),
	("In Progress", "Hold", "On Hold", ISSUE_IT_ROLE, None),  # not in the signed-off 14 - see above
	("On Hold", "Resume", "In Progress", ISSUE_IT_ROLE, None),  # not in the signed-off 14 - see above
] + [
	(state, "Cancel", "Cancelled", ISSUE_IT_ROLE, None)
	for state in ("Open", "In Progress", "Replied", "With Provider", "Provider Responded", "On Hold")
]


def _ensure_issue_workflow():
	"""Bind `status` to a real Frappe Workflow so a jump like Open -> Closed is rejected.

	Idempotent by rebuilding the two child tables from scratch on every run (same
	pattern as `_ensure_work_status_dashboard`'s workspace content) rather than a
	check-then-append merge: simpler to get right, and it is one `Workflow.save()`,
	not N child-row inserts, so re-running never duplicates a state or transition.
	"""
	for state_name, style in ISSUE_STATE_STYLES.items():
		if not frappe.db.exists("Workflow State", state_name):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state_name, "style": style}
			).insert(ignore_permissions=True)

	action_names = sorted({action for _, action, _, _, _ in ISSUE_WORKFLOW_TRANSITIONS})
	for action_name in action_names:
		if not frappe.db.exists("Workflow Action Master", action_name):
			frappe.get_doc(
				{"doctype": "Workflow Action Master", "workflow_action_name": action_name}
			).insert(ignore_permissions=True)

	# Not `_upsert()`: that helper calls `doc.save()` once immediately for an
	# existing record, using whatever child rows are CURRENTLY stored - before this
	# function gets a chance to clear and rebuild them. That intermediate save
	# re-validates old, possibly-stale states/transitions (hit for real: a state's
	# doc_status changed between two runs of this file during development, and the
	# stale stored value alone was enough to fail `Workflow.validate_docstatus()`
	# before a single new row was even appended). So states/transitions are cleared
	# and rebuilt on the in-memory doc BEFORE the one save() call that actually runs.
	values = {
		"workflow_name": ISSUE_WORKFLOW_NAME,
		"document_type": "Issue",
		"workflow_state_field": "status",
		"is_active": 1,
		# False: `_ensure_issue_statuses()` already owns the status Select's
		# options via a Property Setter. Letting the Workflow also override them
		# would be two places asserting the same list.
		"override_status": 0,
	}
	workflow_exists = frappe.db.exists("Workflow", ISSUE_WORKFLOW_NAME)
	if workflow_exists:
		workflow = frappe.get_doc("Workflow", ISSUE_WORKFLOW_NAME)
		workflow.update(values)
	else:
		workflow = frappe.get_doc({"doctype": "Workflow", "name": ISSUE_WORKFLOW_NAME, **values})
	workflow.states = []
	workflow.transitions = []
	for state_name in ISSUE_STATUSES:
		workflow.append(
			"states",
			{
				"state": state_name,
				"doc_status": ISSUE_STATE_DOC_STATUS[state_name],
				# Permissive on purpose: existing read/write access to Issue is owned
				# by the Custom DocPerms (Support Team, All if_owner, Projects
				# Manager) untouched by this plan. `allow_edit: All` means the
				# Workflow's per-state edit lock never becomes a SECOND, stricter
				# permission layer on top of those - only the transition `allowed`
				# role controls who can move the document.
				"allow_edit": "All",
			},
		)
	for state_name, action, next_state, allowed, condition in ISSUE_WORKFLOW_TRANSITIONS:
		row = workflow.append(
			"transitions",
			{"state": state_name, "action": action, "next_state": next_state, "allowed": allowed},
		)
		if condition:
			row.condition = condition
	workflow.flags.ignore_permissions = True
	if workflow_exists:
		workflow.save()
	else:
		workflow.insert()
	return workflow


def _ensure_issue_pipeline_page():
	"""Create the DB `Page` record the app-code Page (issue_pipeline_tracker.json/.js)
	describes.

	A standard Page is dual: the .json/.js files under the app (survive a restore,
	already committed) and a `Page` doctype row (does NOT survive a restore - it is
	normally (re)created by `bench migrate`'s doc-sync step). This module cannot run
	`bench migrate`, and `Workspace Sidebar`'s `link_to` is link-validated against
	that row existing, so this recreates it directly, keyed on the same fields as the
	on-disk JSON.
	"""
	values = {
		"page_name": "issue-pipeline",
		"title": "Issue Pipeline Tracker",
		"module": "SplINH",
		"standard": "Yes",
		"system_page": 0,
	}
	page_exists = frappe.db.exists("Page", "issue-pipeline")
	if page_exists:
		page = frappe.get_doc("Page", "issue-pipeline")
		page.update(values)
		page.flags.ignore_permissions = True
		page.flags.ignore_mandatory = True
		page.save()
	else:
		page = frappe.get_doc({"doctype": "Page", "name": "issue-pipeline", **values})
		# `Page.validate()` throws "Not in Developer Mode" for a brand-new standard
		# Page unless developer_mode is on - which it deliberately is not on this
		# site. `ignore_validate` is exactly what Frappe's own doc-sync
		# (`frappe.modules.import_file.import_doc`) sets for this same situation:
		# importing a Page whose real source of truth is the on-disk JSON, not a
		# hand-filled form.
		page.flags.ignore_validate = True
		page.flags.ignore_permissions = True
		page.flags.ignore_mandatory = True
		page.insert()
	existing_roles = {row.role for row in page.roles}
	changed = False
	for role in ("System Manager", ISSUE_IT_ROLE):
		if role not in existing_roles:
			page.append("roles", {"role": role})
			changed = True
	if changed:
		page.save(ignore_permissions=True)


def _ensure_issue_pipeline_page_sidebar_entry():
	"""Surface the Issue Pipeline Tracker page in the Support sidebar.

	Same mechanism as `_ensure_sidebar_entry()` (Work Status -> Projects sidebar):
	this build drives navigation from per-module `Workspace Sidebar` records, so a
	Page unreachable from there is only reachable by typing its URL.
	"""
	if not frappe.db.exists("Workspace Sidebar", "Support"):
		return
	sidebar = frappe.get_doc("Workspace Sidebar", "Support")
	for item in sidebar.items:
		if item.link_to == "issue-pipeline" and item.link_type == "Page":
			return
	sidebar.append(
		"items",
		{
			"label": "Issue Pipeline Tracker",
			"type": "Link",
			"link_type": "Page",
			"link_to": "issue-pipeline",
			"icon": "activity",
		},
	)
	rows = sidebar.items
	rows.insert(2, rows.pop())  # sit directly under "Home" / "Issue"
	for index, row in enumerate(rows, start=1):
		row.idx = index
	sidebar.save(ignore_permissions=True)


# Grouped so the dashboard reads at a glance (6 tiles) rather than 9 near-identical
# ones, matching the same 6 palette colours as ISSUE_STATE_STYLES.
ISSUE_DASHBOARD_STATUS_GROUPS = {
	"Open - Queue": ["Open"],
	"In Progress": ["In Progress", "Replied", "On Hold"],
	"With Provider": ["With Provider", "Provider Responded"],
	"Resolved": ["Resolved"],
	"Closed": ["Closed"],
	"Cancelled": ["Cancelled"],
}
ISSUE_OPEN_STATUSES = ["Open", "In Progress", "Replied", "With Provider", "Provider Responded", "On Hold"]


def _ensure_issue_dashboard():
	"""Number Cards + Dashboard Charts, added into the existing public Support workspace.

	Unlike `_ensure_work_status_dashboard` (which owns its whole workspace and can
	safely wipe/rebuild `number_cards`/`charts`/`content`), Support already ships a
	full stock sidebar and layout - see the CHANGELOG's Helpdesk-vs-Support
	comparison - so this only ever APPENDS rows/content blocks that are not already
	there, and never touches anything pre-existing.
	"""
	if not frappe.db.exists("Workspace", "Support"):
		return
	workspace = frappe.get_doc("Workspace", "Support")

	card_names = []
	for label, statuses in ISSUE_DASHBOARD_STATUS_GROUPS.items():
		name = f"Issue Tracker - {label}"
		_upsert(
			"Number Card",
			name,
			{
				"label": name,
				"type": "Document Type",
				"document_type": "Issue",
				"function": "Count",
				"filters_json": json.dumps([["Issue", "status", "in", statuses]]),
				"is_public": 1,
				"show_percentage_stats": 0,
				"module": "Support",
			},
		)
		card_names.append(name)

	aging_name = "Issue Tracker - Open Over 7 Days"
	_upsert(
		"Number Card",
		aging_name,
		{
			"label": aging_name,
			"type": "Document Type",
			"document_type": "Issue",
			"function": "Count",
			"filters_json": json.dumps(
				[
					["Issue", "status", "in", ISSUE_OPEN_STATUSES],
					["Issue", "creation", "<", frappe.utils.add_days(frappe.utils.nowdate(), -7)],
				]
			),
			"is_public": 1,
			"show_percentage_stats": 0,
			"module": "Support",
		},
	)
	card_names.append(aging_name)

	chart_names = []
	for suffix, group_by, chart_type in (
		("By Status", "status", "Donut"),
		("By Provider", "custom_solution_provider", "Bar"),
	):
		name = f"Issue Tracker - {suffix}"
		chart = _upsert(
			"Dashboard Chart",
			name,
			{
				"chart_name": name,
				"chart_type": "Group By",
				"document_type": "Issue",
				"group_by_type": "Count",
				"group_by_based_on": group_by,
				"type": chart_type,
				"filters_json": "[]",
				"is_public": 1,
				"module": "Support",
			},
		)
		if chart.currency:
			frappe.db.set_value("Dashboard Chart", chart.name, "currency", None, update_modified=False)
		chart_names.append(name)

	# Aging: not a status count but "what's stuck" - open Issues bucketed by the day
	# they were raised. Dashboard Chart's "Count" type (time-series over a date
	# field) is the closest native fit; it does not support a custom
	# days-since-creation bucket dimension out of the box, so this is a deliberate
	# simplification of the plan's "aging" chart, not the exact bucketing described.
	aging_chart_name = "Issue Tracker - Aging - Open Issues Raised - Last 90 Days"
	aging_chart = _upsert(
		"Dashboard Chart",
		aging_chart_name,
		{
			"chart_name": aging_chart_name,
			"chart_type": "Count",
			"document_type": "Issue",
			"based_on": "creation",
			"timespan": "Last Quarter",
			"time_interval": "Daily",
			"type": "Line",
			"filters_json": json.dumps([["Issue", "status", "in", ISSUE_OPEN_STATUSES]]),
			"is_public": 1,
			"module": "Support",
		},
	)
	if aging_chart.currency:
		frappe.db.set_value("Dashboard Chart", aging_chart.name, "currency", None, update_modified=False)
	chart_names.append(aging_chart_name)

	existing_card_labels = {row.label for row in workspace.number_cards}
	for name in card_names:
		if name not in existing_card_labels:
			workspace.append("number_cards", {"number_card_name": name, "label": name})

	existing_chart_labels = {row.label for row in workspace.charts}
	for name in chart_names:
		if name not in existing_chart_labels:
			workspace.append("charts", {"chart_name": name, "label": name})

	existing_shortcut_labels = {row.label for row in workspace.shortcuts}
	if "Issue Pipeline Tracker" not in existing_shortcut_labels:
		workspace.append(
			"shortcuts",
			{
				"type": "Page",
				"link_to": "issue-pipeline",
				"label": "Issue Pipeline Tracker",
				"color": "Blue",
			},
		)

	content = json.loads(workspace.content or "[]")
	existing_ids = {block.get("id") for block in content}
	new_blocks = []
	if "issuetracker_hdr" not in existing_ids:
		new_blocks.append(
			{
				"id": "issuetracker_hdr",
				"type": "header",
				"data": {"text": '<span class="h4"><b>Issue Tracker Pipeline</b></span>', "col": 12},
			}
		)
	if "issuetracker_sc1" not in existing_ids:
		new_blocks.append(
			{"id": "issuetracker_sc1", "type": "shortcut", "data": {"shortcut_name": "Issue Pipeline Tracker", "col": 6}}
		)
	for i, name in enumerate(card_names, start=1):
		block_id = f"issuetracker_nc{i}"
		if block_id not in existing_ids:
			new_blocks.append({"id": block_id, "type": "number_card", "data": {"number_card_name": name, "col": 4}})
	for i, name in enumerate(chart_names, start=1):
		block_id = f"issuetracker_ch{i}"
		if block_id not in existing_ids:
			new_blocks.append({"id": block_id, "type": "chart", "data": {"chart_name": name, "col": 6}})

	if new_blocks:
		content.extend(new_blocks)
		workspace.content = json.dumps(content)

	workspace.save(ignore_permissions=True)


def _ensure_issue_fields():
	"""Create the issue-tracker fields if they are missing.

	Existence only. Placement, descriptions and defaults are owned by
	ISSUE_FIELD_LAYOUT / _ensure_issue_form_layout(), which runs immediately after and
	re-asserts them on every migrate - so they are deliberately not repeated here. The
	`insert_after` values below are just the creation anchors; the layout step moves
	them into the section it builds.
	"""
	fields = [
		{
			# Named for what it does, NOT "custom_raised_for": that would sit two fields
			# away from "Raised From" (Desk / Mobile App / IT Team) and the two labels
			# would be near-indistinguishable at a glance.
			"fieldname": "custom_relates_to",
			"label": "Relates To",
			"fieldtype": "Select",
			"options": "Customer\nLead",
			"insert_after": "subject",
		},
		{
			"fieldname": "custom_modules",
			"label": "Modules Affected",
			"fieldtype": "Table MultiSelect",
			"options": "Issue Module Item",
			"insert_after": "issue_type",
		},
		{
			"fieldname": "custom_solution_provider",
			"label": "Solution Provider",
			"fieldtype": "Link",
			"options": "Solution Provider",
			"insert_after": "custom_modules",
			"in_standard_filter": 1,
			"read_only": 1,
		},
		{
			"fieldname": "custom_raised_from",
			"label": "Raised From",
			"fieldtype": "Select",
			"options": "\nDesk\nMobile App\nIT Team",
			"insert_after": "custom_solution_provider",
			"in_standard_filter": 1,
		},
	]
	for df in fields:
		name = f"Issue-{df['fieldname']}"
		if frappe.db.exists("Custom Field", name):
			continue
		frappe.get_doc({"doctype": "Custom Field", "dt": "Issue", "is_system_generated": 0, **df}).insert(
			ignore_permissions=True
		)


def _ensure_issue_form_layout():
	"""Give the three issue-tracker fields a home of their own on the Issue form.

	They were originally chained off `issue_type`, which dropped all three - including
	a Table MultiSelect - into the stock header section's right-hand column, on top of
	Status, Priority, Issue Type and Issue Split From. Seven controls in a half-width
	column against four in the other: the column a reporter has to read most carefully
	was the crowded one, and the single most important field (Modules Affected) was the
	fifth thing down it.

	They now sit in their own section between the header and the stock "Details"
	section, so the form reads top-down as: who/what/how urgent -> what is affected and
	where it routes -> the free-text description -> the collapsible SLA/response/
	resolution/reference blocks that only IT ever opens.

	Layout only, and additive: this creates two container fields and re-points
	`insert_after` on the three existing ones. No standard field is hidden, removed or
	moved - `issue_split_from` simply returns to its stock position immediately after
	`issue_type`, because nothing is wedged in front of it any more.

	Deliberately NOT done with a `field_order` property setter. One would have to
	enumerate all 49 standard Issue fields and would then be re-applied verbatim on
	every migrate, so an ERPNext upgrade that adds or renames a field leaves us
	silently maintaining a stale copy of someone else's layout. Everything that
	actually needed to move here is a custom field, and `insert_after` moves custom
	fields without ever naming the standard ones.

	No Client Script either - none of this needs one.
	"""
	for df in ISSUE_LAYOUT_BREAKS:
		if frappe.db.exists("Custom Field", f"Issue-{df['fieldname']}"):
			continue
		frappe.get_doc({"doctype": "Custom Field", "dt": "Issue", "is_system_generated": 0, **df}).insert(
			ignore_permissions=True
		)

	for fieldname, props in ISSUE_FIELD_LAYOUT.items():
		_set_custom_field_props("Issue", fieldname, props)


def _ensure_issue_party_switch():
	"""Make the party picker follow "Relates To" - Customer or Lead, never both.

	Issue already ships with BOTH a `customer` and a `lead` Link field (confirmed on the
	live meta, not the JSON on disk), so this needs no new Link field, no Dynamic Link
	and no data migration. A Select drives the visibility of the two fields ERPNext
	itself already understands, which means `get_list_context()`, `has_website_permission()`
	and SLA customer-matching keep reading exactly the field they always read.

	Three parts:
	  1. `depends_on` on each of the two standard fields - pure config, and the reason a
	     Client Script is not needed for the visibility half of this.
	  2. `field_order`, to lift `lead` out of the collapsible "Reference" section at the
	     bottom of the form and stand it next to `customer`. Without this the switch is
	     useless: picking "Lead" would empty the header and hide the Lead picker two
	     sections down inside a collapsed block. This is the one thing `insert_after`
	     cannot do, because `insert_after` is only consulted for *custom* fields
	     (frappe/model/meta.py, Meta.sort_fields) - moving a standard field needs
	     `field_order`, which is exactly what Customize Form writes when you drag a field.
	  3. A Server Script guard, because config alone cannot undo a core side effect.
	     See _ensure_issue_party_guard_script().
	"""
	_set_property("Issue", "customer", "depends_on", 'eval:doc.custom_relates_to=="Customer"', "Code")
	_set_property("Issue", "lead", "depends_on", 'eval:doc.custom_relates_to=="Lead"', "Code")
	_set_issue_field_order()
	_ensure_issue_party_guard_script()


def _set_issue_field_order():
	"""Stand `lead` immediately after `customer`, in the header section.

	Computed fresh from `tabDocField` on every run rather than stored as a 46-item
	literal. That matters: a stored copy would be a frozen snapshot of someone else's
	layout, and an ERPNext upgrade that adds or renames an Issue field would leave us
	silently re-applying a stale order. Reading the stock order and moving exactly one
	field means every upgrade is picked up on the next migrate for free.

	Only standard fields are listed. Custom fields are deliberately left out, so they
	stay positioned by their own `insert_after` and the section built in
	_ensure_issue_form_layout() is untouched by this.
	"""
	order = frappe.get_all(
		"DocField",
		filters={"parent": "Issue", "parenttype": "DocType"},
		pluck="fieldname",
		order_by="idx",
	)
	if "lead" not in order or "customer" not in order:
		frappe.log_error(
			title="splinh_setup: Issue party fields missing",
			message=f"Expected standard `customer` and `lead` fields on Issue; got {order}. "
			"Field order left untouched.",
		)
		return
	order.remove("lead")
	order.insert(order.index("customer") + 1, "lead")
	_set_property("Issue", None, "field_order", json.dumps(order), "Small Text", for_doctype=True)


def _ensure_issue_party_guard_script():
	"""Stop ERPNext quietly attaching the party the reporter did not choose.

	`Issue.validate()` calls `set_lead_contact(self.raised_by)`, which fills in `lead`,
	and `contact` -> `customer`, from the REPORTER'S OWN email address. That is core
	ERPNext, it runs server-side, and it runs before this script (Document.run_method
	calls the controller first and `run_server_script_for_doc_event` after), so no
	amount of `depends_on` can prevent it. Left alone, an issue the reporter marked
	"Customer" can end up carrying a Lead - or one marked "Lead" a Customer - that
	nobody can see on the form, because the contradicting field is hidden.

	Not hypothetical: 70 enabled users on this site have their own address on a Contact,
	which is what feeds the `contact` -> `customer` branch. (None of those Contacts links
	to a Customer *today*, so nothing is actually mis-set right now - but that is a data
	coincidence, not a guarantee, and one linked Contact would change it.)

	Deliberately minimal. It only clears the field that contradicts the declared type,
	and only when the other one is actually filled, so:
	  - it can never leave an issue with no party reference at all;
	  - an issue that arrived by email or from the portal, where nobody picked anything
	    and ERPNext's guess is the only party reference there is, is left alone.
	`contact` is never touched: it is a legitimate reference in its own right and lives
	in the Reference section where it can be seen and corrected.
	"""
	script = '''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
# Keep the stored party in step with the declared "Relates To". ERPNext's own
# Issue.validate() -> set_lead_contact(raised_by) guesses a Lead/Contact/Customer from
# the reporter's email address, server-side and before this runs, so the hidden field
# can otherwise be silently populated. Only clears a contradiction, and only when the
# chosen side is actually filled - never leaves the issue with no party at all.
relates_to = doc.custom_relates_to
if relates_to == "Customer" and doc.customer and doc.lead:
    doc.lead = None
elif relates_to == "Lead" and doc.lead and doc.customer:
    doc.customer = None
'''
	_upsert_server_script(
		"Issue Party Type Guard",
		{
			"script_type": "DocType Event",
			"reference_doctype": "Issue",
			"doctype_event": "Before Save",
			"script": script,
			"disabled": 0,
		},
	)


def _ensure_issue_statuses():
	"""Extend the status Select - add only, never remove.

	ERPNext's own code writes the stock values (auto_close_tickets() closes from
	"Replied"; issue.js Close/Reopen writes Closed/Open), so dropping any of them
	would break core behaviour.
	"""
	_set_property("Issue", "status", "options", "\n".join(ISSUE_STATUSES), "Text")


def _ensure_reporter_permission():
	"""Let anyone in the org raise an issue and see only their own.

	Out of the box `Support Team` is the ONLY role with any permission on Issue,
	so without this nobody else can report anything at all.
	"""
	existing = frappe.db.exists("Custom DocPerm", {"parent": "Issue", "role": "All", "permlevel": 0})
	if existing:
		return
	frappe.get_doc(
		{
			"doctype": "Custom DocPerm",
			"parent": "Issue",
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": "All",
			"permlevel": 0,
			"read": 1,
			"write": 1,
			"create": 1,
			"if_owner": 1,  # reporters see and edit only what they raised
			"report": 1,
			"email": 1,
			"share": 1,
		}
	).insert(ignore_permissions=True)


def _ensure_issue_routing_script():
	script = '''# Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
# Derive the provider from the selected modules. One provider -> set it.
# Several -> leave unset and tell IT to split, so each provider owns its own issue.
providers = []
for row in (doc.get("custom_modules") or []):
    provider = frappe.db.get_value("System Module", row.system_module, "solution_provider")
    if provider and provider not in providers:
        providers.append(provider)

if len(providers) == 1:
    doc.custom_solution_provider = providers[0]
elif len(providers) > 1:
    doc.custom_solution_provider = None
    frappe.msgprint(
        "This issue spans modules owned by different providers ("
        + ", ".join(providers)
        + "). Split it so each provider gets its own issue."
    )
'''
	_upsert_server_script(
		"Issue Route To Provider",
		{
			"script_type": "DocType Event",
			"reference_doctype": "Issue",
			"doctype_event": "Before Save",
			"script": script,
			"disabled": 0,
		},
	)


def _seed_providers_and_modules():
	"""Seed placeholders only on a completely empty system - never overwrite real data."""
	if frappe.db.count("Solution Provider"):
		_seed_placeholder_members()
		return
	for provider in PLACEHOLDER_PROVIDERS:
		frappe.get_doc(
			{
				"doctype": "Solution Provider",
				"provider_name": provider,
				"enabled": 1,
				"notes": f"{PLACEHOLDER_MARKER} - replace with the real provider details.",
			}
		).insert(ignore_permissions=True)
	_seed_placeholder_members()
	for module_name, provider in PLACEHOLDER_MODULES:
		if frappe.db.exists("System Module", module_name):
			continue
		frappe.get_doc(
			{
				"doctype": "System Module",
				"module_name": module_name,
				"solution_provider": provider,
				"enabled": 1,
				"description": "PLACEHOLDER - confirm which provider owns this module.",
			}
		).insert(ignore_permissions=True)


def _seed_placeholder_members():
	"""Stand-in provider members so the flow is testable before real vendors exist.

	Scoped by the PLACEHOLDER marker in `notes`, not by "has no members yet". The
	latter was the first attempt and it silently stopped self-healing: one team
	email was stored wrongly, the provider was seeded with the other two members,
	and the correction could then never be applied because the table was no longer
	empty. Keyed on the marker instead, a placeholder keeps topping itself up, and
	the moment you replace one with real details the marker goes and we stop
	touching it - including its member list.

	Deliberately does NOT create any `User Permission`. Membership is just a link
	and is harmless; a User Permission is what actually restricts what a user can
	see, so attaching one to an internal account would silently cut that person's
	own access to most issues. Those get created only for genuine vendor accounts.
	"""
	for provider in frappe.get_all("Solution Provider", pluck="name"):
		doc = frappe.get_doc("Solution Provider", provider)
		if not (doc.notes or "").startswith(PLACEHOLDER_MARKER):
			continue
		existing = {m.user for m in doc.members}
		added = False
		for user in get_work_status_team():
			if user in existing:
				continue
			doc.append("members", {"user": user})
			added = True
		if added:
			doc.save(ignore_permissions=True)


# ------------------------------------------------------------------- helpers


def _unblock_modules(users, modules):
	"""Remove `modules` from each user's "Block Modules" list.

	A workspace is filtered out of the sidebar by `get_blocked_modules()` in
	frappe/desk/desktop.py BEFORE any permission or role check runs. So a user can
	hold every role the tool needs, be a member of the project, own tasks in it,
	and still see nothing at all - which is exactly what happened to a team member
	who had 56 modules blocked, "Projects" among them.

	Only the named modules are removed. The rest of the block list is left alone:
	those lists were curated deliberately to keep these users' navigation small,
	and wiping them would hand people back a lot of UI nobody asked to restore.

	Note the save() goes through the document API on purpose. Deleting the child
	row with `frappe.db.delete` leaves `get_blocked_modules()` returning the stale
	list from cache, so the change appears to do nothing.
	"""
	for user in users:
		if not frappe.db.exists("User", user):
			continue
		user_doc = frappe.get_doc("User", user)
		keep = [row for row in user_doc.block_modules if row.module not in modules]
		if len(keep) == len(user_doc.block_modules):
			continue
		removed = sorted({row.module for row in user_doc.block_modules} & set(modules))
		user_doc.block_modules = keep
		user_doc.save(ignore_permissions=True)
		frappe.clear_cache(user=user)
		frappe.log_error(
			title="splinh_setup: unblocked modules for user",
			message=f"Removed {removed} from Block Modules for {user} so the "
			f"corresponding workspace is reachable in the sidebar.",
		)


def _set_property(doctype, fieldname, prop, value, property_type, for_doctype=False):
	"""make_property_setter that only writes when the value actually differs.

	Called on every migrate, so writing unconditionally is wasted work and needless
	row-lock exposure - which has already caused a "Lock wait timeout" failure here.

	The doctype-level lookup is by `doctype_or_field`, not by `field_name`. It used to
	filter on `field_name = ""`, but make_property_setter stores that column as NULL for
	a doctype-level property, so the lookup matched nothing, `current` was always None,
	and every single run re-wrote Customer's `autoname` and `naming_rule` - a delete plus
	an insert each time, which is precisely the churn this helper exists to avoid. It
	logged no error, so it never showed up as a failure; the field_order property setter
	added for the Issue party switch would have made it a great deal more expensive.
	"""
	filters = {"doc_type": doctype, "property": prop}
	if for_doctype:
		filters["doctype_or_field"] = "DocType"
	else:
		filters["doctype_or_field"] = "DocField"
		filters["field_name"] = fieldname
	current = frappe.db.get_value("Property Setter", filters, "value")
	if current == value:
		return
	make_property_setter(
		doctype, fieldname, prop, value, property_type,
		for_doctype=for_doctype, is_system_generated=False,
	)


def _set_custom_field_props(doctype, fieldname, props):
	"""Update an existing Custom Field in place, writing only what actually differs.

	The sibling of `_set_property` for fields we own outright. `_ensure_issue_fields`
	deliberately skips a Custom Field that already exists, so without this the only way
	to change one after it had been created was to delete it - which on a Table
	MultiSelect would take its data with it. Properties listed here are re-asserted;
	anything not listed is left exactly as found, so a hand tweak in Customize Form
	survives.

	Never creates the field. If it is missing, whatever was meant to create it has
	already failed and inventing a half-specified field here would only hide that.
	"""
	name = f"{doctype}-{fieldname}"
	if not frappe.db.exists("Custom Field", name):
		frappe.log_error(
			title="splinh_setup: custom field missing",
			message=f"{name} does not exist, so its layout properties were not applied.",
		)
		return
	field = frappe.get_doc("Custom Field", name)
	changed = {k: v for k, v in props.items() if field.get(k) != v}
	if not changed:
		return
	field.update(changed)
	field.save(ignore_permissions=True)


def _upsert_server_script(name, values):
	"""_upsert for Server Scripts, plus the cache invalidation Frappe misses.

	Server Scripts are dispatched from a cached `server_script_map` in redis.
	`Server Script.on_update` does clear it - but that happens INSIDE the transaction,
	before the commit. Any other process that misses the cache during that window
	rebuilds the map from a database that cannot see the new row yet, and writes that
	stale map straight back to redis. The new script is then silently inert: it does not
	run, nothing errors, nothing reaches the Error Log, and it stays that way until
	something else happens to save a Server Script.

	Seen here for real, not theorised. After the run that created "Issue Party Type
	Guard", a fresh process still saw
	    {'Before Save': ['Issue Route To Provider']}
	and the guard did nothing at all in an end-to-end test. A single delete_value
	afterwards and both scripts appeared. Re-invalidating after the commit closes the
	window - which matters most on exactly the path this module exists for: a restore
	followed by `bench migrate`, with workers running.
	"""
	doc = _upsert("Server Script", name, values)
	frappe.db.after_commit.add(lambda: frappe.client_cache.delete_value("server_script_map"))
	return doc


def _upsert(doctype, name, values):
	"""Create the record if missing, otherwise update it in place."""
	if frappe.db.exists(doctype, name):
		doc = frappe.get_doc(doctype, name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return doc
	doc = frappe.get_doc({"doctype": doctype, "name": name, **values})
	doc.insert(ignore_permissions=True)
	return doc


# ---------------------------------------------------------------- Call tracking

CALL_TRACKING_FIELDS = [
	{
		"fieldname": "custom_device_id",
		"label": "Device ID",
		"fieldtype": "Data",
		"insert_after": "type_of_call",
	},
	{
		"fieldname": "custom_sim_slot",
		"label": "SIM Slot",
		"fieldtype": "Data",
		"insert_after": "custom_device_id",
	},
	{
		"fieldname": "custom_contact_name",
		"label": "Contact Name",
		"fieldtype": "Data",
		"insert_after": "custom_sim_slot",
	},
	{
		# List-view readability + Lead/Customer Connections (2026-09-24). See the
		# approved plan and the module docstring in api/call_tracking.py for why
		# these three exist alongside the original three above.
		"fieldname": "custom_call_summary",
		"label": "Call Summary",
		"fieldtype": "Data",
		"insert_after": "custom_contact_name",
		"read_only": 1,
	},
	{
		"fieldname": "custom_linked_party",
		"label": "Linked Party",
		"fieldtype": "Data",
		"insert_after": "custom_call_summary",
		"read_only": 1,
		"in_list_view": 1,
	},
	{
		"fieldname": "custom_lead",
		"label": "Lead",
		"fieldtype": "Link",
		"options": "Lead",
		"insert_after": "custom_linked_party",
		"read_only": 1,
	},
	{
		# WhatsApp ingestion (2026-09-25). Measured (SIM) vs inferred (WhatsApp) -
		# must be set on every row, forever. reqd=1 and deliberately NO explicit
		# `default` key: a default of "SIM" would turn any code path that forgets
		# to set it into silently labelling a guess as a fact - a forgotten value
		# should fail loudly instead (and the background job records a Call Sync
		# Failure).
		#
		# CORRECTION (2026-09-25 audit): that protection did not actually hold as
		# originally written. Options "SIM\nWhatsApp" has no leading blank line, and
		# frappe.model.create_new.get_static_default_value() (core, confirmed by
		# reading it) falls back to the FIRST Select option as an IMPLICIT static
		# default whenever a Select field has no explicit `default` -
		# `df.options.split("\n", 1)[0]` - applied via Document._set_defaults() on
		# EVERY insert path (get_doc(dict), new_doc(), Desk), not just Desk. Proved
		# live: a savepoint-rolled-back get_doc({...}).insert() with custom_source
		# omitted silently landed as "SIM" instead of raising the mandatory-field
		# error the comment above assumed would fire. The leading blank line below
		# (matching custom_detector_confidence/custom_call_type just below) makes
		# the implicit default resolve to "" instead of "SIM" - which still fails
		# the reqd=1 check - restoring the "forgotten value fails loudly" guarantee
		# without adding a real default and without touching push_calls/
		# ingest_whatsapp_call, which already set this field explicitly either way.
		"fieldname": "custom_source",
		"label": "Source",
		"fieldtype": "Select",
		"options": "\nSIM\nWhatsApp",
		"insert_after": "custom_lead",
		"reqd": 1,
		"in_list_view": 1,
		"in_standard_filter": 1,
	},
	{
		# Only meaningful for WhatsApp rows - SIM calls are facts, not scored.
		"fieldname": "custom_detector_confidence",
		"label": "Detector Confidence",
		"fieldtype": "Select",
		"options": "\nHigh\nMedium\nLow",
		"insert_after": "custom_source",
	},
	{
		"fieldname": "custom_call_type",
		"label": "Call Type (Voice/Video)",
		"fieldtype": "Select",
		"options": "\nVoice\nVideo\nUnknown",
		"insert_after": "custom_detector_confidence",
	},
	{
		"fieldname": "custom_whatsapp_app_version",
		"label": "WhatsApp App Version",
		"fieldtype": "Data",
		"insert_after": "custom_call_type",
	},
	{
		# Call transcription (2026-09-29). Set Pending by receive_call_recording
		# (raw db.set_value, no write permission needed), then Completed/Failed by
		# the background job - see api/call_tracking.py's
		# _transcribe_call_recording_job. read_only=1 like the other job-set fields
		# above (custom_call_summary/custom_linked_party/custom_lead): this is
		# system-computed, never hand-edited.
		"fieldname": "custom_transcript_status",
		"label": "Transcript Status",
		"fieldtype": "Select",
		"options": "\nPending\nCompleted\nFailed",
		"insert_after": "custom_whatsapp_app_version",
		"in_list_view": 1,
		"in_standard_filter": 1,
		"read_only": 1,
	},
	{
		"fieldname": "custom_transcript",
		"label": "Transcript",
		"fieldtype": "Long Text",
		"insert_after": "custom_transcript_status",
		"read_only": 1,
	},
	{
		"fieldname": "custom_transcript_language",
		"label": "Transcript Language",
		"fieldtype": "Data",
		"insert_after": "custom_transcript",
		"read_only": 1,
	},
	{
		# REPO_ID@REVISION (short), so a future model/revision change is traceable
		# on old rows rather than silently ambiguous about which model produced them.
		"fieldname": "custom_transcript_model",
		"label": "Transcript Model",
		"fieldtype": "Data",
		"insert_after": "custom_transcript_language",
		"read_only": 1,
	},
	{
		"fieldname": "custom_transcript_error",
		"label": "Transcript Error",
		"fieldtype": "Small Text",
		"insert_after": "custom_transcript_model",
		"read_only": 1,
	},
]

# Add-only extensions of two stock Select fields. The existing options are kept in
# their exact order - stock ERPNext writes several of them (Completed, No Answer,
# Busy, Failed; Incoming/Outgoing) - "Unknown" is only appended.
CALL_LOG_STATUS_OPTIONS = ["Ringing", "In Progress", "Completed", "Failed", "Busy", "No Answer", "Queued", "Cancelled", "Unknown"]
CALL_LOG_TYPE_OPTIONS = ["Incoming", "Outgoing", "Unknown"]

CALL_TRACKING_MANAGER_ROLE = "Call Tracker Manager"
CALL_TRACKING_USER_ROLE = "Call Tracker User"

# Five outcome labels for the mobile app to tag a call with. The doctype is a bare
# 2-field master (call_type Data + amended_from Link) - confirmed live - so there is
# nothing else to seed per record.
CALL_TRACKING_CALL_TYPES = ["Interested", "Call Back", "Not Reachable", "Converted", "Not Interested"]


def ensure_call_tracking():
	"""Mobile call-tracking integration on stock `Call Log`. See the approved plan
	(the-issue-is-theres-tingly-pebble.md) for the full rationale.
	"""
	_ensure_call_tracking_fields()
	_ensure_call_tracking_permissions()
	_seed_call_types()
	_ensure_call_tracking_display()
	_ensure_call_tracking_source()


def _ensure_call_tracking_fields():
	for df in CALL_TRACKING_FIELDS:
		name = f"Call Log-{df['fieldname']}"
		if frappe.db.exists("Custom Field", name):
			continue
		frappe.get_doc({"doctype": "Custom Field", "dt": "Call Log", "is_system_generated": 0, **df}).insert(
			ignore_permissions=True
		)


def _ensure_call_tracking_permissions():
	"""Create the two call-tracking roles and the three Custom DocPerm rows on Call Log.

	No row for `Employee`: adding any Custom DocPerm row on a doctype makes Frappe stop
	consulting the stock DocPerms for it entirely, including Employee's current blanket
	read=1 (confirmed live) - so that access is deliberately not carried forward here,
	per the plan.
	"""
	for role in (CALL_TRACKING_MANAGER_ROLE, CALL_TRACKING_USER_ROLE):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)

	call_tracking_perms = [
		{
			"role": "System Manager",
			"read": 1, "write": 1, "create": 1, "delete": 1,
		},
		{
			"role": CALL_TRACKING_MANAGER_ROLE,
			"read": 1, "report": 1, "export": 1,
		},
		{
			"role": CALL_TRACKING_USER_ROLE,
			"read": 1, "create": 1, "if_owner": 1,
		},
	]
	for perm in call_tracking_perms:
		if frappe.db.exists("Custom DocPerm", {"parent": "Call Log", "role": perm["role"], "permlevel": 0}):
			continue
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Call Log",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"permlevel": 0,
				**perm,
			}
		).insert(ignore_permissions=True)


CALL_MANAGER_REVIEW_FIELDS = [
	{
		"fieldname": "custom_manager_feedback",
		"label": "Manager Feedback",
		"fieldtype": "Small Text",
		"insert_after": "custom_transcript_error",
		"permlevel": 1,
	},
	{
		# Changed from Select to free text (2026-09-30, user's own correction -
		# the placeholder dropdown wasn't wanted after all). See
		# _fix_custom_training_need_fieldtype() below for the live in-place
		# migration this needs on a bench where the field was already created
		# as a Select - this dict only governs a FRESH creation from here on.
		"fieldname": "custom_training_need",
		"label": "Training Need",
		"fieldtype": "Small Text",
		"insert_after": "custom_manager_feedback",
		"permlevel": 1,
	},
	{
		# Real stage list (2026-09-30, replaces the 2026-09-30 placeholder).
		# Leading blank line matches every other Select on this doctype - see
		# custom_source's own comment for why (avoids Frappe's implicit
		# first-option-as-default behaviour).
		"fieldname": "custom_call_drop_stage",
		"label": "Call Drop Stage",
		"fieldtype": "Select",
		"options": (
			"\nConnection\nIntroduction\nProfiling\nNeed Profiling\nProduct Selection\n"
			"Product Intro\nObjection Handling\nRate\nNegotiation and Closing\nOther"
		),
		"insert_after": "custom_training_need",
		"permlevel": 1,
	},
	{
		# Computed, never hand-edited - see call_log_manager_review.py's validate
		# hook (hooks.py doc_events["Call Log"]). Turns on the moment
		# custom_manager_feedback has real content.
		"fieldname": "custom_manager_remarked",
		"label": "Remarked by Manager",
		"fieldtype": "Check",
		"insert_after": "custom_call_drop_stage",
		"permlevel": 1,
		"read_only": 1,
	},
]


def _ensure_call_manager_review_fields():
	"""4 manager-review fields on Call Log (2026-09-30), permlevel=1 - visible and
	editable only to Call Tracker Manager / System Manager, invisible to everyone
	else (not just read-only - Frappe strips permlevel-restricted fields entirely
	for a role with no grant at that level). Same mechanism already proven live on
	Stock Entry's pricing-field restriction.

	Call Tracker Manager has no permlevel-0 write on Call Log at all (read/report/
	export only - see _ensure_call_tracking_permissions()). Granting write=1 at
	permlevel 1 ONLY (not touching permlevel 0) is enough for Frappe to let this
	role open and save the form, but only these 4 fields actually persist - any
	edit to an existing (permlevel-0) field is silently discarded on save. That is
	the intended, minimal-scope outcome: managers gain the ability to fill in
	review fields, nothing else on Call Log becomes editable for them.

	System Manager also gets an explicit permlevel-1 grant: permlevel
	restrictions apply to every role uniformly, so without this, these fields
	would be invisible even to admins.
	"""
	for df in CALL_MANAGER_REVIEW_FIELDS:
		name = f"Call Log-{df['fieldname']}"
		if frappe.db.exists("Custom Field", name):
			continue
		frappe.get_doc({"doctype": "Custom Field", "dt": "Call Log", "is_system_generated": 0, **df}).insert(
			ignore_permissions=True
		)

	_fix_custom_training_need_fieldtype()
	_set_custom_field_props(
		"Call Log",
		"custom_call_drop_stage",
		{"options": CALL_MANAGER_REVIEW_FIELDS[2]["options"]},
	)

	for role in (CALL_TRACKING_MANAGER_ROLE, "System Manager"):
		if frappe.db.exists("Custom DocPerm", {"parent": "Call Log", "role": role, "permlevel": 1}):
			continue
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Call Log",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"permlevel": 1,
				"role": role,
				"read": 1,
				"write": 1,
			}
		).insert(ignore_permissions=True)


def _fix_custom_training_need_fieldtype():
	"""One-time live migration (2026-09-30): custom_training_need was originally
	created as a Select (placeholder dropdown), then the user decided it should be
	free text instead. Frappe's own Customize Form blocks a direct Select ->
	Small Text change (confirmed live: "Fieldtype cannot be changed from Select
	to Small Text") - core only allows a fieldtype change between two types in
	the same ALLOWED_FIELDTYPE_CHANGE group
	(customize_form.py), and Select/Small Text are never in the same group.
	Select IS grouped with Data, and Data IS grouped with Small Text, so the
	safe, framework-sanctioned path is the same two-hop route Customize Form
	itself would require if done by hand: Select -> Data -> Small Text.

	No-op once already Small Text (idempotent, safe to re-run every migrate).
	"""
	name = "Call Log-custom_training_need"
	if not frappe.db.exists("Custom Field", name):
		return
	current = frappe.db.get_value("Custom Field", name, "fieldtype")
	if current == "Small Text":
		return
	field = frappe.get_doc("Custom Field", name)
	if current == "Select":
		field.fieldtype = "Data"
		field.save(ignore_permissions=True)
		field.reload()
	field.fieldtype = "Small Text"
	field.options = None
	field.save(ignore_permissions=True)


def _seed_call_types():
	for call_type in CALL_TRACKING_CALL_TYPES:
		_upsert("Telephony Call Type", call_type, {"call_type": call_type})


def _ensure_call_tracking_source():
	"""WhatsApp ingestion (2026-09-25): the add-only "Unknown" options, plus a
	one-time backfill of custom_source on rows that predate the field.

	"Unknown" status exists because device testing proved WhatsApp cannot tell
	rejected/failed from an ordinary completed/missed call; "Unknown" type is for
	an undetermined direction. Both are appended, never reordered.

	Backfill: every row that existed before custom_source was added came from
	push_calls (SIM) - confirmed live, all 36 from device d52a3b223e7cb469, and no
	other Call Log creator is active on this bench. Scoped to the SIM naming
	scheme ("M-" prefix), not to "any blank row": this runs on every migrate, so a
	blanket backfill would one day silently label some other blank row - a bug-path
	WhatsApp row, say - as SIM, which is precisely the mislabelling custom_source
	exists to prevent. The name prefix makes the backfill provably correct.
	Touches only blank rows, so it is a no-op after the first run.
	"""
	_set_property("Call Log", "status", "options", "\n".join(CALL_LOG_STATUS_OPTIONS), "Text")
	_set_property("Call Log", "type", "options", "\n".join(CALL_LOG_TYPE_OPTIONS), "Text")
	frappe.db.sql(
		"""UPDATE `tabCall Log` SET custom_source = 'SIM'
		WHERE IFNULL(custom_source, '') = '' AND name LIKE 'M-%%'"""
	)
	# NB (2026-09-25 audit): a Customize-Form-level `default` for custom_source,
	# scoped to "manual Desk creation only", was considered and rejected. Tried
	# live via a Property Setter (default="SIM") and confirmed by test that
	# `Document.insert()` calls `self._set_defaults()` unconditionally for EVERY
	# creation path - `frappe.get_doc({...}).insert()` (what push_calls and
	# ingest_whatsapp_call both use) included, not only Desk's own "+ New" form -
	# so a real default there would have weakened the guarantee for every path,
	# not just Desk. Reverted (Property Setter deleted).
	#
	# While chasing that, found the actual bug: custom_source was ALREADY
	# silently defaulting to "SIM" before any of today's changes, via a
	# completely different mechanism - see the options fix on the field
	# definition above (CALL_TRACKING_FIELDS) and _set_custom_field_props call
	# below, which corrects it in place on this already-deployed field.
	_set_custom_field_props("Call Log", "custom_source", {"options": "\nSIM\nWhatsApp"})


# WhatsApp "Unknown" status -> "Missed" LABEL (2026-09-25, product decision).
# Display only: the stored value stays "Unknown" (no schema change, no data
# migration), so every query, filter, report and aggregate still sees "Unknown".
# Only a human-facing rendering of a row with custom_source == "WhatsApp" AND
# status == "Unknown" is relabelled; SIM rows never are.
#
# Where it applies (all traced in frappe/public/js, v16):
#   - list view status pill + mobile pill, and the form header indicator: both go
#     through frappe.get_indicator(), which consults listview_settings.get_indicator.
#     The List Client Script is evaluated on every meta load (model.js
#     init_doctype -> __custom_list_js), so it's in place for the form too.
#     The 3rd element keeps the RAW filter, so clicking the pill still filters
#     status = Unknown.
#   - report view status cell: frappe.format(value, docfield_map df, .., row) ->
#     df.formatter, installed from the list settings' onload (report_view.js also
#     calls settings.onload). docfield_map isn't populated yet when the list script
#     is evaluated, hence onload rather than top level.
#   - the form's read-only Status field: its per-document docfield copy, via the
#     Form Client Script below.
# NOT relabelled (aggregate, reads the raw value): sidebar group-by counts, the
# filter dropdown's options, and exports.
#
# 2026-09-25 follow-up: custom_call_summary (title_field) is STORED text built by
# _whatsapp_summary() in api/call_tracking.py and, for these same rows, ends in the
# literal suffix "- Unknown" (e.g. "WhatsApp Voice - Incoming from Danish - Unknown")
# - which read oddly next to a pill that now says "Missed". Same principle applies:
# only the trailing "- Unknown" is swapped for "- Missed" wherever the title renders
# to a person (list Subject column, form header/title area + the field itself,
# report view). The stored custom_call_summary and _whatsapp_summary()'s output are
# both untouched - this is the display layer only, reusing the exact WhatsApp+Unknown
# check already written below rather than re-detecting it three more times:
#   - list Subject column: list_view.js's get_subject_text() reads
#     this.settings.formatters[title_field] and writes the result via textContent
#     (not innerHTML) - no HTML-escaping wanted or done here, unlike settings.formatters.status.
#   - form header/title area + browser tab title: toolbar.js's set_title() reads
#     frm.doc[title_field] directly - no formatter hook exists for it at all, so it's
#     overridden after the fact from the Form Client Script's own refresh handler
#     (frm.page.set_title / frappe.utils.set_title), the same way many custom scripts
#     legitimately override a computed header title. frm.doc is never touched.
#   - form's own custom_call_summary field + report view cell: same per-document
#     docfield-copy trick as the Status field (df.formatter set directly, never
#     delegating to frappe.form.formatters.Data - that reaches _apply_custom_formatter,
#     which calls this very formatter again, the identical recursion trap already
#     documented for Select).
# NOT coverable client-side, left showing "Unknown"/the raw stored suffix: Quick Entry
# / awesomplete search-dropdown results (Frappe's link-search renders straight from
# the server's get_title/description cache, no formatter hook reachable from a Client
# Script) and any raw export/API read of custom_call_summary.
CALL_LOG_STATUS_LABEL_FORM_SCRIPT = "Call Log Status Label"
_CALL_LOG_STATUS_LABEL_RULE_JS = (
	"// Display-only: WhatsApp rows stored as \"Unknown\" are LABELLED \"Missed\" (status)\n"
	"// and their title's trailing \"- Unknown\" reads \"- Missed\" (custom_call_summary).\n"
	"// Neither the stored status nor the stored custom_call_summary is ever written to.\n"
	"const splinh_call_log_is_whatsapp_unknown = (doc, status) => {\n"
	"\tconst s = status === undefined ? doc && doc.status : status;\n"
	"\treturn !!(doc && doc.custom_source === \"WhatsApp\" && s === \"Unknown\");\n"
	"};\n"
	"const splinh_call_log_status_label = (doc, value) => {\n"
	"\treturn splinh_call_log_is_whatsapp_unknown(doc, value) ? \"Missed\" : null;\n"
	"};\n"
	"// custom_call_summary is built server-side by _whatsapp_summary() and, for a\n"
	"// WhatsApp+Unknown row, always ends in the literal suffix \"- Unknown\" - only\n"
	"// that trailing suffix is swapped; the rest of the text is passed through as-is.\n"
	"const splinh_call_log_title_label = (doc, value) => {\n"
	"\tif (!splinh_call_log_is_whatsapp_unknown(doc)) return null;\n"
	"\tconst title = value === undefined ? doc && doc.custom_call_summary : value;\n"
	"\tif (typeof title !== \"string\" || !title.endsWith(\"- Unknown\")) return null;\n"
	"\treturn title.slice(0, -\"- Unknown\".length) + \"- Missed\";\n"
	"};\n"
)
CALL_LOG_STATUS_LABEL_LIST_JS = (
	"\n"
	+ _CALL_LOG_STATUS_LABEL_RULE_JS
	+ "(() => {\n"
	"\tconst settings = frappe.listview_settings[\"Call Log\"];\n"
	"\tsettings.add_fields = Array.from(new Set([...(settings.add_fields || []), \"status\", \"custom_source\"]));\n"
	"\tsettings.get_indicator = (doc) => {\n"
	"\t\tconst label = splinh_call_log_status_label(doc);\n"
	"\t\t// Colour kept as for the raw value (gray); filter kept on the RAW value.\n"
	"\t\tif (label) return [__(label), \"gray\", \"status,=,Unknown\"];\n"
	"\t\t// undefined -> frappe.get_indicator's stock status/guess_colour fallback.\n"
	"\t};\n"
	"\tsettings.formatters = settings.formatters || {};\n"
	"\tsettings.formatters.status = (value, df, doc) =>\n"
	"\t\tfrappe.utils.escape_html(__(splinh_call_log_status_label(doc, value) || value || \"\"));\n"
	"\t// list_view.js's get_subject_text() writes this straight into textContent (not\n"
	"\t// innerHTML), unlike the ordinary column path above - no escape_html here, or a\n"
	"\t// literal \"&amp;\" etc. would show for any title containing one of those chars.\n"
	"\tsettings.formatters.custom_call_summary = (value, df, doc) =>\n"
	"\t\tsplinh_call_log_title_label(doc, value) || value;\n"
	"\t// This script is re-evaluated on every meta load and the settings object is\n"
	"\t// reused, so never wrap our own wrapper (the chain would grow each reload).\n"
	"\tconst prev_onload = settings.onload && settings.onload.splinh_status_label\n"
	"\t\t? settings.onload.splinh_prev_onload\n"
	"\t\t: settings.onload;\n"
	"\tsettings.onload = function (listview) {\n"
	"\t\tconst df = frappe.meta.docfield_map[\"Call Log\"] && frappe.meta.docfield_map[\"Call Log\"].status;\n"
	"\t\tif (df && !df.splinh_status_label) {\n"
	"\t\t\tdf.splinh_status_label = 1;\n"
	"\t\t\tdf.formatter = (value, _df, options, doc) => {\n"
	"\t\t\t\tconst label = splinh_call_log_status_label(doc, value);\n"
	"\t\t\t\t// NOT frappe.form.formatters.Select: it reaches _apply_custom_formatter,\n"
	"\t\t\t\t// which calls this very formatter again - infinite recursion.\n"
	"\t\t\t\treturn __(label || (value == null ? \"\" : String(value)));\n"
	"\t\t\t};\n"
	"\t\t}\n"
	"\t\t// Same trick for the title field: covers the report view cell for\n"
	"\t\t// custom_call_summary, same docfield_map mechanism as status above.\n"
	"\t\tconst summary_df =\n"
	"\t\t\tfrappe.meta.docfield_map[\"Call Log\"] &&\n"
	"\t\t\tfrappe.meta.docfield_map[\"Call Log\"].custom_call_summary;\n"
	"\t\tif (summary_df && !summary_df.splinh_title_label) {\n"
	"\t\t\tsummary_df.splinh_title_label = 1;\n"
	"\t\t\tsummary_df.formatter = (value, _df, options, doc) => {\n"
	"\t\t\t\tconst label = splinh_call_log_title_label(doc, value);\n"
	"\t\t\t\t// NOT frappe.form.formatters.Data: same recursion trap as Select above.\n"
	"\t\t\t\treturn __(label || (value == null ? \"\" : String(value)));\n"
	"\t\t\t};\n"
	"\t\t}\n"
	"\t\tif (prev_onload) return prev_onload.call(this, listview);\n"
	"\t};\n"
	"\tsettings.onload.splinh_status_label = 1;\n"
	"\tsettings.onload.splinh_prev_onload = prev_onload;\n"
	"})();\n"
)
CALL_LOG_STATUS_LABEL_FORM_JS = (
	"// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.\n"
	+ _CALL_LOG_STATUS_LABEL_RULE_JS
	+ "// The header indicator is normally handled by the \"Call Log List Settings\"\n"
	"// get_indicator, but this form script also sets it directly below (belt and\n"
	"// braces for a tab whose Call Log meta was cached before that list script\n"
	"// applied - see the comment further down). This relabels the read-only Status\n"
	"// field in the form body (display formatter on this form's docfield copy -\n"
	"// frm.doc.status is never touched).\n"
	"frappe.ui.form.on(\"Call Log\", {\n"
	"\trefresh(frm) {\n"
	"\t\tconst field = frm.fields_dict.status;\n"
	"\t\tif (field) {\n"
	"\t\t\tfield.df.formatter = (value, df, options, doc) => {\n"
	"\t\t\t\tconst label = splinh_call_log_status_label(doc || frm.doc, value);\n"
	"\t\t\t\t// Same output as the stock Select formatter, without its recursion path.\n"
	"\t\t\t\treturn __(label || (value == null ? \"\" : String(value)));\n"
	"\t\t\t};\n"
	"\t\t\tfrm.refresh_field(\"status\");\n"
	"\t\t}\n"
	"\t\t// Same trick for the read-only custom_call_summary field in the form body -\n"
	"\t\t// frm.doc.custom_call_summary is never touched.\n"
	"\t\tconst summary_field = frm.fields_dict.custom_call_summary;\n"
	"\t\tif (summary_field) {\n"
	"\t\t\tsummary_field.df.formatter = (value, df, options, doc) => {\n"
	"\t\t\t\tconst label = splinh_call_log_title_label(doc || frm.doc, value);\n"
	"\t\t\t\t// NOT frappe.form.formatters.Data: same recursion trap noted above.\n"
	"\t\t\t\treturn __(label || (value == null ? \"\" : String(value)));\n"
	"\t\t\t};\n"
	"\t\t\tfrm.refresh_field(\"custom_call_summary\");\n"
	"\t\t}\n"
	"\t\t// The big form header/title area and the browser tab title read\n"
	"\t\t// frm.doc[title_field] directly (toolbar.js's set_title()) - no formatter\n"
	"\t\t// hook exists for either, so they're overridden here after Frappe's own\n"
	"\t\t// refresh_header/toolbar.refresh() have already run and set them from the\n"
	"\t\t// raw stored value. frm.doc itself is never written to.\n"
	"\t\tconst header_label = splinh_call_log_title_label(frm.doc);\n"
	"\t\tif (header_label && frm.page) {\n"
	"\t\t\tfrm.page.set_title(__(header_label));\n"
	"\t\t\tfrappe.utils.set_title(__(header_label) + \" - \" + frm.docname);\n"
	"\t\t}\n"
	"\t\t// The top-of-page indicator badge is normally set by the \"Call Log List\n"
	"\t\t// Settings\" get_indicator (frappe.get_indicator -> listview_settings),\n"
	"\t\t// but that only runs in this browser tab once frappe.model.init_doctype\n"
	"\t\t// has (re)applied this doctype's __custom_list_js - which with_doctype()\n"
	"\t\t// skips whenever Call Log's meta is already cached in locals.DocType\n"
	"\t\t// (e.g. a tab opened before/without this fix), leaving a stale badge\n"
	"\t\t// indefinitely even though the mechanism is otherwise correct. Overridden\n"
	"\t\t// here after Frappe's own toolbar.set_indicator() has already run, the\n"
	"\t\t// same after-the-fact pattern as frm.page.set_title() above - this fires\n"
	"\t\t// on every single-document form load regardless of listview_settings\n"
	"\t\t// timing in that tab. frm.doc.status is never touched.\n"
	"\t\tconst indicator_label = splinh_call_log_status_label(frm.doc);\n"
	"\t\tif (indicator_label && frm.page) {\n"
	"\t\t\tfrm.page.set_indicator(__(indicator_label), \"gray\");\n"
	"\t\t}\n"
	"\t},\n"
	"});\n"
)


def _ensure_call_tracking_display():
	"""List-view readability + Lead/Customer Connections (2026-09-24).

	`custom_call_summary` (populated per-record by _resolve_linked_party /
	_build_call_log_values in api/call_tracking.py) becomes the title_field, so
	it - not the raw sync id (`M-{device_id}-{device_call_id}`) - is
	what shows in the list view's bold Subject column and everywhere Call Log
	is shown as a link.

	Corrected 2026-09-24: hide_name_column is NOT a DocType/Property Setter
	property at all, despite an earlier version of this function writing one -
	list_view.js:465 reads it from `this.settings`, which comes from
	`frappe.listview_settings["Call Log"]` (a client-side object populated from
	either a bundled `[doctype]_list.js` file or, as used here, a List-type
	Client Script) - never from doc meta. That Property Setter was silently
	inert: it saved real data but nothing in the JS ever reads a property by
	that name, which is why the raw-id column kept showing regardless of a hard
	refresh. Found by checking real production data after this shipped, not
	assumed correct from the write succeeding.
	"""
	_set_property("Call Log", None, "title_field", "custom_call_summary", "Data", for_doctype=True)
	_cleanup_ineffective_hide_name_column_property_setter()
	_upsert(
		"Client Script",
		"Call Log List Settings",
		{
			"dt": "Call Log",
			"view": "List",
			"enabled": 1,
			"script": (
				"// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.\n"
				"// Hides the raw sync-id column list_view.js otherwise adds whenever title_field\n"
				'// isn\'t "name" - the correct mechanism (frappe.listview_settings), not a\n'
				"// Property Setter, which this doctype's JS never reads for this key.\n"
				'frappe.listview_settings["Call Log"] = frappe.listview_settings["Call Log"] || {};\n'
				'frappe.listview_settings["Call Log"].hide_name_column = true;\n'
				+ CALL_LOG_STATUS_LABEL_LIST_JS
			),
		},
	)
	_upsert(
		"Client Script",
		CALL_LOG_STATUS_LABEL_FORM_SCRIPT,
		{
			"dt": "Call Log",
			"view": "Form",
			"enabled": 1,
			"script": CALL_LOG_STATUS_LABEL_FORM_JS,
		},
	)
	# `type` (Incoming/Outgoing) is a stock field, not a Custom Field, so this
	# goes through _set_property (Property Setter), not _set_custom_field_props
	# (which only touches fields that are already Custom Field records).
	_set_property("Call Log", "type", "in_list_view", "1", "Check")
	_set_custom_field_props("Call Log", "custom_sim_slot", {"in_list_view": 1})


def _cleanup_ineffective_hide_name_column_property_setter():
	"""Remove the dead Property Setter row from the first (wrong) attempt at
	this - see _ensure_call_tracking_display's docstring. Harmless to leave,
	but dead config that looks load-bearing and isn't is exactly the kind of
	thing that wastes someone's time investigating it later.
	"""
	frappe.db.delete(
		"Property Setter",
		{"doc_type": "Call Log", "property": "hide_name_column", "doctype_or_field": "DocType"},
	)


# ------------------------------------------------ Call Recording player (2026-09-30)

# The recording itself is already stored correctly - a private Frappe File attached
# via save_file("Call Log", call_log_name, is_private=1) in receive_call_recording()
# (api/call_tracking.py) - this is purely a display layer on top of that, same as
# the WhatsApp status-label work above. Frappe has no stock field type for "play
# whatever file happens to be attached to this record": an Attach field only shows
# a file uploaded through that field's own widget, not an out-of-band attachment
# like this one - hence a small Client Script (Tier 2), not a Property Setter.
CALL_RECORDING_PLAYER_FIELD = {
	"fieldname": "custom_call_recording_player",
	"label": "Call Recording",
	"fieldtype": "HTML",
	"insert_after": "duration",
}

CALL_RECORDING_PLAYER_SCRIPT = "Call Log Recording Player"
CALL_RECORDING_PLAYER_JS = (
	"// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.\n"
	"// Renders whatever recording File is attached to this Call Log (attached_to_doctype/\n"
	"// attached_to_name - see receive_call_recording() in api/call_tracking.py) as an\n"
	"// inline audio player, instead of relying on the generic Attachments panel link.\n"
	"frappe.ui.form.on(\"Call Log\", {\n"
	"\trefresh(frm) {\n"
	"\t\tconst field = frm.get_field(\"custom_call_recording_player\");\n"
	"\t\tif (!field) return;\n"
	"\t\tif (frm.is_new()) {\n"
	"\t\t\tfield.$wrapper.empty();\n"
	"\t\t\treturn;\n"
	"\t\t}\n"
	"\t\tfield.$wrapper.html('<div class=\"text-muted\">Loading recording...</div>');\n"
	"\t\tfrappe.call({\n"
	"\t\t\tmethod: \"frappe.client.get_list\",\n"
	"\t\t\targs: {\n"
	"\t\t\t\tdoctype: \"File\",\n"
	"\t\t\t\tfilters: {attached_to_doctype: \"Call Log\", attached_to_name: frm.doc.name},\n"
	"\t\t\t\tfields: [\"file_url\", \"file_name\"],\n"
	"\t\t\t\torder_by: \"creation desc\",\n"
	"\t\t\t\tlimit_page_length: 1,\n"
	"\t\t\t},\n"
	"\t\t}).then((r) => {\n"
	"\t\t\t// Guard against a stale response landing after the user has already\n"
	"\t\t\t// navigated to a different Call Log (frm.get_field looks up the CURRENT\n"
	"\t\t\t// form's field, so this is only wrong if the field itself is gone).\n"
	"\t\t\tif (!frm.get_field(\"custom_call_recording_player\")) return;\n"
	"\t\t\tconst file = (r.message || [])[0];\n"
	"\t\t\tif (file && file.file_url) {\n"
	"\t\t\t\tfield.$wrapper.html(\n"
	"\t\t\t\t\t`<audio controls style=\"width:100%\" src=\"${frappe.utils.escape_html(file.file_url)}\"></audio>` +\n"
	"\t\t\t\t\t`<div class=\"text-muted small\" style=\"margin-top:4px;\">${frappe.utils.escape_html(file.file_name || \"\")}</div>`\n"
	"\t\t\t\t);\n"
	"\t\t\t} else {\n"
	"\t\t\t\tfield.$wrapper.html('<div class=\"text-muted\">No recording available</div>');\n"
	"\t\t\t}\n"
	"\t\t});\n"
	"\t},\n"
	"});\n"
)


def _ensure_call_recording_player():
	name = f"Call Log-{CALL_RECORDING_PLAYER_FIELD['fieldname']}"
	if not frappe.db.exists("Custom Field", name):
		frappe.get_doc(
			{"doctype": "Custom Field", "dt": "Call Log", "is_system_generated": 0, **CALL_RECORDING_PLAYER_FIELD}
		).insert(ignore_permissions=True)
	_upsert(
		"Client Script",
		CALL_RECORDING_PLAYER_SCRIPT,
		{
			"dt": "Call Log",
			"view": "Form",
			"enabled": 1,
			"script": CALL_RECORDING_PLAYER_JS,
		},
	)


# -------------------------------------------------- Call Log phone matching

# Lead has three raw phone-ish fields (see stock get_lead_with_phone_number's
# or_filters); Contact's numbers live one-per-row on the child "Contact Phone"
# table instead. Each gets its own normalized ("last 10 digits, non-digit
# stripped") Custom Field, indexed via search_index so an exact-match lookup
# can use a real index - never stock's unindexed LIKE '%number'. See
# custom/call_log_phone_matching.py for the normalizer and the doc_events
# hooks (hooks.py) that keep these in sync on every Lead/Contact save, and
# api/call_tracking.py for where the pre-insert lookup uses them.
#
# Chosen over a MariaDB generated/virtual indexed column (this bench runs
# 10.11.14, which does support secondary indexes on virtual columns) because:
#   - Lead alone needs three separate normalized values (phone, mobile_no,
#     whatsapp_no) - a generated column would still need three separate
#     columns, so it does not reduce the number of fields to manage either way.
#   - A stored Custom Field is created, tracked, and synced entirely through
#     Frappe's own DocField/Custom Field machinery - the exact mechanism this
#     file already uses for every other field on Call Log/Lead/Contact, and
#     the one guaranteed to survive `bench migrate` and a periodic
#     restore-from-prod-backup (this module's whole reason to exist, per its
#     own docstring). A raw `GENERATED ALWAYS AS (...) VIRTUAL` column added
#     via manual ALTER TABLE would not be represented by any DocField/Custom
#     Field record at all, so nothing in Frappe's schema sync would know it
#     exists - there is no precedent anywhere in this codebase for an
#     untracked raw column, and no way to confirm it would survive whatever
#     schema operation Frappe performs on a future Customize Form change to
#     either doctype.
LEAD_PHONE_LAST10_FIELDS = [
	{
		"fieldname": "custom_phone_last10",
		"label": "Phone (last 10 digits, normalized)",
		"fieldtype": "Data",
		"insert_after": "phone",
		"hidden": 1,
		"read_only": 1,
		"no_copy": 1,
		"search_index": 1,
	},
	{
		"fieldname": "custom_mobile_last10",
		"label": "Mobile No (last 10 digits, normalized)",
		"fieldtype": "Data",
		"insert_after": "mobile_no",
		"hidden": 1,
		"read_only": 1,
		"no_copy": 1,
		"search_index": 1,
	},
	{
		"fieldname": "custom_whatsapp_last10",
		"label": "WhatsApp No (last 10 digits, normalized)",
		"fieldtype": "Data",
		"insert_after": "whatsapp_no",
		"hidden": 1,
		"read_only": 1,
		"no_copy": 1,
		"search_index": 1,
	},
]

CONTACT_PHONE_LAST10_FIELD = {
	"fieldname": "custom_last10",
	"label": "Last 10 digits (normalized)",
	"fieldtype": "Data",
	"insert_after": "phone",
	"hidden": 1,
	"read_only": 1,
	"no_copy": 1,
	"search_index": 1,
}


def ensure_call_log_phone_matching():
	for df in LEAD_PHONE_LAST10_FIELDS:
		name = f"Lead-{df['fieldname']}"
		if frappe.db.exists("Custom Field", name):
			continue
		frappe.get_doc({"doctype": "Custom Field", "dt": "Lead", "is_system_generated": 0, **df}).insert(
			ignore_permissions=True
		)

	name = f"Contact Phone-{CONTACT_PHONE_LAST10_FIELD['fieldname']}"
	if not frappe.db.exists("Custom Field", name):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Contact Phone",
				"is_system_generated": 0,
				**CONTACT_PHONE_LAST10_FIELD,
			}
		).insert(ignore_permissions=True)


# ------------------------------------------------------- Call Monitoring dashboard

# Layer 1 of the Call Monitoring project (2026-09-26): a Workspace of Number Cards
# and Charts against stock Call Log, one click from an Insights drill-down
# (ensure_call_monitoring_insights(), custom/call_monitoring_insights.py).
#
# Real stored `status` values on this doctype today (confirmed against live data,
# not assumed): Completed, No Answer, Busy, Unknown - plus Ringing/In
# Progress/Failed/Queued/Cancelled, which exist as options but have 0 rows so far.
# There is NO literal "Missed" stored value - "Missed" only ever exists as the
# WhatsApp+Unknown DISPLAY label from CALL_LOG_STATUS_LABEL_FORM_JS above. The
# "Missed calls" card below (a broader, ops definition: anything that isn't a
# completed conversation) and the Status Breakdown chart's "Missed" bucket (only
# the WhatsApp+Unknown display-label convention) are therefore deliberately
# DIFFERENT definitions that happen to agree on today's data (no SIM row has ever
# landed on "Unknown") - see the card/chart docstrings below for why neither is a
# bug.
CALL_MONITORING_WORKSPACE = "Call Monitoring"
CALL_MONITORING_MODULE = "SplINH"
CALL_MONITORING_SIDEBAR = "CRM"  # Call Log's own module (Telephony) has no sidebar;
# CRM already lists Lead/Opportunity/Customer, the parties a call is normally about.
CALL_MONITORING_MISSED_STATUSES = ["No Answer", "Busy", "Unknown"]
CALL_MONITORING_STATUS_REPORT = "Call Log Status Breakdown"


def _call_monitoring_week_filter():
	"""["Call Log", "start_time", ">=", <Monday 00:00 of the current week>].

	Computed fresh on every after_migrate run (frappe.utils.get_first_day_of_week,
	which honours System Settings' configured start-of-week - not hardcoded
	Monday). This is a STATIC bound, recomputed each run, not a live in-browser
	recalculation: Frappe's own "dynamic filter" mechanism for a Number Card
	(dynamic_filters_json) evaluates its value as a literal JS expression in the
	browser (frappe/public/js/frappe/utils/dashboard_utils.js get_all_filters()
	calls plain `eval()` on it) - workable with something like
	"frappe.datetime.week_start()", but that returns a moment().format() string
	WITH a timezone offset, which is a real parsing risk for a Datetime filter
	that this project's verification standard (real data, hand-checked) can't
	confirm without a browser. A static bound recomputed every migrate is simpler,
	uses the exact same MariaDB-safe format the rest of this module already
	relies on, and is still natively "adjustable": the card's own Filters section
	is a plain, editable field like any other Number Card - see splinh_setup.py
	module docstring for the resolution hierarchy this follows (native first).
	"""
	week_start = frappe.utils.get_first_day_of_week(frappe.utils.nowdate(), as_str=True)
	return ["Call Log", "start_time", ">=", week_start]


# (label suffix, extra AND-conditions beyond the "this week" bound). Every card
# also gets `_call_monitoring_week_filter()` prepended - see
# _ensure_call_monitoring_cards().
CALL_MONITORING_NATIVE_CARDS = [
	("Total calls", []),
	# The three REAL stored "not a completed conversation" statuses - see the
	# module-level note above for why this is not the same set as the Status
	# Breakdown chart's "Missed" bucket.
	("Missed calls", [["Call Log", "status", "in", CALL_MONITORING_MISSED_STATUSES]]),
	("Outgoing calls", [["Call Log", "type", "=", "Outgoing"]]),
	("Incoming calls", [["Call Log", "type", "=", "Incoming"]]),
	("WhatsApp calls", [["Call Log", "custom_source", "=", "WhatsApp"]]),
	("Phone (SIM) calls", [["Call Log", "custom_source", "=", "SIM"]]),
]


def ensure_call_monitoring_dashboard():
	"""Number Cards, Charts, a Query Report and a Workspace for Call Log.

	The Workspace itself and its cards/charts are left in place (still valid,
	still usable directly at /app/call-monitoring) - only its CRM sidebar
	shortcut is removed (2026-09-26, user's own instruction), since
	"Call Monitoring - Detailed" (`ensure_call_monitoring_detailed_page`,
	renamed to plain "Call Monitoring" the same day) has taken over that
	slot as the primary, everyday view.
	"""
	_ensure_call_log_report_permission()
	_ensure_call_monitoring_cards()
	_ensure_call_monitoring_status_report()
	_ensure_call_monitoring_charts()
	_ensure_call_monitoring_workspace()
	_remove_call_monitoring_workspace_sidebar_entry()


def _ensure_call_log_report_permission():
	"""System Manager's Custom DocPerm row on Call Log (created by
	_ensure_call_tracking_permissions) never carried `report`, only
	read/write/create/delete - Call Tracker Manager is the only role that does.
	Once ANY Custom DocPerm row exists on a doctype Frappe stops consulting the
	stock DocPerms for it entirely (documented on _ensure_call_tracking_permissions
	itself), so without this fix a System Manager who is not also Call Tracker
	Manager gets a PermissionError opening the Status Breakdown chart below -
	its Query Report backing calls frappe.has_permission("Call Log", "report")
	for whoever is viewing it (frappe/desk/query_report.py get_report_doc /
	run()). Only ever adds the flag; never removes anything else on the row.
	"""
	name = frappe.db.exists("Custom DocPerm", {"parent": "Call Log", "role": "System Manager", "permlevel": 0})
	if not name:
		return
	if not frappe.db.get_value("Custom DocPerm", name, "report"):
		frappe.db.set_value("Custom DocPerm", name, "report", 1)
		frappe.clear_cache(doctype="Call Log")


def _ensure_call_monitoring_cards():
	week_filter = _call_monitoring_week_filter()

	for suffix, extra_conditions in CALL_MONITORING_NATIVE_CARDS:
		label = f"Call Monitoring - {suffix}"
		card = _upsert(
			"Number Card",
			label,
			{
				"label": label,
				"type": "Document Type",
				"document_type": "Call Log",
				"function": "Count",
				"filters_json": json.dumps([week_filter, *extra_conditions]),
				"is_public": 1,
				"show_percentage_stats": 0,
				"module": CALL_MONITORING_MODULE,
			},
		)
		# Gotcha (a): `currency` auto-populates for a numeric aggregation and
		# silently renders a plain count as money ("Rs 1.00" instead of "1").
		if card.currency:
			frappe.db.set_value("Number Card", card.name, "currency", None, update_modified=False)

	# "Company calls": customer is set OR custom_lead is set. Confirmed live
	# (frappe/desk/doctype/number_card/number_card.py get_result(), frappe/desk/
	# doctype/number_card/number_card.json) that a plain Number Card filter is an
	# AND-only list - there is no `or_filters_json` field, and get_result() never
	# forwards anything but `filters` to frappe.get_list. So this is the one card
	# built as type="Custom" against splinh.custom.call_log_dashboard's
	# whitelisted method - see that module's docstring for the OR mechanism.
	label = "Call Monitoring - Company calls"
	card = _upsert(
		"Number Card",
		label,
		{
			"label": label,
			"type": "Custom",
			"document_type": "Call Log",
			"method": "splinh.custom.call_log_dashboard.get_company_calls_count",
			"filters_json": json.dumps([week_filter]),
			"is_public": 1,
			"show_percentage_stats": 0,
			"module": CALL_MONITORING_MODULE,
		},
	)
	if card.currency:
		frappe.db.set_value("Number Card", card.name, "currency", None, update_modified=False)


def _ensure_call_monitoring_status_report():
	"""Backs the Status Breakdown chart with a CASE-bucketed Query Report.

	Native "Group By" Dashboard Charts genuinely cannot bucket WhatsApp+Unknown
	into "Missed" - confirmed live by reading get_group_by_chart_config()
	(frappe/desk/doctype/dashboard_chart/dashboard_chart.py): `group_by_based_on`
	is passed BOTH as a raw SQL column expression and into
	frappe.get_meta(doctype).get_field(group_by_field), which requires it to be a
	real, registered fieldname - a CASE expression there is not a fieldname and
	would crash the chart, not merely group on the wrong thing. A Query Report's
	SQL has no such constraint, and chart_type="Report" (Dashboard Chart) can plot
	any two of its result columns via x_field/y_axis - confirmed live in
	chart_widget.js get_report_chart_data(), which builds the chart straight from
	a Report's own columns/rows with no need for `use_report_chart`.
	"""
	query = """SELECT
	CASE WHEN custom_source = 'WhatsApp' AND status = 'Unknown' THEN 'Missed' ELSE status END AS status_bucket,
	COUNT(*) AS count
FROM `tabCall Log`
GROUP BY status_bucket
ORDER BY count DESC"""

	report_exists = frappe.db.exists("Report", CALL_MONITORING_STATUS_REPORT)
	if report_exists:
		report = frappe.get_doc("Report", CALL_MONITORING_STATUS_REPORT)
	else:
		report = frappe.new_doc("Report")
		report.report_name = CALL_MONITORING_STATUS_REPORT
	report.ref_doctype = "Call Log"
	report.report_type = "Query Report"
	report.module = CALL_MONITORING_MODULE
	report.query = query
	report.is_standard = "No"  # Administrator without developer_mode can't save "Yes" anyway.
	report.columns = []
	report.append("columns", {"fieldname": "status_bucket", "label": "Status", "fieldtype": "Data", "width": 200})
	report.append("columns", {"fieldname": "count", "label": "Calls", "fieldtype": "Int", "width": 100})
	existing_roles = {row.role for row in report.roles}
	for role in ("System Manager", CALL_TRACKING_MANAGER_ROLE):
		if role not in existing_roles:
			report.append("roles", {"role": role})
	report.flags.ignore_permissions = True
	if report_exists:
		report.save()
	else:
		report.insert()


def _ensure_call_monitoring_charts():
	chart_names = []

	for suffix, group_by, chart_type in (
		("Incoming vs Outgoing", "type", "Donut"),
		("WhatsApp vs SIM", "custom_source", "Donut"),
	):
		label = f"Call Monitoring - {suffix}"
		chart = _upsert(
			"Dashboard Chart",
			label,
			{
				"chart_name": label,
				"chart_type": "Group By",
				"document_type": "Call Log",
				"group_by_type": "Count",
				"group_by_based_on": group_by,
				"type": chart_type,
				"filters_json": "[]",
				"is_public": 1,
				"module": CALL_MONITORING_MODULE,
			},
		)
		if chart.currency:
			frappe.db.set_value("Dashboard Chart", chart.name, "currency", None, update_modified=False)
		chart_names.append(label)

	# Status Breakdown: chart_type "Report", backed by
	# CALL_MONITORING_STATUS_REPORT so the WhatsApp+Unknown rows plot as one
	# "Missed" slice instead of a raw "Unknown" one - see
	# _ensure_call_monitoring_status_report() for why this can't be a native
	# Group By chart.
	label = "Call Monitoring - Status Breakdown"
	chart = _upsert(
		"Dashboard Chart",
		label,
		{
			"chart_name": label,
			"chart_type": "Report",
			"report_name": CALL_MONITORING_STATUS_REPORT,
			"document_type": "Call Log",
			"use_report_chart": 0,
			"x_field": "status_bucket",
			"type": "Donut",
			"filters_json": "[]",
			"is_public": 1,
			"module": CALL_MONITORING_MODULE,
		},
	)
	chart.y_axis = []
	chart.append("y_axis", {"y_field": "count", "color": "#f6c23e"})
	chart.save(ignore_permissions=True)
	if chart.currency:
		frappe.db.set_value("Dashboard Chart", chart.name, "currency", None, update_modified=False)
	chart_names.append(label)

	# Total calls per day: native Count/time-series chart on `start_time` (the
	# actual call time - Call Log also has `creation`, which is when the sync/
	# ingestion job inserted the row, not when the call happened). "Last Month"
	# is the closest native `timespan` option to "last 30 days" (the Select has
	# no literal 30-day option - Last Year/Last Quarter/Last Month/Last Week/
	# Select Date Range, confirmed on the Dashboard Chart doctype).
	label = "Call Monitoring - Total calls per day"
	chart = _upsert(
		"Dashboard Chart",
		label,
		{
			"chart_name": label,
			"chart_type": "Count",
			"document_type": "Call Log",
			"based_on": "start_time",
			"timespan": "Last Month",
			"time_interval": "Daily",
			"type": "Line",
			"filters_json": "[]",
			"is_public": 1,
			"module": CALL_MONITORING_MODULE,
		},
	)
	if chart.currency:
		frappe.db.set_value("Dashboard Chart", chart.name, "currency", None, update_modified=False)
	chart_names.append(label)

	return chart_names


def _ensure_call_monitoring_workspace():
	card_names = [f"Call Monitoring - {suffix}" for suffix, _ in CALL_MONITORING_NATIVE_CARDS]
	card_names.append("Call Monitoring - Company calls")
	chart_names = [
		"Call Monitoring - Incoming vs Outgoing",
		"Call Monitoring - WhatsApp vs SIM",
		"Call Monitoring - Status Breakdown",
		"Call Monitoring - Total calls per day",
	]

	workspace = _upsert(
		"Workspace",
		CALL_MONITORING_WORKSPACE,
		{
			"label": CALL_MONITORING_WORKSPACE,
			"title": CALL_MONITORING_WORKSPACE,
			"type": "Workspace",
			"public": 1,
			"module": CALL_MONITORING_MODULE,
			"icon": "call",
		},
	)
	workspace.number_cards = []
	workspace.charts = []
	workspace.shortcuts = []
	workspace.append(
		"shortcuts",
		{
			"type": "DocType", "link_to": "Call Log", "label": "All Calls",
			"doc_view": "List", "color": "Blue",
		},
	)
	# The block's reference is matched against the child row's *label*, not the
	# card/chart docname - they must be identical or the block renders blank
	# with no error (same gotcha documented on _ensure_work_status_dashboard).
	for name in card_names:
		workspace.append("number_cards", {"number_card_name": name, "label": name})
	for name in chart_names:
		workspace.append("charts", {"chart_name": name, "label": name})

	content = [
		{"id": "cmhdr1", "type": "header", "data": {"text": '<span class="h4"><b>Call Monitoring</b></span>', "col": 12}},
		{"id": "cmsc1", "type": "shortcut", "data": {"shortcut_name": "All Calls", "col": 6}},
		{"id": "cmhdr2", "type": "header", "data": {"text": '<span class="h4"><b>This Week</b></span>', "col": 12}},
	]
	for i, name in enumerate(card_names, start=1):
		content.append({"id": f"cmnc{i}", "type": "number_card", "data": {"number_card_name": name, "col": 4}})
	content.append(
		{"id": "cmhdr3", "type": "header", "data": {"text": '<span class="h4"><b>Breakdown</b></span>', "col": 12}}
	)
	for i, name in enumerate(chart_names, start=1):
		content.append({"id": f"cmch{i}", "type": "chart", "data": {"chart_name": name, "col": 6}})
	workspace.content = json.dumps(content)
	workspace.save(ignore_permissions=True)


def _remove_call_monitoring_workspace_sidebar_entry():
	"""Removes the CRM sidebar shortcut to the plain Number-Card Workspace
	(2026-09-26, user's own instruction) - superseded by the "Call Monitoring"
	shortcut to the Detailed page (`_ensure_call_monitoring_detailed_sidebar_entry`),
	which now occupies this same "directly under Home" slot instead. The
	Workspace itself is untouched and still reachable at /app/call-monitoring;
	only its sidebar link is gone. Idempotent both ways: safe to re-run
	whether or not the row is still there (e.g. after a fresh UAT restore
	that predates this change and would otherwise re-surface it once
	`_ensure_call_monitoring_workspace()` above re-creates the Workspace).
	"""
	if not frappe.db.exists("Workspace Sidebar", CALL_MONITORING_SIDEBAR):
		return
	sidebar = frappe.get_doc("Workspace Sidebar", CALL_MONITORING_SIDEBAR)
	before = len(sidebar.items)
	sidebar.items = [
		item for item in sidebar.items
		if not (item.link_to == CALL_MONITORING_WORKSPACE and item.link_type == "Workspace")
	]
	if len(sidebar.items) == before:
		return  # already gone - nothing to do
	for index, row in enumerate(sidebar.items, start=1):
		row.idx = index
	sidebar.save(ignore_permissions=True)


# ------------------------------------------------- Call Monitoring - Detailed (Layer 3)

CALL_MONITORING_DETAILED_PAGE = "call-mon-detail"


def ensure_call_monitoring_detailed_page():
	"""Recreate the DB `Page` record for the filterable drill-down view
	(call_mon_detail.json/.js). Named "call-mon-detail", not the fuller
	"call-monitoring-detailed" the brief originally used, because
	`Page.autoname()` (frappe/core/doctype/page/page.py) unconditionally
	truncates `page_name` to 20 characters when deriving `name` - confirmed
	live: an explicit 25-character name/page_name silently came back as
	"call-monitoring-deta" (20 chars) after insert, which then failed
	`Workspace Sidebar`'s Dynamic Link validation because nothing by that
	truncated name is what any of the fixture files ended up asking for.
	"call-mon-detail" (15 chars) is short enough to survive untouched; the
	page's `title` ("Call Monitoring - Detailed", unrestricted length) is
	what actually appears in the browser tab, Desk header and sidebar label.

	Same reasoning and pattern as
	`_ensure_issue_pipeline_page()`: the .json/.js under the app survive a
	restore, the `Page` doctype row does not.

	Real access control for this page's data is NOT this record's `roles` list -
	that only gates whether the page is reachable at all from Desk navigation/
	direct URL. The actual gate is the explicit role check at the top of
	`splinh.custom.call_monitoring_detail.get_dashboard_data`, which runs on
	every single call regardless of how the page was reached. This `roles` list
	is a second, independent layer on top of that (a user without either role
	can't even open the page to see an empty shell) - not a replacement for it.
	"""
	values = {
		"page_name": CALL_MONITORING_DETAILED_PAGE,
		"title": "Call Monitoring",
		"module": CALL_MONITORING_MODULE,
		"standard": "Yes",
		"system_page": 0,
	}
	page_exists = frappe.db.exists("Page", CALL_MONITORING_DETAILED_PAGE)
	if page_exists:
		page = frappe.get_doc("Page", CALL_MONITORING_DETAILED_PAGE)
		page.update(values)
		page.flags.ignore_permissions = True
		page.flags.ignore_mandatory = True
		page.save()
	else:
		page = frappe.get_doc({"doctype": "Page", "name": CALL_MONITORING_DETAILED_PAGE, **values})
		# See _ensure_issue_pipeline_page()'s comment on ignore_validate: this
		# site does not run with developer_mode on, so a brand-new standard Page
		# would otherwise fail Page.validate()'s "Not in Developer Mode" check.
		page.flags.ignore_validate = True
		page.flags.ignore_permissions = True
		page.flags.ignore_mandatory = True
		page.insert()
	existing_roles = {row.role for row in page.roles}
	changed = False
	for role in ("System Manager", CALL_TRACKING_MANAGER_ROLE):
		if role not in existing_roles:
			page.append("roles", {"role": role})
			changed = True
	if changed:
		page.save(ignore_permissions=True)

	_ensure_call_monitoring_detailed_sidebar_entry()


def _ensure_call_monitoring_detailed_sidebar_entry():
	"""One shortcut, in the CRM sidebar only - pinned directly under "Home"
	(2026-09-26: moved into that slot, and relabelled plain "Call Monitoring",
	once the old Number-Card Workspace's own shortcut there was removed - see
	_remove_call_monitoring_workspace_sidebar_entry(). This page has taken
	over that "primary, everyday view" position and name; the Workspace
	itself is untouched, just no longer linked from here.

	Deliberately not added anywhere else beyond this one slot: the brief for
	this page was explicit that further placement (e.g. also under Support,
	or a second copy elsewhere) should be confirmed with the user rather than
	guessed.
	"""
	if not frappe.db.exists("Workspace Sidebar", CALL_MONITORING_SIDEBAR):
		return
	sidebar = frappe.get_doc("Workspace Sidebar", CALL_MONITORING_SIDEBAR)
	existing = next(
		(item for item in sidebar.items if item.link_to == CALL_MONITORING_DETAILED_PAGE and item.link_type == "Page"),
		None,
	)
	if existing:
		if existing.label != "Call Monitoring":
			existing.label = "Call Monitoring"
			sidebar.save(ignore_permissions=True)
		return
	sidebar.append(
		"items",
		{
			"label": "Call Monitoring",
			"type": "Link",
			"link_type": "Page",
			"link_to": CALL_MONITORING_DETAILED_PAGE,
			"icon": "call",
		},
	)
	rows = sidebar.items
	rows.insert(1, rows.pop())  # sit directly under "Home"
	for index, row in enumerate(rows, start=1):
		row.idx = index
	sidebar.save(ignore_permissions=True)


# --------------------------------------------------------- User change notifications

USER_CHANGE_SAVE_NOTIFICATION = "User Change Notification - Save"
USER_CHANGE_DELETE_NOTIFICATION = "User Change Notification - Delete"


def ensure_user_change_notifications():
	"""Notify Administrator (in-app bell) whenever a User record is created,
	edited, or deleted (2026-09-26).

	Replaces an earlier same-day system-wide "*" wildcard doc_events hook
	(custom/global_change_notifications.py, now removed) that fired for every
	doctype. Scope was narrowed to just User, which stock Frappe's no-code
	Notification doctype now covers cleanly - no custom code required:

	  - A single "Save" event Notification covers both create and edit.
	    `Document.insert()` calls `run_post_save_methods()` (which fires
	    on_update) as part of the SAME insert call that fires after_insert -
	    confirmed by reading frappe/model/document.py directly - so a "Save"
	    alert fires exactly once per real write, on creation and on every
	    edit alike. Deliberately NOT also registering a "New" event alongside
	    it: that would double-fire (2 separate Notification Log rows) on
	    every single creation.
	  - Deletion has no dedicated "Delete" named event on the Notification
	    doctype, but is cleanly covered by the generic "Method" event with
	    Trigger Method = "on_trash": frappe.model.delete_doc.delete_doc()
	    calls doc.run_method("on_trash") - with the row still fully present
	    in the database - before the row is actually removed, and
	    Document.run_notifications() dispatches "Method"-event alerts for
	    any method name, not just the four named-event mappings. Confirmed
	    by reading frappe/model/document.py and frappe/model/delete_doc.py.
	  - Receiver is Role = "Administrator", not a hardcoded email/cc value:
	    frappe.core.doctype.role.role.get_info_based_on_role() explicitly
	    special-cases role == "Administrator" and resolves it straight to
	    the real Administrator user's configured email - exactly the
	    name-vs-email mismatch the old custom hook had to patch around by
	    hand (frappe.db.get_value("User", "Administrator", "email")).

	Known, accepted gotcha: several core flows create a User and then
	immediately call .add_roles(...), which does its own internal .save().
	A single "add a user with these roles" action from a person's
	perspective can therefore legitimately produce 2 Save-event
	notifications (one from the insert's own on_update, one from the
	follow-up role-assignment save). This is expected, not a bug to fix.
	"""
	_upsert(
		"Notification",
		USER_CHANGE_SAVE_NOTIFICATION,
		{
			"document_type": "User",
			"event": "Save",
			"channel": "System Notification",
			"enabled": 1,
			"subject": "User saved: {{ doc.full_name }} ({{ doc.name }})",
			"message": (
				"<p>User <b>{{ doc.full_name }}</b> ({{ doc.name }}) was saved "
				"by {{ doc.modified_by }} on {{ doc.modified }}.</p>"
			),
			"recipients": [{"receiver_by_role": "Administrator"}],
		},
	)
	_upsert(
		"Notification",
		USER_CHANGE_DELETE_NOTIFICATION,
		{
			"document_type": "User",
			"event": "Method",
			"method": "on_trash",
			"channel": "System Notification",
			"enabled": 1,
			"subject": "User deleted: {{ doc.full_name }} ({{ doc.name }})",
			"message": (
				"<p>User <b>{{ doc.full_name }}</b> ({{ doc.name }}) was deleted. "
				"Last modified by {{ doc.modified_by }}.</p>"
			),
			"recipients": [{"receiver_by_role": "Administrator"}],
		},
	)
