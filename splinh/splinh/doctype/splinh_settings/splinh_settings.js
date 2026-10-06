// "Test connection": asks our server to try the key against Google's free model-list
// call. A newly typed key is tested as typed; an already-saved (masked) key is tested
// from the encrypted copy on the server. Nothing paid is called and the key is never sent back.
frappe.ui.form.on("SplINH Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test connection"), () => {
			frappe
				.call({
					method: "splinh.api.call_ai.test_gemini_key",
					args: {api_key: frm.doc.gemini_api_key || ""},
					freeze: true,
					freeze_message: __("Asking Google..."),
				})
				.then((r) => {
					const result = r.message || {};
					frappe.msgprint({
						title: result.ok ? __("Key works") : __("Key problem"),
						message: frappe.utils.escape_html(result.message || ""),
						indicator: result.ok ? "green" : "red",
					});
				});
		});
	},
});
