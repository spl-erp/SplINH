# Adds Call Log to a Customer's own Connections tab. Additive only - same
# contract as lead_dashboard.py in this directory (Frappe passes in the stock
# customer_dashboard.get_data() result as `data`; mutate and return it, never
# replace it, or the existing Pre Sales/Orders/Payments/Support/Projects
# sections would silently disappear).
#
# No non_standard_fieldnames entry needed here: Call Log already ships a stock
# "customer" Link field (unlike Lead, which needed a new field), so Frappe's
# own connection-detection finds it automatically by name.
from frappe import _


def get_data(data):
	data.setdefault("transactions", []).append({"label": _("Calls"), "items": ["Call Log"]})
	return data
