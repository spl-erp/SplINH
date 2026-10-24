app_name = "splinh"
app_title = "SplINH"
app_publisher = "Splashjet Ink"
app_description = "Call tracking, monitoring, phone matching, manager review, and the Issue Tracker workflow - extracted from the Sigzen-maintained splashjet app, maintained independently from here on."
app_email = "support@splashjetink.com"
app_license = "mit"

doc_events = {
	# Per-user "Record Calls" toggle: opens/closes a pause period only when that one field
	# changes (custom/call_recording_pause.py).
	"User": {"on_update": "splinh.custom.call_recording_pause.track_recording_toggle"},
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
	# stamp_erp_review: who/when/where a review was made when it is edited in the ERP form
	# (custom/call_review.py), so the app never silently overwrites it.
	"Call Log": {
		# on_update: copy the remark to the Lead's Notes / the Customer's Comments when it (or the
		# Lead/Customer link) is changed in the ERP form (custom/call_remark_sync.py).
		"on_update": "splinh.custom.call_remark_sync.on_call_log_update",
		"validate": [
			"splinh.custom.call_log_manager_review.sync_manager_remarked_flag",
			"splinh.custom.call_review.stamp_erp_review",
		]
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

# Call Tracker device tokens (custom/call_tracker_auth.py). enforce_token_scope returns at once
# unless the request carries `Frappe-Authorization-Source: Call Tracker Token`; for those it limits
# the token to the Call Tracker endpoints. The daily job disables tokens unused for N days.
auth_hooks = ["splinh.custom.call_tracker_auth.enforce_token_scope"]

scheduler_events = {
	"daily": ["splinh.custom.call_tracker_auth.expire_unused_tokens"],
}

# Re-apply local customisations that live as database records (Custom Fields,
# Server/Client Scripts, Property Setters, Workspaces...) on every migrate -
# idempotent and swallows its own errors so it can never break a migration.
after_migrate = ["splinh.custom.splinh_setup.execute"]

# bench-level commands, e.g. `bench --site <site> export-doctype "Lead"`.
commands = ["splinh.commands"]
