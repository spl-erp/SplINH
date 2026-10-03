app_name = "splinh"
app_title = "SplINH"
app_publisher = "Splashjet Ink"
app_description = "Call tracking, monitoring, phone matching, manager review, and the Issue Tracker workflow - extracted from the Sigzen-maintained splashjet app, maintained independently from here on."
app_email = "support@splashjetink.com"
app_license = "mit"

doc_events = {
	# Phone Lookup (indexed number -> Lead/Contact table) - see custom/phone_lookup.py.
	"Contact": {
		"on_update": "splinh.custom.phone_lookup.sync_document",
		"on_trash": "splinh.custom.phone_lookup.delete_document",
	},
	"Lead": {
		"on_update": "splinh.custom.phone_lookup.sync_document",
		"on_trash": "splinh.custom.phone_lookup.delete_document",
	},
	# Manager-review fields - see custom/call_log_manager_review.py and
	# _ensure_call_manager_review_fields() in custom/splinh_setup.py.
	"Call Log": {
		"validate": "splinh.custom.call_log_manager_review.sync_manager_remarked_flag"
	},
}

# Skip stock Call Log.before_insert's two unindexed phone scans for inserts that
# already resolved their party - see override/call_log.py.
override_doctype_class = {
	"Call Log": "splinh.override.call_log.SplinhCallLog",
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
