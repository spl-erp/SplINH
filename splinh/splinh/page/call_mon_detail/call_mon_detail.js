// Managed by splinh.custom.splinh_setup - the Page record itself is re-applied on
// every migrate, but THIS FILE is app code and survives a database restore untouched.
//
// Layer 3 of the Call Monitoring project: a fully filterable, cross-filterable drill-down
// view, sitting alongside (not replacing) the Layer 1 Workspace Number Cards/Charts ("Call
// Monitoring") and the Layer 2 Insights workbook. Real server-side access control lives in
// the whitelisted method this page calls - splinh.custom.call_monitoring_detail.get_dashboard_data
// - which rejects a Guest session and any logged-in user without Call Tracker Manager or
// System Manager before touching a single Call Log row. This page's own `roles` (see the
// Page doctype record) is a second, independent gate at the Desk-routing level - a user
// without either role can't even open /app/call-mon-detail - but the API-level check is the
// one that actually matters and is never skipped regardless.
//
// COMPANY SCOPING (page-wide lens, added after first live review) - "Total calls" is the
// ONLY tile drawn from the unscoped grand total; every other tile, every chart and the
// drill-down table are already company-scoped by the server (see the .py module's
// docstring) before this file ever sees the numbers. There is deliberately no client-side
// toggle for it - it's a fixed lens, not one of the six togglable filter dimensions below.
//
// Colour assignment (dataviz skill): every entity below keeps ONE fixed hex for as long as
// it appears anywhere on the page - never reassigned by rank/frequency, never cycled, and
// never independently re-picked per element. `CM_ENTITY_HUE` is the SINGLE shared
// entity -> hue mapping used by every donut, every active-filter pill, and every drill-down
// table badge - there is no second copy of this assignment anywhere in this file. The 8 hex
// values per theme are the skill's validated default categorical palette (see
// references/palette.md) - both light and dark steps passed `validate_palette.js` for this
// build (worst adjacent CVD Delta E 9.1 light / 8.4 dark, normal-vision floor 19.6 light /
// 19.3 dark - both clear of the >=8 / >=15 gates; re-validated again after adding the pill/
// badge usages below - same hex set, no new hues introduced, so the result is unchanged).
// The light-mode contrast WARN on 3 of the 8 slots is why every donut here ships a companion
// data table (direct values, not color-only) alongside the chart - the "relief" requirement.
const CM_PALETTE = {
	light: {
		blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a", yellow: "#eda100",
		magenta: "#e87ba4", green: "#008300", violet: "#4a3aa7", red: "#e34948",
		muted: "#898781",
	},
	dark: {
		blue: "#3987e5", orange: "#d95926", aqua: "#199e70", yellow: "#c98500",
		magenta: "#d55181", green: "#008300", violet: "#9085e9", red: "#e66767",
		muted: "#898781",
	},
};

function cm_is_dark() {
	return document.documentElement.getAttribute("data-theme") === "dark";
}
function cm_colors() {
	return cm_is_dark() ? CM_PALETTE.dark : CM_PALETTE.light;
}

// ONE shared entity -> hue mapping (see module comment above). Direction, source and status
// values never collide as strings, so merging all three dimensions into one object is safe
// and is exactly what "one shared color-mapping object, not re-picked per element" means in
// practice: the same label always resolves to the same hue everywhere it appears, whichever
// chart/pill/table cell it came from. A label not listed here falls back to `muted` grey
// rather than borrowing another entity's colour.
const CM_ENTITY_HUE = {
	// Direction
	Incoming: "blue", Outgoing: "orange", Unknown: "yellow",
	// Source
	SIM: "blue", WhatsApp: "aqua",
	// Status (bucketed labels - see call_monitoring_detail.py docstring)
	Completed: "blue", "No Answer": "orange", Busy: "aqua", Missed: "red",
	Failed: "yellow", Cancelled: "magenta", Queued: "green", Ringing: "violet",
	"In Progress": "violet", Unspecified: "muted",
};

function cm_color_for(label) {
	const colors = cm_colors();
	return colors[CM_ENTITY_HUE[label]] || colors.muted;
}

const CM_PAGE_LENGTH = 50;

// Direction "Unknown" - a real, honestly-reported detector limitation (requirement #4):
// a WhatsApp call notification doesn't reveal whether it was incoming or outgoing, so those
// rows are stored with an unresolved direction rather than a guessed one. Shown as a static
// caption (not hover-only) near the Direction filter and under the Direction chart title,
// per the dataviz skill's "tooltips enhance, they never gate" rule.
const CM_UNKNOWN_DIRECTION_NOTE =
	"\"Unknown\" = WhatsApp calls where the notification didn't reveal direction - a detector limitation, not a data error.";

// Tile definitions for the summary row, in display order. `filter_key`/`filter_value` is the
// single filter dimension a click on that tile toggles - see the reasoning in each tile's
// `note` for the two tiles that intentionally have neither.
const CM_TILES = [
	{
		label: "Total calls", data_key: "total_calls",
		note: "All calls, unscoped - the one number on this page not limited to Company calls.",
	},
	{
		label: "Company calls", data_key: "company_calls",
		note: "customer or lead set - the fixed base scope for everything below.",
	},
	{ label: "Missed calls", data_key: "missed_calls", filter_key: "status", filter_value: "Missed" },
	{ label: "Outgoing", data_key: "outgoing_calls", filter_key: "direction", filter_value: "Outgoing" },
	{ label: "Incoming", data_key: "incoming_calls", filter_key: "direction", filter_value: "Incoming" },
	{ label: "WhatsApp", data_key: "whatsapp_calls", filter_key: "source", filter_value: "WhatsApp" },
	{ label: "Phone (SIM)", data_key: "phone_calls", filter_key: "source", filter_value: "SIM" },
];
// Reasoning for the two non-clickable tiles (requirement 3b): "Total calls" isn't
// company-scoped like everything else it would sit alongside once filtered, and doesn't
// correspond to any single filter value - there's no togglable dimension called "total".
// "Company calls" WAS a clickable idea before company-scoping became the fixed page lens;
// now every other tile/chart/row is already limited to company calls unconditionally, so a
// click on this tile would have nothing left to toggle - it no longer names a filter value,
// it names the base condition itself. Both stay visible (with a one-line caption explaining
// why) rather than forcing an arbitrary click behaviour onto them.

frappe.pages["call-mon-detail"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Call Monitoring",
		single_column: true,
	});

	new CallMonitoringDetailed(page);
};

class CallMonitoringDetailed {
	constructor(page) {
		this.page = page;
		// Requirement 3a: ONE shared filter state object, driving the dropdown panel AND
		// every chart/tile. Company-scoping is a separate, non-togglable base condition
		// enforced server-side - it deliberately has no key here.
		this.state = {
			from_date: null,
			to_date: null,
			employee: null,
			source: null,
			direction: null,
			status: null,
			start: 0,
		};
		this.filter_options_loaded = false;
		this.employee_options = [];
		this.$body = $(page.body).empty();
		this.inject_styles();
		this.render_shell();
		this.fetch();
	}

	inject_styles() {
		if (document.getElementById("cm-detailed-style")) return;
		$("<style>", { id: "cm-detailed-style" }).text(`
			.cm-active-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; min-height: 26px; }
			.cm-pill { display: inline-flex; align-items: center; gap: 5px; padding: 3px 6px 3px 10px; border-radius: 12px;
				background: var(--control-bg); border: 1px solid var(--border-color); font-size: 12px; color: var(--text-color); }
			.cm-pill-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
			.cm-pill-remove { margin-left: 2px; padding: 0 3px; color: var(--text-muted); text-decoration: none; font-weight: 700; line-height: 1; }
			.cm-pill-remove:hover { color: var(--text-color); }
			.cm-tile { position: relative; }
			.cm-tile[data-clickable="1"] { cursor: pointer; transition: box-shadow .12s ease; }
			.cm-tile[data-clickable="1"]:hover { box-shadow: 0 0 0 1px var(--border-color); }
			.cm-tile.cm-tile-active { box-shadow: 0 0 0 2px var(--cm-tile-color, var(--border-color)) inset; }
			.cm-tile-note { font-size: 10px; margin-top: 2px; line-height: 1.3; }
			.cm-loading-target { transition: opacity .15s ease; }
			.cm-loading-target.cm-loading { opacity: .5; pointer-events: none; }
			.cm-chart-card[data-clickable="1"] .donut-path { cursor: pointer; }
			.cm-chart-empty { height: 220px; display: flex; align-items: center; justify-content: center; text-align: center; }
			.cm-badge-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
			.cm-info-note { font-size: 11px; color: var(--text-muted); margin-top: 3px; }
			.cm-filter-label-row { display: flex; align-items: center; gap: 4px; }
			.cm-info-icon { cursor: help; color: var(--text-muted); font-size: 11px; border: 1px solid var(--border-color); border-radius: 50%;
				width: 14px; height: 14px; display: inline-flex; align-items: center; justify-content: center; line-height: 1; }
		`).appendTo("head");
	}

	render_shell() {
		this.$body.html(`
			<div class="cm-detailed">
				<div class="frappe-card cm-filters" style="padding: 15px; margin-bottom: 15px;">
					<div class="row" style="row-gap: 10px;">
						<div class="col-sm-2">
							<label class="control-label">From</label>
							<input type="date" class="form-control input-sm" data-filter="from_date">
						</div>
						<div class="col-sm-2">
							<label class="control-label">To</label>
							<input type="date" class="form-control input-sm" data-filter="to_date">
						</div>
						<div class="col-sm-2">
							<label class="control-label">Employee</label>
							<select class="form-control input-sm" data-filter="employee">
								<option value="">All</option>
							</select>
						</div>
						<div class="col-sm-2">
							<label class="control-label">Source</label>
							<select class="form-control input-sm" data-filter="source">
								<option value="">All</option>
								<option value="SIM">Phone (SIM)</option>
								<option value="WhatsApp">WhatsApp</option>
							</select>
						</div>
						<div class="col-sm-2">
							<div class="cm-filter-label-row">
								<label class="control-label" style="margin-bottom: 0;">Direction</label>
								<span class="cm-info-icon" title="${frappe.utils.escape_html(CM_UNKNOWN_DIRECTION_NOTE)}">i</span>
							</div>
							<select class="form-control input-sm" data-filter="direction">
								<option value="">All</option>
								<option value="Incoming">Incoming</option>
								<option value="Outgoing">Outgoing</option>
								<option value="Unknown">Unknown</option>
							</select>
						</div>
						<div class="col-sm-2">
							<label class="control-label">Status</label>
							<select class="form-control input-sm" data-filter="status">
								<option value="">All</option>
							</select>
						</div>
					</div>
				</div>

				<div class="cm-active-filters"></div>
				<div style="margin: -6px 0 15px; text-align: right;">
					<button class="btn btn-xs btn-default" id="cm-reset-btn">Reset filters</button>
				</div>

				<div class="cm-summary cm-loading-target" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 15px;"></div>

				<div class="cm-charts cm-loading-target" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 15px; margin-bottom: 15px;">
					${this.chart_block("direction", "Incoming vs Outgoing", { clickable: true, caption: CM_UNKNOWN_DIRECTION_NOTE })}
					${this.chart_block("source", "WhatsApp vs Phone (SIM)", { clickable: true })}
					${this.chart_block("status", "Status Breakdown (Missed merged)", { clickable: true })}
					${this.chart_block("trend", "Calls Per Day", { is_line: true, clickable: true })}
				</div>

				<div class="frappe-card cm-drilldown cm-loading-target" style="padding: 15px;">
					<div class="d-flex align-items-center" style="justify-content: space-between; margin-bottom: 10px;">
						<h5 style="margin: 0;">Call Log detail</h5>
						<div id="cm-page-info" class="text-muted small"></div>
					</div>
					<div class="table-responsive">
						<table class="table table-bordered table-sm" id="cm-table">
							<thead>
								<tr>
									<th>Date/Time</th><th>Direction</th><th>Source</th><th>Status</th>
									<th>Linked Party</th><th>Employee</th><th>Duration</th><th>Call Summary</th>
								</tr>
							</thead>
							<tbody></tbody>
						</table>
					</div>
					<div class="text-right">
						<button class="btn btn-xs btn-default" id="cm-prev-btn">&laquo; Prev</button>
						<button class="btn btn-xs btn-default" id="cm-next-btn">Next &raquo;</button>
					</div>
				</div>
			</div>
		`);

		// Every control funnels through `update_state` so exactly one fetch happens per
		// change (requirement 3c), whichever direction the change came from (3e).
		this.$body.find("[data-filter]").on("change", (e) => {
			const $el = $(e.currentTarget);
			const key = $el.data("filter");
			this.update_state({ [key]: $el.val() || null });
		});
		this.$body.find("#cm-reset-btn").on("click", () => this.reset_filters());
		this.$body.find("#cm-prev-btn").on("click", () => {
			this.state.start = Math.max(0, this.state.start - CM_PAGE_LENGTH);
			this.fetch();
		});
		this.$body.find("#cm-next-btn").on("click", () => {
			if (this.state.start + CM_PAGE_LENGTH < (this.last_total_rows || 0)) {
				this.state.start += CM_PAGE_LENGTH;
				this.fetch();
			}
		});

		this.render_active_filters();
	}

	chart_block(key, title, opts) {
		opts = opts || {};
		return `
			<div class="frappe-card cm-chart-card" data-chart="${key}" data-clickable="${opts.clickable ? "1" : "0"}" style="padding: 15px;">
				<div class="d-flex align-items-center" style="justify-content: space-between;">
					<h6 style="margin: 0;">${frappe.utils.escape_html(title)}</h6>
					<a href="#" class="cm-toggle-table small" data-chart="${key}">View data</a>
				</div>
				${opts.caption ? `<div class="cm-info-note">${frappe.utils.escape_html(opts.caption)}</div>` : ""}
				<div class="cm-chart-canvas" style="margin-top: 10px;"></div>
				<div class="cm-chart-table" style="display: none; margin-top: 10px;"></div>
			</div>
		`;
	}

	// --- Shared state plumbing (requirement 3) ---------------------------------------

	/** Every state mutation - dropdown change, chart click, tile click, pill removal -
	 * goes through here: merge the patch, reset paging, keep the controls/pills in visual
	 * sync, then fire exactly ONE backend call. */
	update_state(patch) {
		Object.assign(this.state, patch, { start: 0 });
		this.sync_controls();
		this.render_active_filters();
		this.fetch();
	}

	/** Click-to-toggle helper: clicking the currently-active value again clears it. */
	toggle_filter(key, value) {
		this.update_state({ [key]: this.state[key] === value ? null : value });
	}

	toggle_date(day) {
		if (this.state.from_date === day && this.state.to_date === day) {
			this.update_state({ from_date: null, to_date: null });
		} else {
			this.update_state({ from_date: day, to_date: day });
		}
	}

	reset_filters() {
		this.state = {
			from_date: null, to_date: null, employee: null,
			source: null, direction: null, status: null, start: 0,
		};
		this.sync_controls();
		this.render_active_filters();
		this.fetch();
	}

	/** Dropdown/date-input values always mirror `this.state`, however it changed
	 * (requirement 3e) - covers the click -> dropdown direction; the dropdown -> click
	 * direction already goes through `update_state` above. */
	sync_controls() {
		this.$body.find('input[data-filter="from_date"]').val(this.state.from_date || "");
		this.$body.find('input[data-filter="to_date"]').val(this.state.to_date || "");
		this.$body.find('select[data-filter="employee"]').val(this.state.employee || "");
		this.$body.find('select[data-filter="source"]').val(this.state.source || "");
		this.$body.find('select[data-filter="direction"]').val(this.state.direction || "");
		this.$body.find('select[data-filter="status"]').val(this.state.status || "");
	}

	/** Prominent, individually-removable active-filter pills (requirement 3d). Colour-coded
	 * dimensions (source/direction/status) reuse the exact same `cm_color_for` used by the
	 * charts and the table - never a separately picked pill colour. */
	render_active_filters() {
		const s = this.state;
		const pills = [];

		if (s.from_date || s.to_date) {
			let label;
			if (s.from_date && s.to_date && s.from_date === s.to_date) {
				label = `Date: ${frappe.datetime.str_to_user(s.from_date)}`;
			} else {
				const from_label = s.from_date ? frappe.datetime.str_to_user(s.from_date) : "…";
				const to_label = s.to_date ? frappe.datetime.str_to_user(s.to_date) : "…";
				label = `Date: ${from_label} to ${to_label}`;
			}
			pills.push({ label, keys: { from_date: null, to_date: null } });
		}
		if (s.employee) {
			const row = this.employee_options.find((e) => e.employee === s.employee);
			const name = row && row.employee_name ? row.employee_name : s.employee;
			pills.push({ label: `Employee: ${name}`, keys: { employee: null } });
		}
		if (s.source) pills.push({ label: `Source: ${s.source}`, keys: { source: null }, hue: cm_color_for(s.source) });
		if (s.direction) pills.push({ label: `Direction: ${s.direction}`, keys: { direction: null }, hue: cm_color_for(s.direction) });
		if (s.status) pills.push({ label: `Status: ${s.status}`, keys: { status: null }, hue: cm_color_for(s.status) });

		const $wrap = this.$body.find(".cm-active-filters");
		if (!pills.length) {
			$wrap.html('<span class="text-muted small">No filters applied - click a chart segment or tile, or use the dropdowns above.</span>');
			return;
		}
		$wrap.html(
			pills
				.map(
					(p, i) => `
				<span class="cm-pill" data-pill-index="${i}">
					${p.hue ? `<span class="cm-pill-dot" style="background: ${p.hue};"></span>` : ""}
					${frappe.utils.escape_html(p.label)}
					<a href="#" class="cm-pill-remove" title="Remove filter">&times;</a>
				</span>`
				)
				.join("")
		);
		$wrap.find(".cm-pill-remove").on("click", (e) => {
			e.preventDefault();
			const idx = $(e.currentTarget).closest(".cm-pill").data("pill-index");
			this.update_state(pills[idx].keys);
		});
	}

	// --- Fetch / render -----------------------------------------------------------------

	fetch() {
		// Refetch keeps the frame (dataviz interaction.md): dim the existing render instead
		// of a full-page freeze/skeleton, so nothing flashes or jumps while data reloads.
		this.$body.find(".cm-loading-target").addClass("cm-loading");
		frappe.call({
			method: "splinh.custom.call_monitoring_detail.get_dashboard_data",
			args: {
				filters: this.state,
				start: this.state.start,
				page_length: CM_PAGE_LENGTH,
			},
			callback: (r) => {
				this.$body.find(".cm-loading-target").removeClass("cm-loading");
				if (!r.message) return;
				this.render_result(r.message);
			},
			error: () => {
				this.$body.find(".cm-loading-target").removeClass("cm-loading");
				this.$body.find(".cm-summary").html(
					'<div class="text-danger">Could not load Call Monitoring data - you may not have permission to view this page.</div>'
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
		// One response, one render pass across every tile/chart/table (requirement 3c) -
		// nothing here is rendered from a second, independent call.
		this.render_summary(data.summary || {});
		this.render_chart("direction", data.charts.direction, "donut", "direction");
		this.render_chart("source", data.charts.source, "donut", "source");
		this.render_chart("status", data.charts.status, "donut", "status");
		this.render_trend(data.charts.trend);
		this.render_table(data.rows || []);
		this.bind_table_toggles();
	}

	populate_filter_options(options) {
		this.employee_options = options.employees || [];
		const $employee = this.$body.find('select[data-filter="employee"]');
		this.employee_options.forEach((row) => {
			if (!row.employee) return;
			const label = row.employee_name ? `${row.employee_name} (${row.employee})` : row.employee;
			$employee.append(`<option value="${frappe.utils.escape_html(row.employee)}">${frappe.utils.escape_html(label)}</option>`);
		});
		const $status = this.$body.find('select[data-filter="status"]');
		// Real, present values only (see get_dashboard_data / _status_options) - not the
		// full stock Call Log status enum.
		(options.statuses || []).forEach((s) => {
			$status.append(`<option value="${frappe.utils.escape_html(s)}">${frappe.utils.escape_html(s)}</option>`);
		});
	}

	render_summary(summary) {
		this.$body.find(".cm-summary").html(
			CM_TILES.map((tile) => {
				const value = cint(summary[tile.data_key]);
				const clickable = !!tile.filter_key;
				const active = clickable && this.state[tile.filter_key] === tile.filter_value;
				const hue = clickable ? cm_color_for(tile.filter_value) : null;
				return `
					<div class="frappe-card cm-tile${active ? " cm-tile-active" : ""}" data-clickable="${clickable ? "1" : "0"}"
						${tile.filter_key ? `data-filter-key="${tile.filter_key}" data-filter-value="${frappe.utils.escape_html(tile.filter_value)}"` : ""}
						style="padding: 14px; text-align: center; ${active ? `--cm-tile-color: ${hue};` : ""}">
						<div style="font-size: 22px; font-weight: 600; font-variant-numeric: proportional-nums;">${value}</div>
						<div class="text-muted small">${frappe.utils.escape_html(tile.label)}</div>
						${tile.note ? `<div class="text-muted cm-tile-note">${frappe.utils.escape_html(tile.note)}</div>` : ""}
					</div>`;
			}).join("")
		);
		this.$body.find(".cm-tile[data-clickable=\"1\"]").on("click", (e) => {
			const $tile = $(e.currentTarget);
			this.toggle_filter($tile.data("filter-key"), $tile.data("filter-value"));
		});
	}

	render_chart(key, rows, type, filter_key) {
		rows = rows || [];
		const $card = this.$body.find(`.cm-chart-card[data-chart="${key}"]`);
		const $canvas = $card.find(".cm-chart-canvas");
		const $table = $card.find(".cm-chart-table");

		if (!rows.length) {
			$canvas.html('<div class="cm-chart-empty text-muted">No calls match the current filters.</div>');
			$table.html("");
			return;
		}

		const labels = rows.map((r) => r.label);
		const values = rows.map((r) => cint(r.value));
		const colors = labels.map((l) => cm_color_for(l));

		$canvas.empty();
		// eslint-disable-next-line no-new
		new frappe.Chart($canvas[0], {
			data: { labels, datasets: [{ values }] },
			type,
			height: 220,
			colors,
			maxSlices: 8,
			truncateLegends: 0,
			tooltipOptions: {
				formatTooltipY: (value) => `${value} call${value === 1 ? "" : "s"}`,
			},
		});

		// Chart segments are clickable (requirement 3b). frappe-charts only wires its own
		// click-to-select event for axis (bar/line) charts, not donut/pie, so the donut
		// slice paths (class "donut-path", one per label in the same order as `labels` -
		// see frappe-charts' donutSlices layer) are bound directly here.
		$canvas.find(".donut-path").each((idx, el) => {
			const label = labels[idx];
			if (label === undefined) return;
			// Dim every slice except the currently active filter value, so the chart
			// visually reflects the filter state exactly like the pills/dropdown do.
			if (this.state[filter_key]) {
				el.style.opacity = this.state[filter_key] === label ? "1" : "0.35";
			}
			el.addEventListener("click", () => this.toggle_filter(filter_key, label));
		});

		const total = values.reduce((a, b) => a + b, 0);
		$table.html(this.data_table_html(["Category", "Calls", "%"], rows.map((r) => [
			r.label, cint(r.value), total ? `${((r.value / total) * 100).toFixed(1)}%` : "0%",
		])));
	}

	render_trend(rows) {
		rows = rows || [];
		const $card = this.$body.find('.cm-chart-card[data-chart="trend"]');
		const $canvas = $card.find(".cm-chart-canvas");
		const $table = $card.find(".cm-chart-table");

		if (!rows.length) {
			$canvas.html('<div class="cm-chart-empty text-muted">No calls match the current filters.</div>');
			$table.html("");
			return;
		}

		const days = rows.map((r) => r.day);
		const labels = rows.map((r) => frappe.datetime.str_to_user(r.day, true));
		const values = rows.map((r) => cint(r.value));
		const colors = [cm_colors().blue];

		$canvas.empty();
		// eslint-disable-next-line no-new
		new frappe.Chart($canvas[0], {
			data: { labels, datasets: [{ name: "Calls", values }] },
			type: "line",
			height: 220,
			colors,
			axisOptions: { xIsSeries: 1 },
			lineOptions: { regionFill: 0, hideDots: 0 },
			tooltipOptions: {
				formatTooltipY: (value) => `${value} call${value === 1 ? "" : "s"}`,
			},
		});

		// Line/bar charts get a native "data-select" click event from frappe-charts
		// (dispatched on the chart's own container element) - use the raw ISO day rather
		// than re-parsing the formatted label back into a date.
		$canvas[0].addEventListener("data-select", (e) => {
			const day = days[e.index];
			if (day) this.toggle_date(day);
		});

		$table.html(this.data_table_html(["Date", "Calls"], rows.map((r) => [
			frappe.datetime.str_to_user(r.day, true), cint(r.value),
		])));
	}

	data_table_html(headers, body_rows) {
		return `
			<div class="table-responsive">
				<table class="table table-bordered table-sm">
					<thead><tr>${headers.map((h) => `<th>${frappe.utils.escape_html(h)}</th>`).join("")}</tr></thead>
					<tbody>
						${body_rows
							.map(
								(row) =>
									`<tr>${row.map((cell) => `<td>${frappe.utils.escape_html(String(cell))}</td>`).join("")}</tr>`
							)
							.join("")}
					</tbody>
				</table>
			</div>`;
	}

	bind_table_toggles() {
		this.$body.find(".cm-toggle-table").off("click").on("click", (e) => {
			e.preventDefault();
			const key = $(e.currentTarget).data("chart");
			const $card = this.$body.find(`.cm-chart-card[data-chart="${key}"]`);
			const $table = $card.find(".cm-chart-table");
			const showing = $table.is(":visible");
			$table.toggle(!showing);
			$(e.currentTarget).text(showing ? "View data" : "Hide data");
		});
	}

	render_table(rows) {
		const $tbody = this.$body.find("#cm-table tbody").empty();
		if (!rows.length) {
			$tbody.html('<tr><td colspan="8" class="text-muted text-center" style="padding: 24px 0;">No calls match the current filters.</td></tr>');
		} else {
			rows.forEach((row) => {
				const status_label =
					row.custom_source === "WhatsApp" && row.status === "Unknown" ? "Missed" : row.status || "-";
				const linked_party = row.customer || row.custom_lead || "-";
				const duration = row.duration ? cm_format_duration(row.duration) : "-";
				$tbody.append(`
					<tr>
						<td>${row.start_time ? frappe.datetime.str_to_user(row.start_time) : "-"}</td>
						<td>${cm_badge_html(row.type)}</td>
						<td>${cm_badge_html(row.custom_source)}</td>
						<td>${cm_badge_html(status_label)}</td>
						<td>${frappe.utils.escape_html(linked_party)}</td>
						<td>${frappe.utils.escape_html(row.call_received_by || "-")}</td>
						<td>${frappe.utils.escape_html(duration)}</td>
						<td>${frappe.utils.escape_html(row.custom_call_summary || "-")}</td>
					</tr>
				`);
			});
		}

		const from = this.last_total_rows ? this.state.start + 1 : 0;
		const to = Math.min(this.state.start + CM_PAGE_LENGTH, this.last_total_rows || 0);
		this.$body.find("#cm-page-info").text(
			this.last_total_rows ? `${from}-${to} of ${this.last_total_rows} (Company calls)` : ""
		);
		this.$body.find("#cm-prev-btn").prop("disabled", this.state.start === 0);
		this.$body.find("#cm-next-btn").prop("disabled", this.state.start + CM_PAGE_LENGTH >= (this.last_total_rows || 0));
	}
}

function cint(v) {
	return frappe.utils.cint ? frappe.utils.cint(v) : parseInt(v, 10) || 0;
}

// Small coloured dot + label, reusing the exact same `cm_color_for` as the charts and pills
// (requirement: one shared color-mapping object used consistently everywhere) - not a
// separately-chosen table colour.
function cm_badge_html(value) {
	if (!value) return "-";
	const hue = cm_color_for(value);
	return `<span class="cm-badge-dot" style="background: ${hue};"></span>${frappe.utils.escape_html(value)}`;
}

// Call Log's `duration` is seconds (Duration fieldtype) - no client-side
// formatter ships for it, so a small local one covers the drill-down table.
function cm_format_duration(seconds) {
	seconds = Math.round(Number(seconds) || 0);
	const h = Math.floor(seconds / 3600);
	const m = Math.floor((seconds % 3600) / 60);
	const s = seconds % 60;
	const pad = (n) => String(n).padStart(2, "0");
	return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
