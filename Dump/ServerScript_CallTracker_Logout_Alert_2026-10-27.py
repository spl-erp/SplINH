# ============================================================================
# Server Script "Call Tracker Logout Alert"
#   Script Type: DocType Event     Reference DocType: Call Tracker Login Event     DocType Event: After Insert
# Mails the user's own Team Lead (Export only while DOMESTIC_ON is False) when a Call Tracker token is
# revoked (logged out, password changed, unused 30 days...). TEST_TO = your own mail while testing.
# Sandbox: no tuple unpacking, functions cannot see module-level names, so logic is inline.
# ============================================================================
TEST_TO = ""
EXCLUDE = ["dummy@splashjetink.com", "temp@gmail.com"]
DOMESTIC_ON = False  # Domestic (non-Export) alerts are paused; True sends them to the National Sales Head
EXPORT_HEAD = "Exports Sales Head"
NATIONAL_HEAD = "National Sales Head"


# ============================================================================
# Server Script "Call Tracker Logout Alert"
#   Script Type: DocType Event     Reference DocType: Call Tracker Login Event
#   DocType Event: After Insert     (paste everything below this line)
# Mails the user's own Team Lead (Export only while DOMESTIC_ON = False  # Domestic (non-Export) alerts are paused; True sends them to the National Sales Head
EXPORT_HEAD = "Exports Sales Head"
NATIONAL_HEAD = "National Sales Head"


try:
    reason = doc.detail or ""
    if doc.event == "token_revoked" and "replaced by a newer device" not in reason and doc.user not in EXCLUDE:
        to = ""
        sp = frappe.db.get_value("Sales Person", {"custom_sales_person_email": doc.user, "enabled": 1},
                                 ["name", "lft", "parent_sales_person"], as_dict=True)
        if not sp:
            emp = frappe.db.get_value("Employee", {"user_id": doc.user}, "name")
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
                if parent == EXPORT_HEAD or (pmail and pmail.lower() != doc.user.lower()):
                    to = pmail or ""
                    break
                parent = prow.parent_sales_person
        elif DOMESTIC_ON:
            nrow = frappe.db.get_value("Sales Person", NATIONAL_HEAD, ["employee", "custom_sales_person_email"], as_dict=True)
            to = nrow.custom_sales_person_email or (frappe.db.get_value("Employee", nrow.employee, "user_id") if nrow.employee else "") or ""
        u = frappe.db.get_value("User", doc.user, ["full_name", "custom_inh_last_sync", "custom_inh_last_seen"], as_dict=True)
        body = ("<p><b>%s</b> (%s) was signed out of the Call Tracker app.</p>"
                "<p>Reason: %s<br>Time: %s<br>Last upload: %s<br>Last app check-in: %s</p>"
                "<p>Recordings from this phone will not sync until they sign in again.</p>") % (
                    (u.full_name if u else "") or doc.user, doc.user, reason or "-",
                    str(doc.creation)[:16], str((u.custom_inh_last_sync if u else None) or "-")[:16],
                    str((u.custom_inh_last_seen if u else None) or "-")[:16])
        recipient = TEST_TO or to
        if recipient and recipient.lower() != doc.user.lower():
            frappe.sendmail(recipients=[recipient],
                            subject="Call Tracker: %s was signed out" % ((u.full_name if u else "") or doc.user),
                            message=body)
except Exception as err:
    frappe.log_error(title="Call Tracker Logout Alert failed", message=repr(err))
