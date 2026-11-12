# ============================================================================
# Server Script "Call Tracker Sync Alert"
#   Script Type: Scheduler Event      Event Frequency: Cron      Cron Format: */15 10-19 * * 1-6
#   (every 15 min, 10:00-19:59, Mon-Sat, server time).
# Mails the user's own Team Lead (Export only while DOMESTIC_ON is False) when a Call Tracker phone has
# not uploaded for STALE_HOURS. DRY_RUN = True only writes "SYNC ALERT DRY RUN" to the Error Log.
# TEST_TO = your own mail sends every mail there. LIVE since 2026-10-27.
# Needs the custom field on User: custom_inh_last_alert (Datetime, read only).
# Sandbox: no tuple unpacking, functions cannot see module-level names, so logic is inline.
# ============================================================================
DRY_RUN = False
TEST_TO = ""
STALE_HOURS = 2
EXCLUDE = ["dummy@splashjetink.com", "temp@gmail.com", "erp@splashjetink.com"]
DOMESTIC_ON = False  # Domestic (non-Export) alerts are paused; True sends them to the National Sales Head
EXPORT_HEAD = "Exports Sales Head"
NATIONAL_HEAD = "National Sales Head"


# ============================================================================
# Server Script "Call Tracker Sync Alert"
#   Script Type: Scheduler Event      Event Frequency: Cron      Cron Format: */15 10-19 * * 1-6
#   (every 15 min, 10:00-19:59, Mon-Sat, server time). Paste everything below this line.
# Mails the user's own Team Lead (Export only while DOMESTIC_ON = False  # Domestic (non-Export) alerts are paused; True sends them to the National Sales Head
EXPORT_HEAD = "Exports Sales Head"
NATIONAL_HEAD = "National Sales Head"


try:
    now = frappe.utils.now_datetime()
    cutoff = frappe.utils.add_to_date(now, hours=-STALE_HOURS)

    call_users = frappe.get_all("Has Role", filters={"role": "Call Tracker User", "parenttype": "User"}, pluck="parent")
    stale = frappe.get_all(
        "User",
        filters=[
            ["enabled", "=", 1],
            ["custom_inh_app_user", "=", 1],
            ["custom_inh_record_calls", "=", 1],
            ["custom_inh_last_sync", "is", "set"],  # never-synced users are skipped
            ["custom_inh_last_sync", "<", cutoff],
            ["name", "in", call_users or ["-"]],
        ],
        fields=["name", "full_name", "custom_inh_last_sync", "custom_inh_last_seen",
                "custom_inh_rec_pending", "custom_inh_rec_failed", "custom_inh_app_version",
                "custom_inh_last_alert"],
        order_by="custom_inh_last_sync asc",
    )

    by_to = {}
    for u in stale:
        if u.name in EXCLUDE:
            continue
        if u.custom_inh_last_alert and u.custom_inh_last_alert >= u.custom_inh_last_sync:
            continue  # already alerted for this silence; alerts again only after the phone uploads and goes quiet again
        to = ""
        sp = frappe.db.get_value("Sales Person", {"custom_sales_person_email": u.name, "enabled": 1},
                                 ["name", "lft", "parent_sales_person"], as_dict=True)
        if not sp:
            emp = frappe.db.get_value("Employee", {"user_id": u.name}, "name")
            if emp:
                sp = frappe.db.get_value("Sales Person", {"employee": emp, "enabled": 1},
                                         ["name", "lft", "parent_sales_person"], as_dict=True)
        ex = frappe.db.get_value("Sales Person", EXPORT_HEAD, ["lft", "rgt"], as_dict=True)
        if sp and ex.lft <= sp.lft <= ex.rgt:
            # Export: nearest ancestor with a mail that is not the user himself (his TL); the Exports Sales Head is the fallback
            parent = sp.parent_sales_person
            for i in range(8):
                if not parent:
                    break
                prow = frappe.db.get_value("Sales Person", parent, ["employee", "custom_sales_person_email", "parent_sales_person"], as_dict=True)
                pmail = prow.custom_sales_person_email
                if not pmail and prow.employee:
                    pmail = frappe.db.get_value("Employee", prow.employee, "user_id")
                if parent == EXPORT_HEAD or (pmail and pmail.lower() != u.name.lower()):
                    to = pmail or ""
                    break
                parent = prow.parent_sales_person
        elif DOMESTIC_ON:
            nrow = frappe.db.get_value("Sales Person", NATIONAL_HEAD, ["employee", "custom_sales_person_email"], as_dict=True)
            to = nrow.custom_sales_person_email or (frappe.db.get_value("Employee", nrow.employee, "user_id") if nrow.employee else "") or ""
        if not to or to.lower() == u.name.lower():
            continue
        if to not in by_to:
            by_to[to] = []
        by_to[to].append(u)

    def table(users):
        html = "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
        html += "<tr><th>User</th><th>Last upload</th><th>Last app check-in</th><th>Pending</th><th>Failed</th><th>App</th></tr>"
        for u in users:
            html += "<tr><td>%s<br>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                u.full_name or "", u.name, str(u.custom_inh_last_sync)[:16],
                str(u.custom_inh_last_seen or "-")[:16], u.custom_inh_rec_pending or 0,
                u.custom_inh_rec_failed or 0, u.custom_inh_app_version or "-")
        return html + "</table>"

    for to in by_to:
        users = by_to[to]
        subject = "Call Tracker: %d user(s) not uploading recordings" % len(users)
        body = ("<p>These phones have not uploaded a call or recording for more than %d hours "
                "(checked %s).</p>%s<p>Please ask them to open the Call Tracker app, check they are signed in, "
                "and that recording permission and battery settings are on.</p>") % (
                    STALE_HOURS, str(now)[:16], table(users))
        recipient = TEST_TO or to
        if DRY_RUN:
            frappe.log_error(title="SYNC ALERT DRY RUN", message="to=%s subject=%s users=%s" % (
                recipient, subject, ", ".join([x.name for x in users])))
        else:
            frappe.sendmail(recipients=[recipient], subject=subject, message=body)
            for x in users:
                frappe.db.set_value("User", x.name, "custom_inh_last_alert", now, update_modified=False)
except Exception as err:
    frappe.log_error(title="Call Tracker Sync Alert failed", message=repr(err))
