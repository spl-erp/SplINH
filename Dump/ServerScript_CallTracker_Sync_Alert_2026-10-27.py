# ============================================================================
# Server Script "Call Tracker Sync Alert"
#   Script Type: Scheduler Event      Event Frequency: Cron      Cron Format: */15 10-19 * * 1-6
#   (every 15 min, 10:00-19:59, Mon-Sat, server time). Paste everything below this line.
# Mails the Exports / National Sales Head when a Call Tracker phone has not uploaded for STALE_HOURS.
# First save with DRY_RUN = True (result appears in Error Log, titled "SYNC ALERT DRY RUN"),
# then set DRY_RUN = False. TEST_TO = your own mail sends BOTH heads' mails there while testing.
# Needs the custom field on User: custom_inh_last_alert (Datetime, read only).
# ============================================================================
DRY_RUN = True
TEST_TO = ""
STALE_HOURS = 4
EXCLUDE = ["dummy@splashjetink.com", "temp@gmail.com", "erp@splashjetink.com"]
EXPORT_HEAD = "Exports Sales Head"
NATIONAL_HEAD = "National Sales Head"


def head_info(sp_name):
    row = frappe.db.get_value(
        "Sales Person", sp_name, ["lft", "rgt", "employee", "custom_sales_person_email"], as_dict=True
    )
    mail = row.custom_sales_person_email
    if not mail and row.employee:
        mail = frappe.db.get_value("Employee", row.employee, "user_id")
    return row.lft, row.rgt, mail


def sales_person_lft(user):
    lft = frappe.db.get_value("Sales Person", {"custom_sales_person_email": user, "enabled": 1}, "lft")
    if lft is None:
        emp = frappe.db.get_value("Employee", {"user_id": user}, "name")
        if emp:
            lft = frappe.db.get_value("Sales Person", {"employee": emp, "enabled": 1}, "lft")
    return lft


try:
    ex_l, ex_r, ex_mail = head_info(EXPORT_HEAD)
    na_l, na_r, na_mail = head_info(NATIONAL_HEAD)
    now = frappe.utils.now_datetime()
    today = str(now)[:10]
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

    groups = {"export": [], "national": [], "unlinked": []}
    for u in stale:
        if u.name in EXCLUDE:
            continue
        if u.custom_inh_last_alert and u.custom_inh_last_alert >= u.custom_inh_last_sync:
            continue  # already alerted for this silence; alerts again only after the phone uploads and goes quiet again
        lft = sales_person_lft(u.name)
        if lft is not None and ex_l <= lft <= ex_r:
            groups["export"].append(u)
        elif lft is not None and na_l <= lft <= na_r:
            groups["national"].append(u)
        else:
            groups["unlinked"].append(u)

    def table(users):
        html = "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
        html += "<tr><th>User</th><th>Last upload</th><th>Last app check-in</th><th>Pending</th><th>Failed</th><th>App</th></tr>"
        for u in users:
            html += "<tr><td>%s<br>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                u.full_name or "", u.name, str(u.custom_inh_last_sync)[:16],
                str(u.custom_inh_last_seen or "-")[:16], u.custom_inh_rec_pending or 0,
                u.custom_inh_rec_failed or 0, u.custom_inh_app_version or "-")
        return html + "</table>"

    # who gets which list; unlinked people go to the National head with a note
    mails = []
    if groups["export"]:
        mails.append((ex_mail, "Export", groups["export"], ""))
    nat_users = groups["national"] + groups["unlinked"]
    if nat_users:
        note = ""
        if groups["unlinked"]:
            note = "<p>Not linked to a Sales Person (please link them): %s</p>" % ", ".join([x.name for x in groups["unlinked"]])
        mails.append((na_mail, "Domestic", nat_users, note))

    for to, team, users, note in mails:
        subject = "Call Tracker: %d %s user(s) not uploading recordings" % (len(users), team)
        body = ("<p>These phones have not uploaded a call or recording for more than %d hours "
                "(checked %s).</p>%s%s<p>Please ask them to open the Call Tracker app, check they are signed in, "
                "and that recording permission and battery settings are on.</p>") % (
                    STALE_HOURS, str(now)[:16], table(users), note)
        recipient = TEST_TO or to
        if DRY_RUN:
            frappe.log_error(title="SYNC ALERT DRY RUN", message="to=%s subject=%s users=%s" % (
                recipient, subject, ", ".join([x.name for x in users])))
        elif recipient:
            frappe.sendmail(recipients=[recipient], subject=subject, message=body)
            for x in users:
                frappe.db.set_value("User", x.name, "custom_inh_last_alert", now, update_modified=False)
except Exception as err:
    frappe.log_error(title="Call Tracker Sync Alert failed", message=repr(err))
