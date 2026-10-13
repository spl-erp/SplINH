// Managed by splinh.custom.splinh_setup - the Page record itself is re-applied on
// every migrate, but THIS FILE is app code and survives a database restore untouched.
//
// Layer 3 of the KPI Capturing project: a filterable drill-down view of the Sales KPI
// Entry metrics, sitting alongside the Layer 1 Workspace ("KPI Dashboard") and the Layer 2
// Insights workbook. Real server-side access control lives in the whitelisted method this
// page calls - splinh.custom.kpi_dashboard_detail.get_dashboard_data - which rejects a
// Guest session and any logged-in user without Sales Manager or System Manager before
// touching a single row. This page's own `roles` (see the Page doctype record) is a
// second, independent gate at the Desk-routing level, same split as call_mon_detail.js.
//
// Grain: one row per (salesperson, day) - same grain as the Sales KPI Entry doctype/Web
// Form, computed here directly from the source tables (Lead/Call Log/Start-End
// Meeting/Sales KPI Entry) for a whole team over a date range, not from the doctype's own
// per-record virtual-field properties (see kpi_dashboard_detail.py's module docstring).
//
// Colour assignment (dataviz skill): same validated default categorical palette as
// call_mon_detail.js (references/palette.md), reused here rather than re-picked, so a
// shared hue (e.g. "Cold"/"blue") never means something different across the two pages.
const KPI_PALETTE = {
	light: { blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a", green: "#008300", muted: "#898781" },
	dark: { blue: "#3987e5", orange: "#d95926", aqua: "#199e70", green: "#008300", muted: "#898781" },
};

function kpi_is_dark() {
	return document.documentElement.getAttribute("data-theme") === "dark";
}
function kpi_colors() {
	return kpi_is_dark() ? KPI_PALETTE.dark : KPI_PALETTE.light;
}

const KPI_BUCKET_HUE = {
	"Cold Call": "orange", "Followup Call": "blue",
	"Visit Existing": "aqua", "Visit New": "green",
};
function kpi_color_for(label) {
	const colors = kpi_colors();
	return colors[KPI_BUCKET_HUE[label]] || colors.muted;
}

const KPI_PAGE_LENGTH = 50;

// Tile definitions for the summary row, in display order - no click-to-filter on this
// page (unlike Call Monitoring): there are only two togglable dimensions here (date
// range, employee), both already directly editable via the filter row above, so a tile
// click would have nothing further to toggle.
const KPI_TILES = [
	{ label: "Lead Generation", data_key: "lead_generation_count" },
	{ label: "Lead Score (avg)", data_key: "lead_score" },
	{ label: "Lead Qualification", data_key: "lead_qualification_count" },
	{ label: "Qualification Score (avg)", data_key: "lead_qualification_score" },
	{ label: "Cold Call", data_key: "cold_call_count" },
	{ label: "Followup Call", data_key: "followup_call_count" },
	{ label: "WhatsApp Calling", data_key: "whatsapp_call_count" },
	{ label: "Visit Existing", data_key: "visit_existing_count" },
	{ label: "Visit New", data_key: "visit_new_count" },
	{ label: "Meeting Duration (min)", data_key: "meeting_duration_minutes" },
];

const KPI_TABLE_COLUMNS = [
	{ label: "Date", key: "date", fmt: "date" },
	{ label: "User", key: "user" },
	{ label: "Lead Gen", key: "lead_generation_count" },
	{ label: "Lead Score", key: "lead_score" },
	{ label: "Lead Qual", key: "lead_qualification_count" },
	{ label: "Qual Score", key: "lead_qualification_score" },
	{ label: "Cold", key: "cold_call_count" },
	{ label: "Followup", key: "followup_call_count" },
	{ label: "WhatsApp Call", key: "whatsapp_call_count" },
	{ label: "Visit Existing", key: "visit_existing_count" },
	{ label: "Visit New", key: "visit_new_count" },
	{ label: "Meeting (min)", key: "meeting_duration_minutes" },
	{ label: "Office Meeting (min)", key: "office_meeting_duration" },
	{ label: "Training (min)", key: "training_duration" },
	{ label: "Complaints (No.)", key: "customer_complaints_count" },
	{ label: "Complaints (min)", key: "customer_complaints_duration" },
	{ label: "Reporting (min)", key: "reporting_duration" },
	{ label: "Google Meet (No.)", key: "google_meet_count" },
	{ label: "Google Meet (min)", key: "google_meet_duration" },
	{ label: "WhatsApp Chat (No.)", key: "whatsapp_chat_count" },
	{ label: "WhatsApp Chat (min)", key: "whatsapp_chat_duration" },
];

frappe.pages["kpi-dashboard-detail"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "KPI Dashboard",
		single_column: true,
	});

	new KPIDashboardDetailed(page);
};

class KPIDashboardDetailed {
	constructor(page) {
		this.page = page;
		this.state = { from_date: null, to_date: null, employee: null, start: 0 };
		this.filter_options_loaded = false;
		this.$body = $(page.body).empty();
		this.inject_styles();
		this.render_shell();
		this.fetch();
	}

	inject_styles() {
		if (document.getElementById("kpi-detailed-style")) return;
		$("<style>", { id: "kpi-detailed-style" }).text(`
			.kpi-tile { padding: 14px; text-align: center; }
			.kpi-loading-target { transition: opacity .15s ease; }
			.kpi-loading-target.kpi-loading { opacity: .5; pointer-events: none; }
			.kpi-chart-empty { height: 220px; display: flex; align-items: center; justify-content: center; text-align: center; }
			.kpi-table-wrap { max-height: 520px; overflow: auto; }
		`).appendTo("head");
	}

	render_shell() {
		this.$body.html(`
			<div class="kpi-detailed">
				<div class="frappe-card kpi-filters" style="padding: 15px; margin-bottom: 15px;">
					<div class="row" style="row-gap: 10px;">
						<div class="col-sm-3">
							<label class="control-label">From</label>
							<input type="date" class="form-control input-sm" data-filter="from_date">
						</div>
						<div class="col-sm-3">
							<label class="control-label">To</label>
							<input type="date" class="form-control input-sm" data-filter="to_date">
						</div>
						<div class="col-sm-4">
							<label class="control-label">Employee</label>
							<select class="form-control input-sm" data-filter="employee">
								<option value="">All</option>
							</select>
						</div>
						<div class="col-sm-2" style="display: flex; align-items: flex-end;">
							<button class="btn btn-xs btn-default" id="kpi-reset-btn" style="width: 100%;">Reset</button>
						</div>
					</div>
				</div>

				<div class="kpi-summary kpi-loading-target" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 15px;"></div>

				<div class="kpi-charts kpi-loading-target" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 15px; margin-bottom: 15px;">
					${this.chart_block("lead_trend", "Lead Generation, Per Day")}
					${this.chart_block("call_bucket", "Cold vs Followup Call")}
					${this.chart_block("visit_bucket", "Visit Existing vs New")}
				</div>

				<div class="frappe-card kpi-drilldown kpi-loading-target" style="padding: 15px;">
					<div class="d-flex align-items-center" style="justify-content: space-between; margin-bottom: 10px;">
						<h5 style="margin: 0;">Per-salesperson, per-day detail</h5>
						<div id="kpi-page-info" class="text-muted small"></div>
					</div>
					<div class="kpi-table-wrap">
						<table class="table table-bordered table-sm" id="kpi-table">
							<thead><tr>${KPI_TABLE_COLUMNS.map((c) => `<th>${frappe.utils.escape_html(c.label)}</th>`).join("")}</tr></thead>
							<tbody></tbody>
						</table>
					</div>
					<div class="text-right">
						<button class="btn btn-xs btn-default" id="kpi-prev-btn">&laquo; Prev</button>
						<button class="btn btn-xs btn-default" id="kpi-next-btn">Next &raquo;</button>
					</div>
				</div>
			</div>
		`);

		this.$body.find("[data-filter]").on("change", (e) => {
			const $el = $(e.currentTarget);
			this.update_state({ [$el.data("filter")]: $el.val() || null });
		});
		this.$body.find("#kpi-reset-btn").on("click", () => this.reset_filters());
		this.$body.find("#kpi-prev-btn").on("click", () => {
			this.state.start = Math.max(0, this.state.start - KPI_PAGE_LENGTH);
			this.fetch();
		});
		this.$body.find("#kpi-next-btn").on("click", () => {
			if (this.state.start + KPI_PAGE_LENGTH < (this.last_total_rows || 0)) {
				this.state.start += KPI_PAGE_LENGTH;
				this.fetch();
			}
		});
	}

	chart_block(key, title) {
		return `
			<div class="frappe-card kpi-chart-card" data-chart="${key}" style="padding: 15px;">
				<h6 style="margin: 0;">${frappe.utils.escape_html(title)}</h6>
				<div class="kpi-chart-canvas" style="margin-top: 10px;"></div>
			</div>
		`;
	}

	update_state(patch) {
		Object.assign(this.state, patch, { start: 0 });
		this.fetch();
	}

	reset_filters() {
		this.state = { from_date: null, to_date: null, employee: null, start: 0 };
		this.sync_controls();
		this.fetch();
	}

	sync_controls() {
		this.$body.find('input[data-filter="from_date"]').val(this.state.from_date || "");
		this.$body.find('input[data-filter="to_date"]').val(this.state.to_date || "");
		this.$body.find('select[data-filter="employee"]').val(this.state.employee || "");
	}

	fetch() {
		this.$body.find(".kpi-loading-target").addClass("kpi-loading");
		frappe.call({
			method: "splinh.custom.kpi_dashboard_detail.get_dashboard_data",
			args: { filters: this.state, start: this.state.start, page_length: KPI_PAGE_LENGTH },
			callback: (r) => {
				this.$body.find(".kpi-loading-target").removeClass("kpi-loading");
				if (!r.message) return;
				this.render_result(r.message);
			},
			error: () => {
				this.$body.find(".kpi-loading-target").removeClass("kpi-loading");
				this.$body.find(".kpi-summary").html(
					'<div class="text-danger">Could not load KPI Dashboard data - you may not have permission to view this page.</div>'
				);
			},
		});
	}

	render_result(data) {
		this.last_total_rows = data.total_rows || 0;
		if (!this.filter_options_loaded) {
			this.populate_filter_options(data.filter_options || {});
			this.filter_options_loaded = true;
			this.sync_controls();
		}
		this.render_summary(data.summary || {});
		this.render_trend(data.charts.lead_trend);
		this.render_donut("call_bucket", data.charts.call_bucket);
		this.render_donut("visit_bucket", data.charts.visit_bucket);
		this.render_table(data.rows || []);
	}

	populate_filter_options(options) {
		const $employee = this.$body.find('select[data-filter="employee"]');
		(options.employees || []).forEach((row) => {
			if (!row.user) return;
			$employee.append(`<option value="${frappe.utils.escape_html(row.user)}">${frappe.utils.escape_html(row.user)}</option>`);
		});
	}

	render_summary(summary) {
		this.$body.find(".kpi-summary").html(
			KPI_TILES.map((tile) => {
				const value = summary[tile.data_key];
				return `
					<div class="frappe-card kpi-tile">
						<div style="font-size: 22px; font-weight: 600; font-variant-numeric: proportional-nums;">${value ?? 0}</div>
						<div class="text-muted small">${frappe.utils.escape_html(tile.label)}</div>
					</div>`;
			}).join("")
		);
	}

	render_donut(key, rows) {
		rows = rows || [];
		const $card = this.$body.find(`.kpi-chart-card[data-chart="${key}"]`);
		const $canvas = $card.find(".kpi-chart-canvas");
		const total = rows.reduce((a, r) => a + (r.value || 0), 0);
		if (!total) {
			$canvas.html('<div class="kpi-chart-empty text-muted">No data for the current filters.</div>');
			return;
		}
		const labels = rows.map((r) => r.label);
		const values = rows.map((r) => cint(r.value));
		const colors = labels.map((l) => kpi_color_for(l));
		$canvas.empty();
		// eslint-disable-next-line no-new
		new frappe.Chart($canvas[0], {
			data: { labels, datasets: [{ values }] },
			type: "donut", height: 220, colors, maxSlices: 8, truncateLegends: 0,
		});
	}

	render_trend(rows) {
		rows = rows || [];
		const $card = this.$body.find('.kpi-chart-card[data-chart="lead_trend"]');
		const $canvas = $card.find(".kpi-chart-canvas");
		if (!rows.length) {
			$canvas.html('<div class="kpi-chart-empty text-muted">No data for the current filters.</div>');
			return;
		}
		const labels = rows.map((r) => frappe.datetime.str_to_user(r.day, true));
		const values = rows.map((r) => cint(r.value));
		$canvas.empty();
		// eslint-disable-next-line no-new
		new frappe.Chart($canvas[0], {
			data: { labels, datasets: [{ name: "Leads", values }] },
			type: "line", height: 220, colors: [kpi_colors().blue],
			axisOptions: { xIsSeries: 1 }, lineOptions: { regionFill: 0, hideDots: 0 },
		});
	}

	render_table(rows) {
		const $tbody = this.$body.find("#kpi-table tbody").empty();
		if (!rows.length) {
			$tbody.html(`<tr><td colspan="${KPI_TABLE_COLUMNS.length}" class="text-muted text-center" style="padding: 24px 0;">No entries match the current filters.</td></tr>`);
		} else {
			rows.forEach((row) => {
				$tbody.append(
					`<tr>${KPI_TABLE_COLUMNS.map((c) => {
						let value = row[c.key];
						if (c.fmt === "date" && value) value = frappe.datetime.str_to_user(value);
						return `<td>${frappe.utils.escape_html(String(value ?? "-"))}</td>`;
					}).join("")}</tr>`
				);
			});
		}
		const from = this.last_total_rows ? this.state.start + 1 : 0;
		const to = Math.min(this.state.start + KPI_PAGE_LENGTH, this.last_total_rows || 0);
		this.$body.find("#kpi-page-info").text(this.last_total_rows ? `${from}-${to} of ${this.last_total_rows}` : "");
		this.$body.find("#kpi-prev-btn").prop("disabled", this.state.start === 0);
		this.$body.find("#kpi-next-btn").prop("disabled", this.state.start + KPI_PAGE_LENGTH >= (this.last_total_rows || 0));
	}
}

function cint(v) {
	return frappe.utils.cint ? frappe.utils.cint(v) : parseInt(v, 10) || 0;
}
