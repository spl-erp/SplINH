# Adds Call Log to a Lead's own Connections tab. Additive only - Frappe calls
# the stock erpnext.crm.doctype.lead.lead_dashboard.get_data() FIRST and passes
# its result in as `data` (confirmed live from frappe/model/meta.py's
# Meta.get_dashboard_data), so mutating and returning `data` here is the only
# valid contract - replacing it would silently drop Opportunity/Quotation/
# Prospect, which are already there.
#
# Call Log has no field literally named "lead" (only the stock "custom_lead"
# link we added for exactly this purpose - see
# splinh.custom.splinh_setup.ensure_call_tracking_display), so
# non_standard_fieldnames is required here - the same mechanism the stock
# lead_dashboard.py already uses for Quotation/Opportunity's own party_name.
from frappe import _


def get_data(data):
	data.setdefault("transactions", []).append({"label": _("Calls"), "items": ["Call Log"]})
	data.setdefault("non_standard_fieldnames", {})["Call Log"] = "custom_lead"
	return data
