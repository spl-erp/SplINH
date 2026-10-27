"""Remove the DILO dummy data made on 24 Oct 2026 for erp@splashjetink.com (run only when asked).

  cd /home/frappe/frappe-bench/sites
  /home/frappe/frappe-bench/env/bin/python /home/frappe/frappe-bench/apps/splinh/Dump/cleanup_dilo_dummy_data.py

Deletes the Call Logs named M-dilotest-* (owner erp@splashjetink.com, summary 'DILO TEST DATA - safe to delete') and
resets the user-entered/auto fields of that scorecard's day rows. Touches nothing else."""
import logging
import frappe
_orig = frappe.logger  # logs/cssutils.log belongs to the service user; a plain logger avoids a PermissionError
frappe.logger = lambda module=None, *a, **k: logging.getLogger("cssutils") if module == "cssutils" else _orig(module, *a, **k)
frappe.init(site="erp.splashjetink.com", sites_path="/home/frappe/frappe-bench/sites"); frappe.connect()
NAME = "erp@splashjetink.com-2026-10-01"
n = frappe.db.sql("select count(*) from `tabCall Log` where name like 'M-dilotest-%%' and owner='erp@splashjetink.com' and custom_call_summary='DILO TEST DATA - safe to delete'")[0][0]
frappe.db.sql("delete from `tabCall Log` where name like 'M-dilotest-%%' and owner='erp@splashjetink.com' and custom_call_summary='DILO TEST DATA - safe to delete'")
frappe.db.sql("""update `tabSales Scorecard Day` set no_target=0, plan_new=0, plan_followup=0, actual_new=0, actual_followup=0, ach_new=0, ach_followup=0,
  orders_expected=0, orders_received=0, orders_ach=0, order_amount_expected=0, order_amount_received=0, order_amount_ach=0,
  payments_expected=0, payments_received=0, payments_ach=0, remarks=NULL, plan_saved_at=NULL, eod_saved_at=NULL
  where parent=%s""", (NAME,))
frappe.db.commit()
from splinh.custom import scorecard as sc
sc.refresh_scorecard(NAME); frappe.db.commit()
print("deleted dummy Call Logs:", n, "| scorecard reset")
frappe.destroy()
