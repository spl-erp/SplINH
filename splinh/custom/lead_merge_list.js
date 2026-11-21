// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
// "Merge Leads" in the Actions menu of the Lead list: select 2 to 4 Leads, pick the main Lead and, per
// field, which Lead's value goes into it (or append the text of the others). Server side:
// splinh.api.lead_merge. Wraps (never replaces) the existing onload - sigzensfa's lead_list.js adds its
// Sales Person filter there and splinh's Duplicate Check button wraps it too.
// The dialog is also offered elsewhere through window.splinh_lead_merge.open(names, {main, listview, on_done}):
// the Duplicate Check result (list view) and the "possible duplicates" banner on the Lead form. This file is installed
// as both the List and the Form Client Script, so it works whichever page is opened first.
(() => {
	try {
		install();
	} catch (e) {
		// never let this script stop the Lead list from loading
		console.error("Merge Leads could not be installed", e);
	}

	function install() {
	// the entry point other scripts call; defined once per page
	window.splinh_lead_merge = window.splinh_lead_merge || {open: (names, opts) => open_names(names, opts || {})};

	const settings = (frappe.listview_settings["Lead"] = frappe.listview_settings["Lead"] || {});
	if (settings.__splinh_lead_merge) return;
	settings.__splinh_lead_merge = true;

	const previous_onload = settings.onload;
	settings.onload = function (listview) {
		if (previous_onload) previous_onload.apply(this, arguments);
		try {
			if (!frappe.model.can_write("Lead")) return;
			listview.page.add_actions_menu_item(__("Merge Leads"), () => start(listview), false);
		} catch (e) {
			console.error("Merge Leads menu item failed", e);
		}
	};

	const MIN = 2;
	const MAX = 4;
	const DATA_MAX = 140;
	const SEPARATOR = {single: " ", multi: "\n", html: "<div><br></div>"};

	const esc = (text) => frappe.utils.escape_html(text == null ? "" : String(text));
	const norm = (v) => (v == null || v === "" || v === 0 || v === false ? "" : String(v).split(/\s+/).filter(Boolean).join(" "));

	// Same rule as custom/lead_merge.py append_values: a text equal to the base or to one already
	// appended (ignoring case and spacing) is not added twice.
	function append_values(base, extras, kind) {
		const sep = SEPARATOR[kind];
		let text = base == null ? "" : String(base);
		const seen = [norm(text).toLowerCase()];
		for (const extra of extras) {
			const piece = extra == null ? "" : String(extra);
			if (!piece.trim()) continue;
			const key = norm(piece).toLowerCase();
			if (seen.includes(key)) continue;
			seen.push(key);
			text = !text.trim() ? piece.trim() : text.replace(/\s+$/, "") + sep + piece.trim();
		}
		return text;
	}

	function start(listview) {
		open_names(listview.get_checked_items(true), {listview: listview});
	}

	// names: the Leads to merge (2 to 4); opts.main: the Lead to suggest as the main one; opts.listview: clear its ticks afterwards;
	// opts.on_done(main): what to do after a merge (default: open the main Lead).
	function open_names(names, opts) {
		if (names.length < MIN || names.length > MAX) {
			frappe.msgprint({
				title: __("Merge Leads"),
				message: __("Select between {0} and {1} Leads, then choose Merge Leads again.", [MIN, MAX]),
				indicator: "orange",
			});
			return;
		}
		frappe
			.call({
				method: "splinh.api.lead_merge.get_merge_preview",
				args: {names: names},
				freeze: true,
				freeze_message: __("Comparing the Leads..."),
			})
			.then((r) => r.message && open_dialog(opts, r.message));
	}

	function open_dialog(opts, preview) {
		const names = preview.names;
		const lead_by_name = {};
		preview.leads.forEach((l) => (lead_by_name[l.name] = l));
		const state = {
			main: opts.main && preview.names.includes(opts.main) ? opts.main : preview.suggested_main,
			use: {}, // fieldname -> lead name, only where the user left the main Lead's value
			append: {}, // fieldname -> [lead names]
			show_all: false,
		};

		const dialog = new frappe.ui.Dialog({
			title: __("Merge Leads"),
			size: "extra-large",
			fields: [{fieldname: "body", fieldtype: "HTML"}],
			primary_action_label: __("Merge"),
			primary_action: () => confirm_merge(),
		});
		dialog.$wrapper.find(".modal-dialog").css("max-width", "1200px");

		function used_lead(row) {
			return state.use[row.fieldname] || state.main;
		}

		function result_of(row) {
			const use = used_lead(row);
			let value = row.values[use];
			const extra = (state.append[row.fieldname] || []).filter((n) => n !== use);
			if (row.appendable && extra.length) {
				const ordered = names.filter((n) => extra.includes(n));
				value = append_values(value, ordered.map((n) => row.values[n]), row.appendable);
			}
			return value;
		}

		function too_long(row, value) {
			return row.appendable === "single" && norm(value).length > (row.length || DATA_MAX);
		}

		function shown(value, fieldtype) {
			if (value == null || value === "") return "";
			if (fieldtype === "Check") return value ? __("Yes") : __("No");
			if (fieldtype === "Text Editor") return frappe.utils.html2text ? frappe.utils.html2text(String(value)) : String(value);
			return String(value);
		}

		function cell_text(value, fieldtype) {
			const text = shown(value, fieldtype);
			if (!text) return `<span class="text-muted">&mdash;</span>`;
			const clipped = text.length > 90 ? text.slice(0, 90) + "..." : text;
			return `<span title="${esc(text)}" style="white-space: pre-wrap; word-break: break-word;">${esc(clipped)}</span>`;
		}

		function render() {
			const rows = preview.rows.filter((row) => state.show_all || row.differs);
			let invalid = 0;
			let html = `<div class="text-muted small mb-2">${esc(
				__("Choose the main Lead, then for each field the value that goes into it. A field you do not change keeps the main Lead's value, even when it is empty. 'Append' adds that Lead's text after the chosen value (text fields only). Everything connected to the other Leads (calls, contacts, addresses, customers, notes, comments, tasks) moves to the main Lead and they are deleted."),
			)}</div>`;

			html += `<div class="d-flex flex-wrap mb-2" style="gap: 12px;">`;
			names.forEach((n) => {
				const l = lead_by_name[n];
				const chips = [
					`${l.calls} ${__("calls")}`,
					`${l.contacts} ${__("contacts")}`,
					`${l.addresses} ${__("addresses")}`,
					`${l.notes} ${__("notes")}`,
				];
				if (l.customers.length) chips.push(`<b class="text-danger">${esc(__("Customer"))}: ${esc(l.customers.join(", "))}</b>`);
				html += `<div class="border rounded p-2" style="flex: 1 1 200px; min-width: 200px;">
					<label class="mb-1"><input type="radio" name="splinh-main" value="${esc(n)}" ${state.main === n ? "checked" : ""}>
						<b>${esc(__("Main Lead"))}</b></label>
					<div><a href="${frappe.utils.get_form_link("Lead", n)}" target="_blank" rel="noopener">${esc(n)}</a></div>
					<div class="small">${esc(l.title)}</div>
					<div class="small text-muted">${esc(l.status || "")} &middot; ${esc(l.owner || "")}</div>
					<div class="small text-muted">${chips.join(" &middot; ")}</div>
				</div>`;
			});
			html += `</div>`;

			html += `<div class="mb-2"><label class="small"><input type="checkbox" class="splinh-show-all" ${state.show_all ? "checked" : ""}>
				${esc(__("Show all fields (not only the ones that differ)"))}</label>
				<span class="text-muted small"> &middot; ${esc(__("{0} of {1} fields shown", [rows.length, preview.rows.length]))}</span></div>`;

			html += `<div style="max-height: 55vh; overflow: auto;"><table class="table table-bordered table-sm small mb-0"><thead style="position: sticky; top: 0; background: var(--card-bg, #fff); z-index: 1;"><tr><th style="min-width: 150px;">${esc(__("Field"))}</th>`;
			names.forEach((n) => {
				html += `<th style="min-width: 150px;">${esc(n)}${state.main === n ? ` <span class="badge badge-primary">${esc(__("main"))}</span>` : ""}</th>`;
			});
			html += `<th style="min-width: 170px;">${esc(__("Result in main Lead"))}</th></tr></thead><tbody>`;

			let section = null;
			rows.forEach((row) => {
				if (row.section !== section) {
					section = row.section;
					html += `<tr class="table-active"><td colspan="${names.length + 2}"><b>${esc(section || "")}</b></td></tr>`;
				}
				const use = used_lead(row);
				const appended = (state.append[row.fieldname] || []).filter((n) => n !== use);
				const result = result_of(row);
				const bad = too_long(row, result);
				if (bad) invalid += 1;
				html += `<tr><td>${esc(row.label)}<div class="text-muted" style="font-size: 10px;">${esc(row.fieldname)}</div></td>`;
				names.forEach((n) => {
					const is_use = use === n;
					const is_app = appended.includes(n);
					html += `<td ${is_use ? 'class="table-success"' : is_app ? 'class="table-info"' : ""}>
						<label class="d-block mb-0"><input type="radio" class="splinh-use" name="use-${esc(row.fieldname)}" data-field="${esc(row.fieldname)}" data-lead="${esc(n)}" ${is_use ? "checked" : ""}>
							${cell_text(row.values[n], row.fieldtype)}</label>
						${row.appendable && !is_use && norm(row.values[n]) ? `<label class="d-block mb-0 text-muted"><input type="checkbox" class="splinh-append" data-field="${esc(row.fieldname)}" data-lead="${esc(n)}" ${is_app ? "checked" : ""}> ${esc(__("Append"))}</label>` : ""}
					</td>`;
				});
				html += `<td ${bad ? 'class="table-danger"' : ""}>${cell_text(result, row.fieldtype)}${bad ? `<div class="text-danger">${esc(__("Longer than {0} characters", [row.length || DATA_MAX]))}</div>` : ""}</td></tr>`;
			});
			if (!rows.length) html += `<tr><td colspan="${names.length + 2}" class="text-muted">${esc(__("The selected Leads have identical values in every field."))}</td></tr>`;
			html += `</tbody></table></div>`;

			const $body = dialog.fields_dict.body.$wrapper;
			$body.html(html);
			dialog.set_primary_action(__("Merge {0} Leads into {1}", [names.length, state.main]), confirm_merge);
			dialog.get_primary_btn().prop("disabled", invalid > 0);

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
				if (this.dataset.lead === state.main) delete state.use[f];
				else state.use[f] = this.dataset.lead;
				state.append[f] = (state.append[f] || []).filter((n) => n !== this.dataset.lead);
				render();
			});
			$body.find(".splinh-append").on("change", function () {
				const f = this.dataset.field;
				const list = (state.append[f] || []).filter((n) => n !== this.dataset.lead);
				if (this.checked) list.push(this.dataset.lead);
				state.append[f] = list;
				render();
			});
		}

		function choices_payload() {
			const out = {};
			preview.rows.forEach((row) => {
				const use = state.use[row.fieldname];
				const append = (state.append[row.fieldname] || []).filter((n) => n !== (use || state.main));
				if (use || append.length) out[row.fieldname] = {use: use || state.main, append: append};
			});
			return out;
		}

		function confirm_merge() {
			const others = names.filter((n) => n !== state.main);
			frappe.confirm(
				__("Merge {0} into {1}? The other Lead(s) will be DELETED and everything connected to them moves to {1}. A copy of them is kept in the Lead Merge Log, but this cannot be undone from the screen.", [others.join(", "), state.main]),
				() => {
					frappe
						.call({
							method: "splinh.api.lead_merge.merge_leads",
							args: {names: names, main: state.main, choices: choices_payload(), stamps: preview.stamps},
							freeze: true,
							freeze_message: __("Merging..."),
						})
						.then((r) => {
							if (!r.message) return;
							dialog.hide();
							frappe.show_alert({message: __("Merged into {0}", [r.message.main]), indicator: "green"});
							const over = Object.keys(r.message.overridden || {});
							if (over.length) {
								frappe.msgprint({
									title: __("Merged - please check"),
									indicator: "orange",
									message: __("The Lead's own rules changed these fields when it was saved, so they differ from your choice: {0}. (For example a Lead that has a Customer is always Converted.)", [over.join(", ")]),
								});
							}
							opts.listview && opts.listview.clear_checked_items && opts.listview.clear_checked_items();
							if (opts.on_done) return opts.on_done(r.message.main);
							const open_form = window.cur_frm && cur_frm.doc && cur_frm.doc.doctype === "Lead" ? cur_frm : null;
							if (open_form && open_form.doc.name === r.message.main) open_form.reload_doc();
							else frappe.set_route("Form", "Lead", r.message.main);
						});
				},
			);
		}

		dialog.show();
		render();
	}
	}
})();
