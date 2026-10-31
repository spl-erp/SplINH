app_name = "splinh"
app_title = "SplINH"
app_publisher = "Splashjet Ink"
app_description = "Call tracking, monitoring, phone matching, manager review, and the Issue Tracker workflow - extracted from the Sigzen-maintained splashjet app, maintained independently from here on."
app_email = "support@splashjetink.com"
app_license = "mit"

doc_events = {
	# Sales Scorecard (doctype built in the UI): targets / Ach % on save, live actuals when opened.
	"Sales Scorecard": {
		"validate": "splinh.custom.scorecard.on_validate",
		"onload": "splinh.custom.scorecard.on_load",
	},
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
		# on_update: refresh the Call Log summary when the remark is changed in the ERP form;
		# validate fill_caller_name: the name the Activity call card shows (custom/call_remark_sync.py).
		"on_update": "splinh.custom.call_remark_sync.on_call_log_update",
		"validate": [
			"splinh.custom.call_log_manager_review.sync_manager_remarked_flag",
			"splinh.custom.call_review.stamp_erp_review",
			"splinh.custom.call_remark_sync.fill_caller_name",
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
	"daily": [
		"splinh.custom.call_tracker_auth.expire_unused_tokens",
		# every eligible person has this month's Sales Scorecard (the month rollover copies targets)
		"splinh.custom.scorecard.ensure_current_month_scorecards",
	],
	# live actual call counts and Ach % on this month's scorecards (custom/scorecard.py)
	"hourly": ["splinh.custom.scorecard.refresh_current_month_scorecards"],
}

# Re-apply local customisations that live as database records (Custom Fields,
# Server/Client Scripts, Property Setters, Workspaces...) on every migrate -
# idempotent and swallows its own errors so it can never break a migration.
after_migrate = ["splinh.custom.splinh_setup.execute"]

# bench-level commands, e.g. `bench --site <site> export-doctype "Lead"`.
commands = ["splinh.commands"]

# Waba (the WhatsApp app) asks these functions who a phone number belongs to. See splinh/waba_bridge.py.
waba_party_matchers = ["splinh.waba_bridge.match_party"]
# ...and every record that holds the number (a person can be a Lead and a Contact at once).
waba_party_all = ["splinh.waba_bridge.match_all_parties"]
# ...and who looks after that customer (the conversation goes to them first), and the customer's language.
waba_record_owner = ["splinh.waba_bridge.record_owner"]
# ...and whether the record already says "do not contact" (Unsubscribed / Do Not Disturb), which blocks WhatsApp templates too.
waba_opt_out = ["splinh.waba_bridge.opt_out"]
