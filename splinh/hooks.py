app_name = "splinh"
app_title = "SplINH"
app_publisher = "Splashjet Ink"
app_description = "Call tracking, monitoring, phone matching, manager review, and the Issue Tracker workflow - extracted from the Sigzen-maintained splashjet app, maintained independently from here on."
app_email = "support@splashjetink.com"
app_license = "mit"

# Keeps the normalized/indexed phone-matching columns in sync - see
# custom/call_log_phone_matching.py for why (stock Call Log's before_insert
# phone match is an unindexed, dash-sensitive LIKE '%number').
doc_events = {
	"Lead": {
		"validate": "splinh.custom.call_log_phone_matching.set_lead_phone_last10"
	},
	"Contact": {
		"validate": "splinh.custom.call_log_phone_matching.set_contact_phone_last10"
	},
	# Manager-review fields - see custom/call_log_manager_review.py and
	# _ensure_call_manager_review_fields() in custom/splinh_setup.py.
	"Call Log": {
		"validate": "splinh.custom.call_log_manager_review.sync_manager_remarked_flag"
	},
}

# Surfaces linked Call Log entries on a Lead/Customer's own Connections tab -
# see override/lead_dashboard.py and customer_dashboard.py for why each needs
# a different fieldname mapping.
override_doctype_dashboards = {
	"Lead": "splinh.override.lead_dashboard.get_data",
	"Customer": "splinh.override.customer_dashboard.get_data",
}

# Re-apply local customisations that live as database records (Custom Fields,
# Server/Client Scripts, Property Setters, Workspaces...) on every migrate -
# idempotent and swallows its own errors so it can never break a migration.
after_migrate = ["splinh.custom.splinh_setup.execute"]

# bench-level commands, e.g. `bench --site <site> export-doctype "Lead"`.
commands = ["splinh.commands"]
