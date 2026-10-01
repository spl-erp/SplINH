// Managed by splinh.custom.splinh_setup - the Page record itself is re-applied on
// every migrate, but THIS FILE is app code and survives a database restore untouched.

// Same 9 states/order as ISSUE_STATUSES in splinh_setup.py, and the same style
// palette as the Workflow States it enforces (see _ensure_issue_workflow). Kept as a
// literal here - duplicating a python list into JS is simpler and safer than exposing
// it through an API just for a label/colour lookup.
const ISSUE_PIPELINE_STAGES = [
	{ state: "Open", color: "gray" },
	{ state: "In Progress", color: "blue" },
	{ state: "Replied", color: "blue" },
	{ state: "With Provider", color: "orange" },
	{ state: "Provider Responded", color: "orange" },
	{ state: "On Hold", color: "blue" },
	{ state: "Resolved", color: "dark-green" },
	{ state: "Closed", color: "green" },
	{ state: "Cancelled", color: "red" },
];

frappe.pages["issue-pipeline"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Issue Pipeline Tracker",
		single_column: true,
	});

	const $body = $(page.body).empty();

	const $search_row = $(`
		<div class="frappe-card" style="padding: 15px; margin-bottom: 15px;">
			<div class="row">
				<div class="col-sm-6">
					<input type="text" class="form-control" id="issue-id-input"
						placeholder="Issue ID, e.g. ISS-2026-00001">
				</div>
				<div class="col-sm-2">
					<button class="btn btn-primary btn-sm" id="issue-fetch-btn" style="width: 100%;">
						Track
					</button>
				</div>
			</div>
		</div>
	`).appendTo($body);

	const $summary = $(`<div id="issue-summary"></div>`).appendTo($body);
	const $stages = $(`<div id="issue-stages" class="frappe-card" style="padding: 20px; margin-bottom: 15px; overflow-x: auto;"></div>`).appendTo($body);
	const $timeline = $(`<div id="issue-timeline" class="frappe-card" style="padding: 15px;"></div>`).appendTo($body);

	function render_stages(current_status) {
		const current_idx = ISSUE_PIPELINE_STAGES.findIndex((s) => s.state === current_status);
		let html = '<div style="display: flex; align-items: center; min-width: 900px;">';
		ISSUE_PIPELINE_STAGES.forEach((stage, idx) => {
			const is_current = idx === current_idx;
			const is_past = current_idx >= 0 && idx < current_idx;
			const opacity = is_current ? "1" : is_past ? "0.55" : "0.25";
			const border = is_current ? "3px solid var(--gray-900)" : "1px solid var(--gray-400)";
			html += `
				<div style="text-align:center; flex: 1;">
					<div class="indicator-pill ${stage.color}"
						style="opacity: ${opacity}; border: ${border}; padding: 6px 10px; font-weight: ${is_current ? "bold" : "normal"};">
						${frappe.utils.escape_html(stage.state)}
					</div>
				</div>`;
			if (idx < ISSUE_PIPELINE_STAGES.length - 1) {
				html += `<div style="flex: 0 0 20px; height: 2px; background: var(--gray-300); opacity: ${is_past ? "0.55" : "0.2"};"></div>`;
			}
		});
		html += "</div>";
		$stages.html(html);
	}

	function render_summary(data) {
		const modules = (data.modules || []).join(", ") || "-";
		$summary.html(`
			<div class="frappe-card" style="padding: 15px; margin-bottom: 15px;">
				<h4>${frappe.utils.escape_html(data.issue)}
					<span class="indicator-pill ${data.current_status === "Closed" ? "green" : data.current_status === "Cancelled" ? "red" : "blue"}" style="margin-left: 10px;">
						${frappe.utils.escape_html(data.current_status || "")}
					</span>
				</h4>
				<div class="text-muted">
					Raised by: ${frappe.utils.escape_html(data.raised_by || data.owner || "-")}
					&nbsp;|&nbsp; Modules: ${frappe.utils.escape_html(modules)}
					&nbsp;|&nbsp; Solution Provider:
					${data.solution_provider ? frappe.utils.escape_html(data.solution_provider) : "<em>IT (not escalated)</em>"}
				</div>
			</div>
		`);
	}

	function render_timeline(trace) {
		if (!trace || !trace.length) {
			$timeline.html('<div class="text-muted">No status transitions recorded yet.</div>');
			return;
		}
		let html = '<h5>Transition history</h5><table class="table table-bordered"><thead><tr>' +
			"<th>When</th><th>From</th><th>To</th><th>By</th></tr></thead><tbody>";
		trace.forEach((row) => {
			html += `<tr>
				<td>${frappe.datetime.str_to_user(row.timestamp)}</td>
				<td>${frappe.utils.escape_html(row.from_state || "-")}</td>
				<td>${frappe.utils.escape_html(row.to_state || "-")}</td>
				<td>${frappe.utils.escape_html(row.changed_by || "-")}</td>
			</tr>`;
		});
		html += "</tbody></table>";
		$timeline.html(html);
	}

	function fetch_issue() {
		const issue_name = $("#issue-id-input").val().trim();
		if (!issue_name) return;
		frappe.call({
			method: "splinh.custom.issue_tracking.get_issue_pipeline_trace",
			args: { issue_name },
			freeze: true,
			callback: function (r) {
				if (!r.message) return;
				render_summary(r.message);
				render_stages(r.message.current_status);
				render_timeline(r.message.trace);
			},
		});
	}

	page.set_primary_action("Track", fetch_issue);
	$("#issue-fetch-btn").on("click", fetch_issue);
	$("#issue-id-input").on("keydown", (e) => {
		if (e.key === "Enter") fetch_issue();
	});

	render_stages(null);
	$timeline.html('<div class="text-muted">Enter an Issue ID above to see its pipeline trace.</div>');
};
