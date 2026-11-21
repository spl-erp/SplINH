// Managed by splinh.custom.splinh_setup - edits here are overwritten on migrate.
// "Possible duplicates" banner on the Lead form (2026-11-13): when other Leads share this Lead's phone, email or company name,
// a slim banner lists them with a Merge link that opens the Merge Leads dialog (window.splinh_lead_merge, defined by
// lead_merge_list.js which is installed in the same Client Script). Server side: splinh.api.lead_duplicates.similar_leads
// (read-only). Never blocks the form: any failure shows no banner.
(() => {
	const done = {};
	const esc = (text) => frappe.utils.escape_html(text == null ? "" : String(text));
	const REASON = {phone: __("phone number"), email: __("email"), company: __("company name")};

	frappe.ui.form.on("Lead", {
		refresh(frm) {
			try {
				remove_banner(frm);
				if (frm.is_new() || !frm.perm || !frm.perm[0] || !frm.perm[0].write) return;
				const key = frm.doc.name + "|" + frm.doc.modified;
				if (done[key] !== undefined) return show(frm, done[key]);
				frappe.call({
					method: "splinh.api.lead_duplicates.similar_leads",
					type: "GET",
					args: {lead: frm.doc.name},
					silent: true,
					callback: (r) => {
						if (!r || !r.message) return;
						done[key] = r.message.matches || [];
						if (frm.doc && frm.doc.name === r.message.lead) show(frm, done[key]);
					},
					error: () => {},
				});
			} catch (e) {
				console.error("Possible duplicates banner failed", e);
			}
		},
	});

	function remove_banner(frm) {
		frm.layout && frm.layout.wrapper && frm.layout.wrapper.find(".splinh-dup-banner").remove();
	}

	function show(frm, matches) {
		remove_banner(frm);
		if (!matches.length || !window.splinh_lead_merge || !frm.layout || !frm.layout.wrapper) return;
		const reasons = [];
		matches.forEach((m) => (m.matched_on || []).forEach((x) => REASON[x] && !reasons.includes(x) && reasons.push(x)));
		const links = matches
			.slice(0, 5)
			.map((m) => `<a href="${frappe.utils.get_form_link("Lead", m.name)}" target="_blank" rel="noopener">${esc(m.title)}</a> <span class="text-muted">(${esc(m.name)}${m.status ? ", " + esc(__(m.status)) : ""})</span>`)
			.join(", ");
		const more = matches.length > 5 ? ` ${esc(__("and {0} more", [matches.length - 5]))}` : "";
		const text = esc(__("{0} other Lead(s) share this Lead's {1}:", [matches.length, reasons.map((x) => REASON[x]).join(", ")]));
		const $banner = $(`<div class="alert alert-warning splinh-dup-banner d-flex flex-wrap align-items-center mb-3" style="gap: 8px;">
			<div style="flex: 1 1 300px; min-width: 0;">${text} ${links}${more}</div>
			<button class="btn btn-warning btn-xs splinh-dup-merge">${esc(__("Merge"))}</button>
		</div>`);
		$banner.find(".splinh-dup-merge").on("click", () => {
			if (frm.is_dirty()) {
				frappe.msgprint({title: __("Merge Leads"), message: __("Save this Lead first, then choose Merge again."), indicator: "orange"});
				return;
			}
			const names = [frm.doc.name].concat(matches.slice(0, 3).map((m) => m.name));
			if (matches.length > 3) frappe.show_alert({message: __("Only 4 Leads can be merged at once; merge again afterwards for the rest."), indicator: "orange"});
			window.splinh_lead_merge.open(names, {main: frm.doc.name});
		});
		frm.layout.wrapper.prepend($banner);
	}
})();
