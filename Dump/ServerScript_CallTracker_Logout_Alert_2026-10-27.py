# ============================================================================
# Server Script "Call Tracker Logout Alert"
#   Script Type: DocType Event     Reference DocType: Call Tracker Login Event
#   DocType Event: After Insert     (paste everything below this line)
# Mails the Exports / National Sales Head when a Call Tracker token is revoked
# (logged out, password changed, unused 30 days...). TEST_TO = your own mail while testing.
# ============================================================================
TEST_TO = ""
EXCLUDE = ["dummy@splashjetink.com", "temp@gmail.com"]
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


try:
    reason = doc.detail or ""
    if doc.event == "token_revoked" and "replaced by a newer device" not in reason and doc.user not in EXCLUDE:
        lft = frappe.db.get_value("Sales Person", {"custom_sales_person_email": doc.user, "enabled": 1}, "lft")
        if lft is None:
            emp = frappe.db.get_value("Employee", {"user_id": doc.user}, "name")
            if emp:
                lft = frappe.db.get_value("Sales Person", {"employee": emp, "enabled": 1}, "lft")
        ex_l, ex_r, ex_mail = head_info(EXPORT_HEAD)
        na_l, na_r, na_mail = head_info(NATIONAL_HEAD)
        team, to, note = "Domestic", na_mail, ""
        if lft is not None and ex_l <= lft <= ex_r:
            team, to = "Export", ex_mail
        elif lft is None:
            note = "<p>This user is not linked to a Sales Person, please link them.</p>"
        u = frappe.db.get_value("User", doc.user, ["full_name", "custom_inh_last_sync", "custom_inh_last_seen"], as_dict=True)
        body = ("<p><b>%s</b> (%s, %s) was signed out of the Call Tracker app.</p>"
                "<p>Reason: %s<br>Time: %s<br>Last upload: %s<br>Last app check-in: %s</p>%s"
                "<p>Recordings from this phone will not sync until they sign in again.</p>") % (
                    (u.full_name if u else "") or doc.user, doc.user, team, reason or "-",
                    str(doc.creation)[:16], str((u.custom_inh_last_sync if u else None) or "-")[:16],
                    str((u.custom_inh_last_seen if u else None) or "-")[:16], note)
        recipient = TEST_TO or to
        if recipient and recipient != doc.user:
            frappe.sendmail(recipients=[recipient],
                            subject="Call Tracker: %s was signed out" % ((u.full_name if u else "") or doc.user),
                            message=body)
except Exception as err:
    frappe.log_error(title="Call Tracker Logout Alert failed", message=repr(err))
