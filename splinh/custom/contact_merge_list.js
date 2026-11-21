// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
// "Merge Contacts" in the Actions menu of the Contact list: select 2 to 4 Contacts that are duplicates (they share a phone number or an
// email), pick the main Contact and, per field, which Contact's value goes into it. Phone numbers, emails and party links of all of them
// are combined onto the main Contact. Server side: splinh.api.contact_merge. Wraps (never replaces) the existing onload.
// The dialog is also offered through window.splinh_contact_merge.open(names, {main, listview, on_done}): the "Similar Contacts" banner on
// the Contact form. This file is installed as both the List and the Form Client Script, so it works whichever page is opened first.
(() => {
	try {
		install();
	} catch (e) {
		// never let this script stop the Contact list from loading
		console.error("Merge Contacts could not be installed", e);
	}

	function install() {
	window.splinh_contact_merge = window.splinh_contact_merge || {open: (names, opts) => open_names(names, opts || {})};

	const settings = (frappe.listview_settings["Contact"] = frappe.listview_settings["Contact"] || {});
	if (settings.__splinh_contact_merge) return;
	settings.__splinh_contact_merge = true;

	const previous_onload = settings.onload;
	settings.onload = function (listview) {
		if (previous_onload) previous_onload.apply(this, arguments);
		try {
			if (!frappe.model.can_write("Contact")) return;
			listview.page.add_actions_menu_item(__("Merge Contacts"), () => start(listview), false);
		} catch (e) {
			console.error("Merge Contacts menu item failed", e);
		}
	};

	const MIN = 2;
	const MAX = 4;
	const esc = (text) => frappe.utils.escape_html(text == null ? "" : String(text));
	const norm = (v) => (v == null || v === "" || v === 0 || v === false ? "" : String(v).split(/\s+/).filter(Boolean).join(" "));
	const digits = (v) => String(v || "").replace(/\D/g, "").slice(-10);
	const unique = (values, key) => {
		const seen = new Set();
		return values.filter((v) => {
			const k = key(v);
			if (!k || seen.has(k)) return false;
			seen.add(k);
			return true;
		});
	};

	function start(listview) {
		open_names(listview.get_checked_items(true), {listview: listview});
	}

	// names: the Contacts to merge (2 to 4); opts.main: the Contact to suggest as the main one; opts.listview: clear its ticks afterwards;
	// opts.on_done(main): what to do after a merge (default: open the main Contact).
	function open_names(names, opts) {
		if (names.length < MIN || names.length > MAX) {
			frappe.msgprint({
				title: __("Merge Contacts"),
				message: __("Select between {0} and {1} Contacts, then choose Merge Contacts again.", [MIN, MAX]),
				indicator: "orange",
			});
			return;
		}
		frappe
			.call({
				method: "splinh.api.contact_merge.get_merge_preview",
				args: {names: names},
				freeze: true,
				freeze_message: __("Comparing the Contacts..."),
			})
			.then((r) => r.message && open_dialog(opts, r.message));
	}

	function open_dialog(opts, preview) {
		const names = preview.names;
		const by_name = {};
		preview.contacts.forEach((c) => (by_name[c.name] = c));
		const state = {
			main: opts.main && names.includes(opts.main) ? opts.main : preview.suggested_main,
			use: {}, // fieldname -> contact name, only where the user left the main Contact's value
			show_all: false,
		};
		const dup = preview.duplicate || {ok: true};

		const dialog = new frappe.ui.Dialog({
			title: __("Merge Contacts"),
			size: "extra-large",
			fields: [{fieldname: "body", fieldtype: "HTML"}],
			primary_action_label: __("Merge"),
			primary_action: () => confirm_merge(),
		});
		dialog.$wrapper.find(".modal-dialog").css("max-width", "1200px");

		const used = (row) => state.use[row.fieldname] || state.main;

		function shown(value, fieldtype) {
			if (value == null || value === "") return "";
			if (fieldtype === "Check") return value ? __("Yes") : __("No");
			return String(value);
		}

		function cell_text(value, fieldtype) {
			const text = shown(value, fieldtype);
			if (!text) return `<span class="text-muted">&mdash;</span>`;
			const clipped = text.length > 90 ? text.slice(0, 90) + "..." : text;
			return `<span title="${esc(text)}" style="white-space: pre-wrap; word-break: break-word;">${esc(clipped)}</span>`;
		}

		function chips(c) {
			const list = [`${(c.phones || []).length} ${__("phones")}`, `${(c.emails || []).length} ${__("emails")}`, `${c.calls} ${__("calls")}`, `${c.documents} ${__("documents")}`];
			if (c.primary_for && c.primary_for.length) list.push(`<b class="text-danger">${esc(__("Primary Contact of"))}: ${esc(c.primary_for.join(", "))}</b>`);
			return list.join(" &middot; ");
		}

		function notices() {
			let html = "";
			if (!dup.ok) html += `<div class="alert alert-danger mb-2"><b>${esc(__("These Contacts cannot be merged."))}</b> ${esc(dup.reason)}</div>`;
			else if ((dup.pairs || []).length) {
				const lines = dup.pairs.map((p) => `${esc(p.a)} &harr; ${esc(p.b)} (${p.on.map((x) => esc(x === "phone" ? __("phone number") : __("email"))).join(", ")})`).join("; ");
				html += `<div class="text-muted small mb-2">${esc(__("Duplicates: they share"))} ${lines}</div>`;
			}
			if (dup.ok && preview.cross_party && preview.cross_party.different) html += `<div class="alert alert-warning mb-2">${esc(preview.cross_party.note)}</div>`;
			if (dup.ok && preview.logins && preview.logins.conflict) html += `<div class="alert alert-info mb-2">${esc(__("These Contacts have different portal logins ({0}). Choose the one to keep in the 'User' row; the other login stays a User but is no longer linked to this Contact.", [preview.logins.users.join(", ")]))}</div>`;
			if (dup.ok && preview.unsubscribed_differs) html += `<div class="alert alert-warning mb-2">${esc(__("One of these Contacts is unsubscribed and another is not. Check the 'Unsubscribed' row: choosing 'not unsubscribed' lets this person be messaged again."))}</div>`;
			return html;
		}

		function combined_html() {
			const all = names.map((n) => by_name[n]);
			const phones = unique([].concat(...all.map((c) => c.phones || [])), digits);
			const emails = unique([].concat(...all.map((c) => c.emails || [])), (v) => String(v || "").toLowerCase());
			const parties = unique([].concat(...all.map((c) => c.parties || [])), (v) => v);
			return `<div class="small mb-2"><b>${esc(__("The main Contact will hold"))}:</b>
				${esc(__("phones"))}: ${esc(phones.join(", ") || "-")} &middot; ${esc(__("emails"))}: ${esc(emails.join(", ") || "-")} &middot; ${esc(__("linked to"))}: ${esc(parties.join(", ") || "-")}
				<div class="text-muted">${esc(__("The main Contact keeps its own primary phone and email; the others are added as extra numbers."))}</div></div>`;
		}

		function render() {
			const rows = preview.rows.filter((row) => state.show_all || row.differs);
			let html = notices();
			if (!dup.ok) {
				html += `<div class="d-flex flex-wrap mb-2" style="gap: 12px;">` + names.map((n) => {
					const c = by_name[n];
					return `<div class="border rounded p-2" style="flex: 1 1 200px;"><a href="${frappe.utils.get_form_link("Contact", n)}" target="_blank" rel="noopener">${esc(n)}</a>
						<div class="small text-muted">${esc((c.phones || []).join(", ") || __("no phone"))}</div><div class="small text-muted">${esc((c.emails || []).join(", ") || __("no email"))}</div></div>`;
				}).join("") + `</div>`;
				const $b = dialog.fields_dict.body.$wrapper;
				$b.html(html);
				dialog.get_primary_btn().prop("disabled", true);
				return;
			}
			html += `<div class="text-muted small mb-2">${esc(__("Choose the main Contact, then for each field the value that goes into it. A field you do not change keeps the main Contact's value. Everything connected to the other Contacts (calls, quotations, orders, invoices, comments, tasks, the Customer it is the primary Contact of) moves to the main Contact and they are deleted."))}</div>`;
			html += `<div class="d-flex flex-wrap mb-2" style="gap: 12px;">`;
			names.forEach((n) => {
				const c = by_name[n];
				html += `<div class="border rounded p-2" style="flex: 1 1 200px; min-width: 200px;">
					<label class="mb-1"><input type="radio" name="splinh-main" value="${esc(n)}" ${state.main === n ? "checked" : ""}> <b>${esc(__("Main Contact"))}</b></label>
					<div><a href="${frappe.utils.get_form_link("Contact", n)}" target="_blank" rel="noopener">${esc(n)}</a></div>
					<div class="small">${esc(c.title)}</div>
					<div class="small text-muted">${esc((c.parties || []).join(", ") || __("no party"))}</div>
					<div class="small text-muted">${chips(c)}</div>
				</div>`;
			});
			html += `</div>` + combined_html();
			html += `<div class="mb-2"><label class="small"><input type="checkbox" class="splinh-show-all" ${state.show_all ? "checked" : ""}> ${esc(__("Show all fields (not only the ones that differ)"))}</label>
				<span class="text-muted small"> &middot; ${esc(__("{0} of {1} fields shown", [rows.length, preview.rows.length]))}</span></div>`;
			html += `<div style="max-height: 45vh; overflow: auto;"><table class="table table-bordered table-sm small mb-0"><thead style="position: sticky; top: 0; background: var(--card-bg, #fff); z-index: 1;"><tr><th style="min-width: 150px;">${esc(__("Field"))}</th>`;
			names.forEach((n) => (html += `<th style="min-width: 150px;">${esc(n)}${state.main === n ? ` <span class="badge badge-primary">${esc(__("main"))}</span>` : ""}</th>`));
			html += `<th style="min-width: 170px;">${esc(__("Result in main Contact"))}</th></tr></thead><tbody>`;
			let section = null;
			rows.forEach((row) => {
				if (row.section !== section) {
					section = row.section;
					html += `<tr class="table-active"><td colspan="${names.length + 2}"><b>${esc(section || "")}</b></td></tr>`;
				}
				const use = used(row);
				html += `<tr><td>${esc(row.label)}<div class="text-muted" style="font-size: 10px;">${esc(row.fieldname)}</div></td>`;
				names.forEach((n) => {
					const is_use = use === n;
					html += `<td ${is_use ? 'class="table-success"' : ""}><label class="d-block mb-0"><input type="radio" class="splinh-use" name="use-${esc(row.fieldname)}" data-field="${esc(row.fieldname)}" data-contact="${esc(n)}" ${is_use ? "checked" : ""}> ${cell_text(row.values[n], row.fieldtype)}</label></td>`;
				});
				html += `<td>${cell_text(row.values[use], row.fieldtype)}</td></tr>`;
			});
			if (!rows.length) html += `<tr><td colspan="${names.length + 2}" class="text-muted">${esc(__("The selected Contacts have identical values in every field."))}</td></tr>`;
			html += `</tbody></table></div>`;

			const $body = dialog.fields_dict.body.$wrapper;
			$body.html(html);
			dialog.set_primary_action(__("Merge {0} Contacts into {1}", [names.length, state.main]), confirm_merge);
			dialog.get_primary_btn().prop("disabled", false);
			$body.find("input[name=splinh-main]").on("change", function () {
				state.main = this.value;
				Object.keys(state.use).forEach((f) => state.use[f] === state.main && delete state.use[f]);
				render();
			});
			$body.find(".splinh-show-all").on("change", function () {
				state.show_all = this.checked;
				render();
			});
			$body.find(".splinh-use").on("change", function () {
				const f = this.dataset.field;
				if (this.dataset.contact === state.main) delete state.use[f];
				else state.use[f] = this.dataset.contact;
				render();
			});
		}

		function choices_payload() {
			const out = {};
			preview.rows.forEach((row) => {
				const use = state.use[row.fieldname];
				if (use) out[row.fieldname] = {use: use, append: []};
			});
			return out;
		}

		function confirm_merge() {
			const others = names.filter((n) => n !== state.main);
			frappe.confirm(
				__("Merge {0} into {1}? The other Contact(s) will be DELETED and everything connected to them moves to {1}; their phone numbers, emails and party links are added to it. A copy of them is kept in the Contact Merge Log, but this cannot be undone from the screen.", [others.join(", "), state.main]),
				() => {
					frappe
						.call({
							method: "splinh.api.contact_merge.merge_contacts",
							args: {names: names, main: state.main, choices: choices_payload(), stamps: preview.stamps},
							freeze: true,
							freeze_message: __("Merging..."),
						})
						.then((r) => {
							if (!r.message) return;
							dialog.hide();
							const m = r.message;
							const extra = [];
							if (m.added) extra.push(__("added {0} phones, {1} emails, {2} party links", [m.added.phones, m.added.emails, m.added.links]));
							if ((m.customers_refreshed || []).length) extra.push(__("refreshed {0}", [m.customers_refreshed.join(", ")]));
							frappe.show_alert({message: __("Merged into {0}", [m.main]) + (extra.length ? " (" + extra.join("; ") + ")" : ""), indicator: "green"}, 10);
							const over = Object.keys(m.overridden || {});
							if (over.length) frappe.msgprint({title: __("Merged - please check"), indicator: "orange", message: __("The Contact's own rules changed these fields when it was saved, so they differ from your choice: {0}.", [over.join(", ")])});
							opts.listview && opts.listview.clear_checked_items && opts.listview.clear_checked_items();
							if (opts.on_done) return opts.on_done(m.main);
							const open_form = window.cur_frm && cur_frm.doc && cur_frm.doc.doctype === "Contact" ? cur_frm : null;
							if (open_form && (open_form.doc.name === m.main || names.includes(open_form.doc.name))) frappe.set_route("Form", "Contact", m.main);
							else frappe.set_route("Form", "Contact", m.main);
						});
				},
			);
		}

		dialog.show();
		render();
	}
	}
})();
