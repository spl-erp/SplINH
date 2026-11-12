__version__ = '0.1.0'


# Let Call Tracker token requests skip the user's restrict_ip list again (Frappe 16.51 started to
# enforce it for Authorization-header requests). Installed here, at package import, because hooks.py is
# imported only on an `app_hooks` cache miss; same approach as sigzensfa/__init__.py. Idempotent.
# See splinh/custom/call_tracker_ip.py.
def _noop_patch(*args, **kwargs):
	pass


try:
	from splinh.custom.call_tracker_ip import patch_validate_ip_address
except Exception:
	patch_validate_ip_address = _noop_patch

try:
	patch_validate_ip_address()
except Exception:
	pass  # never stop the app from loading because of this patch
