"""bench-level commands for this project.

Usage:
    bench --site <site> list-doctypes
    bench --site <site> list-doctypes --like customer
    bench --site <site> export-doctype "Lead"
    bench --site <site> export-doctype "Lead" --out /tmp/leads.csv
    bench --site <site> verify-live-code apps/splinh/splinh/custom/call_monitoring_detail.py

Exists because exporting a large doctype (30k+ rows) through the Desk UI's
Report Builder export is slow and fiddly for a one-off pull. This is the same
data, straight from the database, as a single CSV - no UI round trip.
"""

import csv
import os
import time

import click
import frappe
from frappe.commands import get_site, pass_context
from frappe.model import no_value_fields, table_fields


def _exportable_fields(meta):
	"""Every real, flat field on the doctype - same exclusion Frappe itself uses
	for "does this field hold a value" (Section/Column/Tab Break, HTML, Button,
	etc: no_value_fields), plus two field kinds a single frappe.get_all cannot
	pull the way every ordinary field can:
	  - Table/Table MultiSelect: child rows, not a column of the parent.
	  - is_virtual fields (e.g. Sales Order's last_scanned_warehouse): the
	    DocType explicitly opts these out of having a database column at all -
	    computed at read time, never stored - so a raw SQL SELECT naming one
	    fails with "Unknown column", not a missing-migration problem. Found
	    live via `export-doctype "Sales Order"` erroring on exactly that field;
	    confirmed no such column exists in tabSales Order and it's declared
	    is_virtual:1 in erpnext's own sales_order.json, not schema drift.
	"""
	child_tables = []
	virtual_fields = []
	fields = []
	for df in meta.fields:
		if df.fieldtype in table_fields:
			child_tables.append(df.fieldname)
			continue
		if df.fieldtype in no_value_fields:
			continue
		if df.is_virtual:
			virtual_fields.append(df.fieldname)
			continue
		fields.append(df.fieldname)
	return fields, child_tables, virtual_fields


@click.command("export-doctype")
@click.argument("doctype")
@click.option("--out", default=None, help="Output CSV path. Defaults to <doctype>.csv in the current directory.")
@pass_context
def export_doctype(context, doctype, out=None):
	"""Export every field and every record of a doctype to a single CSV.

	Table / Table MultiSelect fields are child rows, not flat columns, and
	is_virtual fields have no database column at all (computed on read, never
	stored) - neither can come back from a single frappe.get_all. Both are
	listed (not exported) at the end so nothing is silently missing from the
	CSV without you knowing.
	"""
	site = get_site(context)
	frappe.init(site)
	frappe.connect()
	try:
		if not frappe.db.exists("DocType", doctype):
			click.echo(f"No such DocType: {doctype!r}")
			return

		meta = frappe.get_meta(doctype)
		fields, child_tables, virtual_fields = _exportable_fields(meta)

		# `name` is always the real primary key and is often not in meta.fields
		# at all (it's implicit) - make sure it's first and never duplicated.
		if "name" in fields:
			fields.remove("name")
		fields = ["name"] + fields

		# `bench` runs commands from inside sites/, not wherever the terminal prompt
		# shows - a bare relative default landed there once already and looked like
		# a missing file. Anchor the default to the site's own folder so it's always
		# somewhere findable and consistent, whatever directory bench was invoked from.
		out_path = out or frappe.get_site_path(f"{frappe.scrub(doctype)}.csv")
		rows = frappe.get_all(doctype, fields=fields, limit_page_length=0, order_by="creation asc")

		with open(out_path, "w", newline="") as fh:
			writer = csv.DictWriter(fh, fieldnames=fields)
			writer.writeheader()
			for row in rows:
				writer.writerow(row)

		import os

		click.echo(f"Wrote {len(rows)} rows, {len(fields)} fields -> {os.path.abspath(out_path)}")
		if child_tables:
			click.echo(
				f"Not included (child tables, not flat columns): {', '.join(child_tables)}. "
				f"Ask for these by name if you need them too - each is its own doctype "
				f"and can be exported the same way, filtered by parent."
			)
		if virtual_fields:
			click.echo(
				f"Not included (virtual fields, no stored value - computed on read): "
				f"{', '.join(virtual_fields)}."
			)
	finally:
		frappe.destroy()


@click.command("list-doctypes")
@click.option("--like", default=None, help="Only names containing this text (case-insensitive).")
@pass_context
def list_doctypes(context, like=None):
	"""Print every DocType name on this site, one per line - plain text, so it's
	easy to copy a name straight out of the terminal for use with export-doctype
	or anything else that needs an exact doctype name.
	"""
	site = get_site(context)
	frappe.init(site)
	frappe.connect()
	try:
		filters = {"name": ["like", f"%{like}%"]} if like else {}
		names = frappe.get_all("DocType", filters=filters, pluck="name", order_by="name asc")
		for name in names:
			click.echo(name)
		click.echo(f"\n{len(names)} doctype(s)", err=True)
	finally:
		frappe.destroy()


@click.command("rematch-call-logs")
@click.option(
	"--dry-run",
	is_flag=True,
	default=False,
	help="Rehearse in a SAVEPOINT and always roll back - reports before/after numbers, persists nothing.",
)
@click.option("--batch-size", default=500, help="Batch size for the unlinked-row correction pass.")
@pass_context
def rematch_call_logs_command(context, dry_run=False, batch_size=500):
	"""Re-run the fixed Customer > Lead > neither Call Log/Lead/Customer phone
	matching (2026-09-26 normalized-last10 fix) across every Call Log record
	synced to date.

	Idempotent and safe to re-run any time more matching fixes land later:
	an already-linked row (customer or custom_lead already set) is only ever
	VERIFIED against its own existing `links` - never re-matched from the raw
	phone number - and the command refuses to write ANYTHING at all if that
	verification finds even one discrepancy between the stored party fields
	and what the current priority logic would derive. Only a row with
	NEITHER customer nor custom_lead set is actually corrected, using the
	same normalized/indexed last-10-digit lookup as the live insert path
	(never the old unindexed LIKE). See
	splinh.custom.call_log_phone_matching.rematch_call_logs for the full
	two-pass design.

	Use --dry-run first: runs the exact same logic inside a SAVEPOINT and
	always rolls back at the end, so the before/after numbers can be
	sanity-checked with nothing persisted.
	"""
	site = get_site(context)
	frappe.init(site)
	frappe.connect()
	try:
		from splinh.custom.call_log_phone_matching import rematch_call_logs

		result = rematch_call_logs(batch_size=batch_size, dry_run=dry_run)

		click.echo(f"{'DRY RUN (rolled back, nothing persisted)' if dry_run else 'REAL RUN'}")
		click.echo(f"Total Call Log rows: {result['total']}")
		click.echo(
			f"  BEFORE - customer set: {result['before_customer']}, "
			f"custom_lead set: {result['before_lead']}, neither: {result['before_neither']}"
		)
		click.echo(
			f"Verify pass: {result['verified_consistent']} already-linked row(s) confirmed consistent, "
			f"{len(result['discrepancies'])} discrepancy(ies)"
		)

		if not result["discrepancies"]:
			click.echo(
				f"  Dynamic Link backfill (Pass 1b, additive only): "
				f"{result['dynamic_links_backfilled']} row(s) gained a Customer/Lead Dynamic Link row"
			)

		if result["discrepancies"]:
			click.echo(
				"STOPPED: discrepancy found on an already-linked row - nothing was written "
				"(including the unlinked-row correction pass). Review before re-running:"
			)
			for d in result["discrepancies"]:
				click.echo(f"  {d['name']}: stored={d['stored']!r} recomputed={d['recomputed']!r}")
			return

		click.echo(
			f"Correction pass: scanned {result['scanned_unlinked']} unlinked row(s) -> "
			f"{result['newly_linked_customer']} newly linked to a Customer, "
			f"{result['newly_linked_lead']} newly linked to a Lead "
			f"({result['dynamic_links_added_pass2']} Dynamic Link row(s) added)"
		)
		click.echo(
			f"  AFTER  - customer set: {result['after_customer']}, "
			f"custom_lead set: {result['after_lead']}, still unlinked: {result['still_unlinked']}"
		)
		if result["dynamic_links_missing_target"]:
			click.echo(
				f"  WARNING: {len(result['dynamic_links_missing_target'])} row(s) reference a "
				f"Customer/Lead that no longer exists (dangling reference, pre-existing - not "
				f"fixed here, only skipped so it doesn't abort the run):"
			)
			for m in result["dynamic_links_missing_target"]:
				click.echo(f"    {m['name']}: {m['link_doctype']} {m['link_name']!r} not found")
		if result["sample_unlinked"]:
			click.echo("Sample still-unlinked row(s) (name, type, number):")
			for s in result["sample_unlinked"]:
				click.echo(f"  {s['name']}: {s['type']} {s['number']}")
	finally:
		frappe.destroy()


@click.command("verify-live-code")
@click.argument("paths", nargs=-1, required=True)
@pass_context
def verify_live_code_command(context, paths):
	"""Check whether the gunicorn/worker processes actually serving THIS bench
	have been restarted since the given file(s) were last edited.

	Exists because of a recurring trap on this bench (documented in
	CHANGELOG.md - four separate times in one day at last count): an edit
	lands on disk, a `bench console` or `bench execute` check is used to
	"verify" it, and that check passes - because both of those always spin up
	a brand-new process that imports current disk code regardless of whether
	the real, long-running gunicorn workers (which serve actual browser/API
	traffic, running with `--preload` so they import each Python module
	exactly ONCE at worker-start and never again) have picked up the change.
	The fix can be completely correct on disk and the live site can still
	serve the old behaviour indefinitely, until something restarts those
	specific worker processes.

	This command replaces the manual `ps -eo pid,lstart,cmd` + `stat` +
	side-by-side comparison with one line: for each PATH, print its mtime,
	then print every running process that (a) belongs to THIS bench
	(matched by `frappe.utils.get_bench_path()` appearing in its command
	line - so a second bench on the same host, e.g. a differently-named
	bench serving a different site on a different port, is never confused
	with this one) and (b) is one of the actual request-serving processes
	(gunicorn web workers, RQ background workers, the scheduler) - NOT this
	command's own short-lived process, which tells you nothing about them.
	Each such process is flagged OK (started after the file was last
	modified - it has the current code) or STALE (started before - it is
	still running whatever was on disk at ITS start time, no matter what
	the file says now).

	Exit code is non-zero if anything is STALE, so this can gate a "did I
	actually ship this" step in a script, not just a human reading output.
	"""
	import psutil

	site = get_site(context)
	frappe.init(site)
	try:
		bench_path = os.path.realpath(frappe.utils.get_bench_path())
	finally:
		frappe.destroy()

	# Matched by substring on each process's own cmdline - deliberately not
	# "is a child of this bench's supervisor", since supervisor's own pid
	# isn't a stable/discoverable parent here and the cmdline already
	# uniquely identifies which bench's venv/gunicorn binary is running.
	relevant_markers = ("gunicorn", "bench_helper")
	own_pid = os.getpid()  # this command itself runs AS a bench_helper process -
	# exclude it explicitly, or it would always show up as one more (trivially
	# fresh, meaningless) "OK" row rather than being left out entirely.

	procs = []
	for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
		if proc.info["pid"] == own_pid:
			continue
		try:
			cmdline = proc.info["cmdline"] or []
		except (psutil.NoSuchProcess, psutil.AccessDenied):
			continue
		cmd_str = " ".join(cmdline)
		if bench_path not in cmd_str:
			continue
		if not any(marker in cmd_str for marker in relevant_markers):
			continue
		procs.append(
			{
				"pid": proc.info["pid"],
				"start": proc.info["create_time"],
				"cmd": cmd_str,
			}
		)

	if not procs:
		click.echo(
			f"No running gunicorn/worker processes found for bench path {bench_path!r}. "
			f"Nothing to compare against - is the bench actually running (`sudo supervisorctl "
			f"status`)?"
		)
		raise SystemExit(1)

	any_stale = False
	for path in paths:
		abspath = os.path.abspath(path)
		if not os.path.exists(abspath):
			click.echo(f"{path}: NOT FOUND, skipping")
			any_stale = True
			continue
		mtime = os.path.getmtime(abspath)
		click.echo(
			f"\n{path}\n  edited: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))}"
		)
		for p in sorted(procs, key=lambda p: p["start"]):
			status = "OK (postdates edit)" if p["start"] > mtime else "STALE (predates edit)"
			if status.startswith("STALE"):
				any_stale = True
			click.echo(
				f"  pid {p['pid']:>7}  started {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p['start']))}"
				f"  {status}"
			)

	click.echo("")
	if any_stale:
		click.echo(
			"STALE process(es) found: restart them before trusting live behaviour - "
			"e.g. `sudo supervisorctl restart <web-group>:* <workers-group>:*` then "
			"`bench --site <site> clear-cache`, then re-run this command to confirm."
		)
		raise SystemExit(1)
	click.echo("All matching processes postdate every file checked - safe to trust live behaviour.")


commands = [export_doctype, list_doctypes, rematch_call_logs_command, verify_live_code_command]
