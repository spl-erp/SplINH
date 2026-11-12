/* ===== CALL MONITORING DASHBOARD JS â V2 ===== */
(function () {
  'use strict';

  /* ---------- security: redirect guests ---------- */
  if (frappe.session && frappe.session.user === 'Guest') {
    window.location.href = '/login?redirect-to=' + encodeURIComponent(window.location.pathname);
    return;
  }

  /* ---------- load Chart.js ---------- */
  function loadScript(src, cb) {
    if (window.Chart) { cb(); return; }
    var s = document.createElement('script');
    s.src = src; s.onload = cb; document.head.appendChild(s);
  }

  /* ---------- theme ---------- */
  var isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    || window.matchMedia('(prefers-color-scheme: dark)').matches;

  /* ---------- PFP-matching chart color palette ---------- */
  var C = {
    blue:   '#3b82f6',
    green:  '#22c55e',
    orange: '#f97316',
    amber:  '#d97706',
    pink:   '#ec4899',
    violet: '#8b5cf6',
    cyan:   '#06b6d4',
    muted:  '#94a3b8'
  };

  function gridColor() { return isDark ? '#2a3040' : '#f1f5f9'; }
  function tickColor() { return isDark ? '#64748b' : '#9ca3af'; }

  /* ---------- state ---------- */
  var S = { from_date: '', to_date: '', employee: '', source: '', direction: '', status: '', category: '', review: '', start: 0 };
  var DATA  = {};
  var _accLogs = [];
  var CHARTS = {};

  /* ---------- DOM refs ---------- */
  var $from    = document.getElementById('cm-from');
  var $to      = document.getElementById('cm-to');
  var $emp     = document.getElementById('cm-emp');
  /* employee autocomplete */
  var _empNameToId = {};
  var $empDL = document.createElement('datalist');
  $empDL.id = 'cm-emp-list';
  document.body.appendChild($empDL);
  if ($emp) $emp.setAttribute('list', 'cm-emp-list');
  var $src     = document.getElementById('cm-src');
  var dir = document.getElementById('cm-dir'), dmin = document.getElementById('cm-dmin'), dmax = document.getElementById('cm-dmax'), dunit = document.getElementById('cm-dunit');
  var $apply   = document.getElementById('cm-apply');
  var $reset   = document.getElementById('cm-reset');
  var $pills   = document.getElementById('cm-pills');
  var $tiles   = document.getElementById('cm-tiles');
  var $lbGrid  = document.getElementById('cm-lb-grid');
  var $logBody = document.getElementById('cm-log-body');
  var $pgInfo  = document.getElementById('cm-page-info');
  var $prev    = document.getElementById('cm-prev');
 var $loadMore = document.getElementById('cm-load-more');
  var $next    = document.getElementById('cm-next');
  var $loading = document.getElementById('cm-loading');
  var $error   = document.getElementById('cm-error');
  var $logTitle= document.getElementById('cm-log-title');
  var $range   = document.getElementById('cm-subtitle-range');

  /* ---------- tile definitions (no color borders â dark text only) ---------- */
  var TILES = [
    ['total',   'Total Calls',   'total_calls',   false, null,        null],
    ['conn',    'Connected Calls', 'connected_calls', true, 'status', 'completed'],
    ['c30',     'Under 30s',     'conn_lt30',     true, 'conn_bucket', 'lt30'],
    ['c3060',   '30s - 1m',      'conn_30_60',    true, 'conn_bucket', '30_60'],
    ['c60',     'Over 1m',       'conn_ge60',     true, 'conn_bucket', 'ge60'],
    ['talk',    'Total Talk Time', 'total_duration', false, null, null],
    ['qual',    'Conversation Calls', 'qualified_calls', true, 'qualified', '1'],
    ['unmapped', 'Unmapped Calls', 'unmapped_calls', true, 'mapping', 'unmapped'],
    ['out',     'Outgoing',      'outgoing',      true,  'direction', 'Outgoing'],
    ['inc',     'Incoming',      'incoming',      true,  'direction', 'Incoming'],
    ['miss',    'Not Connected', 'missed',        true,  'status',    'missed'],
    ['wa',      'WhatsApp',      'whatsapp',      true,  'source',    'WhatsApp'],
    ['phone',   'Phone (SIM)',   'phone',         true,  'source',    'SIM'],
    ['newc',    'New Calls',     'new_calls',     true,  'category',  'new'],
    ['fup',     'Follow Up Calls', 'followup_calls', true, 'category',  'follow'],
    ['rev',     'Comments Updated', 'reviewed_calls', true, 'review',    'reviewed'],
    ['nrev',    'Comments Not Updated', 'not_reviewed_calls', true, 'review', 'not_reviewed'],
  ];

  /* ---------- helpers ---------- */
  function fmtDur(sec) {
    if (!sec) return '–';
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + String(s).padStart(2, '0');
  }
  function fmtTime(dt) {
    if (!dt) return '–';
    return dt.replace('T', ' ').substring(0, 16);
  }
  function badge(dotColor, text) {
    return '<span class="cm-badge"><span class="cm-dot" style="background:' + dotColor + '"></span>' + text + '</span>';
  }
  function dirBadge(d) {
    if (d === 'Outgoing') return badge(C.blue,   'Outgoing');
    if (d === 'Incoming') return badge(C.green,  'Incoming');
    return badge(C.muted, d || '–');
  }
  function statusBadge(s) {
    if (s === 'Completed') return badge(C.green,  'Completed');
    if (s === 'No Answer') return badge(C.orange, 'No Answer');
    if (s === 'Busy')      return badge(C.amber,  'Busy');
    return badge(C.muted, s || '–');
  }
  function stageBadge(s) {
    s = (s || '').trim();
    if (!s) return '–';
    var m = {'Not Connected':'#f97316','0. No 2-Way Communication':'#ef4444','1. No Need':'#a855f7','1. Not Qualified Lead':'#6366f1','2. No Trust':'#3b82f6','3. No Money':'#eab308','4. No Discount':'#14b8a6','5. Converted':'#22c55e','6. Follow-Up Other than Sales':'#ec4899'};
    return badge(m[s] || C.muted, s);
  }
  function srcBadge(s) {
    if (s === 'SIM')      return badge(C.violet, 'SIM');
    if (s === 'WhatsApp') return badge(C.cyan,   'WhatsApp');
    return badge(C.muted, s || '–');
  }
  function showLoading(b) { if ($loading) $loading.style.display = b ? 'flex' : 'none'; }
  function showError(msg) { if ($error) { $error.style.display = 'block'; $error.textContent = msg; } }

  /* ---------- render tiles ---------- */
  var TILE_INFO = {total:'All calls in the selected period',conn:'Calls with Connected (Completed) status',talk:'Total duration of all calls in the period',miss:'Missed incoming calls plus outgoing calls that were not connected (No Answer or Busy)',c30:'Connected calls shorter than 30 seconds',c3060:'Connected calls from 30 seconds up to 1 minute',c60:'Connected calls of 1 minute or more',qual:'Calls that lasted 1 minute or more',unmapped:'Calls not linked to any Lead or Customer',out:'Calls made by the salesperson',inc:'Calls received by the salesperson',miss:'Calls not answered or busy (incoming and outgoing)',wa:'Calls logged from WhatsApp',phone:'Calls logged from the phone SIM',newc:'Calls marked New Call by the salesperson',fup:'Calls marked Follow Up Call by the salesperson',rev:'Connected calls where the salesperson has updated comments in the app (calls marked Not Connected excluded)',nrev:'Connected calls still waiting for the salesperson comments, including those marked Not Connected'};
  function renderTiles() {
    var sum = DATA.summary || {};
    sum.not_reviewed_calls = (sum.connected_calls || 0) - (sum.reviewed_calls || 0);
    var total = sum.total_calls || 0;
    var MAIN = ['total', 'conn', 'qual', 'talk', 'unmapped', 'miss', 'rev'];
    var SHORT = { total: 'All calls in the period', conn: 'Calls with Connected status', qual: 'Calls of 1 minute or more', talk: 'Total time spent on calls', unmapped: 'Not linked to a Lead or Customer', miss: 'No answer or busy', rev: 'Connected calls with comments updated' };
    var GROUPS = [
      ['Call type', ['out', 'inc']],
      ['Connected', ['c30', 'c3060', 'c60']],
      ['Source', ['phone', 'wa']],
      ['Category', ['newc', 'fup']],
      ['Review', ['rev', 'nrev']]
    ];
    var CHIP_LABEL = { out: 'Outgoing', inc: 'Incoming', phone: 'SIM', wa: 'WhatsApp', newc: 'New', fup: 'Follow Up', rev: 'Comments Updated', nrev: 'Comments Not Updated', c30: 'Under 30s', c3060: '30s – 1m', c60: 'Over 1m' };
    var byId = {};
    TILES.forEach(function (t) { byId[t[0]] = t; });
    function state(t) {
      var fk = t[4], fv = t[5];
      return { active: !!(fk && S[fk] === fv), dimmed: !!(fk && S[fk] && S[fk] !== fv) };
    }
    var html = '<div class="cm-tile-grid">';
    MAIN.forEach(function (id) {
      var t = byId[id]; if (!t) return;
      var label = t[1], key = t[2], click = t[3], fk = t[4], fv = t[5];
      var val = sum[key] || 0, st = state(t);
      var cls = ['cm-tile'];
      if (click) cls.push('cm-clickable');
      if (st.active) cls.push('cm-active');
      if (st.dimmed) cls.push('cm-dimmed');
      html += '<div class="' + cls.join(' ') + '" data-fk="' + (fk || '') + '" data-fv="' + (fv || '') + '" title="' + (TILE_INFO[id] || '') + '">';
      html += '<div class="cm-tile-label">' + label + '</div>';
      if (id === 'rev') {
        html += '<div class="cm-tile-value">' + val.toLocaleString() + ' <span class="cm-of">of ' + (sum.connected_calls || 0).toLocaleString() + ' connected</span></div>';
        html += '<div class="cm-bar"><span style="width:' + ((sum.connected_calls || 0) ? Math.min(100, Math.round(val * 100 / sum.connected_calls)) : 0) + '%"></span></div>';
      } else if (id === 'talk') {
        html += '<div class="cm-tile-value">' + fmtTalk(val) + '</div>';
      } else {
        html += '<div class="cm-tile-value">' + val.toLocaleString() + '</div>';
      }
      var info = SHORT[id] || '';
      if (id === 'miss') info = 'Missed incoming ' + (sum.missed_in || 0).toLocaleString() + ' · Outgoing not connected ' + (sum.missed_out || 0).toLocaleString();
      if ((id === 'qual' || id === 'conn') && total) info += ' · ' + Math.round(val * 100 / total) + '% of total';
      html += '<div class="cm-tile-sub">' + info + '</div>';
      html += '</div>';
    });
    html += '</div><div class="cm-brk-grid">';
    var BRK = [
      ['Call Type', ['out', 'inc'], ['phone', 'wa']],
      ['Connected by Length', ['c30', 'c3060', 'c60'], null],
      ['Call Category', ['newc', 'fup'], null],
      ['Comments', ['rev', 'nrev'], null]
    ];
    var SEGCOL = { out: '#3b82f6', inc: '#22c55e', c30: '#f59e0b', c3060: '#3b82f6', c60: '#22c55e', newc: '#8b5cf6', fup: '#06b6d4', rev: '#22c55e', nrev: '#94a3b8' };
    var SEGLAB = { out: 'Outgoing', inc: 'Incoming', c30: 'Under 30s', c3060: '30s – 1m', c60: 'Over 1m', newc: 'New', fup: 'Follow Up', rev: 'Updated', nrev: 'Not Updated' };
    BRK.forEach(function (bk) {
      var vals = bk[1].map(function (cid) { var c = byId[cid]; return c ? (sum[c[2]] || 0) : 0; });
      var tot = vals.reduce(function (acc, v) { return acc + v; }, 0);
      html += '<div class="cm-brk-card"><div class="cm-brk-title">' + bk[0] + '</div><div class="cm-seg-row">';
      bk[1].forEach(function (cid, i) {
        var c = byId[cid]; if (!c) return;
        var cs = state(c), cc = ['cm-seg', 'cm-clickable'];
        if (cs.active) cc.push('cm-active');
        if (cs.dimmed) cc.push('cm-dimmed');
        var pct = tot ? Math.round(vals[i] * 100 / tot) : 0;
        html += '<div class="' + cc.join(' ') + '" data-fk="' + c[4] + '" data-fv="' + c[5] + '" title="' + (TILE_INFO[cid] || '') + ' · ' + pct + '%"><span class="cm-seg-n">' + vals[i].toLocaleString() + '</span><span class="cm-seg-l"><i style="background:' + SEGCOL[cid] + '"></i>' + SEGLAB[cid] + '</span></div>';
      });
      html += '</div><div class="cm-seg-bar">';
      bk[1].forEach(function (cid, i) { html += '<span style="width:' + (tot ? (vals[i] * 100 / tot) : 0) + '%;background:' + SEGCOL[cid] + '"></span>'; });
      html += '</div>';
      if (bk[2]) {
        html += '<div class="cm-brk-foot"><span class="cm-brk-foot-l">Source</span>';
        bk[2].forEach(function (cid) {
          var c = byId[cid]; if (!c) return;
          var cs = state(c), cc = ['cm-chip', 'cm-clickable'];
          if (cs.active) cc.push('cm-active');
          if (cs.dimmed) cc.push('cm-dimmed');
          html += '<span class="' + cc.join(' ') + '" data-fk="' + c[4] + '" data-fv="' + c[5] + '" title="' + (TILE_INFO[cid] || '') + '">' + CHIP_LABEL[cid] + ' <b>' + (sum[c[2]] || 0).toLocaleString() + '</b></span>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    html += '</div>';
    $tiles.innerHTML = html;
    $tiles.querySelectorAll('.cm-clickable').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var fk = el.dataset.fk, fv = el.dataset.fv;
        if (!fk) return;
        S[fk] = (S[fk] === fv) ? '' : fv;
        S.start = 0;
        load();
      });
    });
  }

  /* ---------- render pills ---------- */
  var PILL_LABELS = {
    from_date:'From', to_date:'To', employee:'Employee Name',
    source:'Call Source', direction:'Call Type', status:'Status', mapping: 'Mapping', min_duration: 'Min duration (s)', max_duration: 'Max duration (s)', team: 'Department', qualified: 'Duration', category: 'Call Category', review: 'Comments', drop_stage: 'Call Drop Stage', conn_bucket: 'Connected Duration'
  };
  var PILL_VAL = { category: { new: 'New Call', follow: 'Follow Up Call', none: 'Not set' }, review: { reviewed: 'Updated', not_reviewed: 'Not Updated' }, conn_bucket: { lt30: 'Under 30s', '30_60': '30s – 1m', ge60: 'Over 1m' }, status: { completed: 'Connected', missed: 'Not Connected' } };
  function renderPills() {
    var html = '';
    ['from_date', 'to_date', 'employee', 'source', 'direction', 'status', 'mapping', 'min_duration', 'max_duration', 'team', 'qualified', 'category', 'review', 'drop_stage', 'conn_bucket'].forEach(function (k) {
      if (!S[k]) return;
      html += '<span class="cm-pill" data-key="' + k + '">';
      html += (k === 'qualified') ? 'Duration: 1 min or more' : PILL_LABELS[k] + ': ' + ((PILL_VAL[k] && PILL_VAL[k][S[k]]) || S[k]);
      html += '<span class="cm-pill-x" title="Remove">&times;</span></span>';
    });
    $pills.innerHTML = html;
    $pills.querySelectorAll('.cm-pill-x').forEach(function (x) {
      x.addEventListener('click', function () {
        var k = x.closest('.cm-pill').dataset.key;
        S[k] = ''; S.start = 0;
        if (k === 'from_date') $from.value = '';
        if (k === 'to_date')   $to.value   = '';
        if (k === 'employee')  $emp.value  = '';
        if (k === 'source')    $src.value  = '';
      if (k === 'min_duration') dmin.value = '';
      if (k === 'max_duration') dmax.value = '';
        load();
      });
    });
  }

  /* ---------- render trend line chart ---------- */
  function renderTrend(tr) {
    var labels = tr.map(function (d) { return d.date; });
    var values = tr.map(function (d) { return d.value; });
    if (CHARTS['cm-chart-trend']) { CHARTS['cm-chart-trend'].destroy(); }
    var ctx = document.getElementById('cm-chart-trend');
    if (!ctx) return;
    CHARTS['cm-chart-trend'] = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Calls',
          data: values,
          fill: true,
          borderColor: C.blue,
          backgroundColor: 'rgba(59,130,246,0.10)',
          pointRadius: labels.length > 30 ? 0 : 3,
          pointHoverRadius: 5,
          borderWidth: 2,
          tension: 0.35
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { mode: 'index', intersect: false }
        },
        scales: {
          x: {
            ticks: { maxTicksLimit: 10, color: tickColor(), font: { size: 10 } },
            grid:  { color: gridColor() }
          },
          y: {
            beginAtZero: true,
            ticks: { color: tickColor(), font: { size: 10 } },
            grid:  { color: gridColor() }
          }
        }
      }
    });
  }

  /* ---------- render outcomes horizontal bar chart ---------- */
  var CMLABELS = { id: 'cmLabels', afterDatasetsDraw: function (chart) { var ds = chart.data.datasets[0], meta = chart.getDatasetMeta(0), ctx = chart.ctx; var total = ds.data.reduce(function (a, b) { return a + b; }, 0); ctx.save(); ctx.font = '600 11px sans-serif'; ctx.fillStyle = (getComputedStyle(document.documentElement).getPropertyValue('--fg-2') || '').trim() || tickColor(); ctx.textBaseline = 'middle'; ctx.textAlign = 'left'; meta.data.forEach(function (bar, i) { var v = ds.data[i]; var pct = total ? Math.round(v * 100 / total) : 0; ctx.fillText(v.toLocaleString() + ' (' + pct + '%)', bar.x + 6, bar.y); }); ctx.restore(); } };
  function renderStatusBar(st) {
    /* map status labels → friendly names & colors */
    var COLOR_MAP = {
      'Completed': C.green,
      'No Answer': C.orange,
      'Busy':      C.amber,
      'Unknown':   C.muted
    };
    var filtered = st.filter(function (d) { return d.value > 0; });
    var labels = filtered.map(function (d) { return d.label; });
    var values = filtered.map(function (d) { return d.value; });
    var STAGE_COL = {'Not Connected':'#f97316','0. No 2-Way Communication':'#ef4444','1. No Need':'#a855f7','1. Not Qualified Lead':'#6366f1','2. No Trust':'#3b82f6','3. No Money':'#eab308','4. No Discount':'#14b8a6','5. Converted':'#22c55e','6. Follow-Up Other than Sales':'#ec4899','Not Updated':'#9ca3af'};
    var colors = filtered.map(function (d) { return STAGE_COL[d.label] || C.muted; });

    if (CHARTS['cm-chart-status']) { CHARTS['cm-chart-status'].destroy(); }
    var ctx = document.getElementById('cm-chart-status');
    if (!ctx) return;
    CHARTS['cm-chart-status'] = new Chart(ctx, {
      plugins: [CMLABELS], type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: colors,
          borderRadius: 4,
          borderSkipped: false
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                var pct = total ? Math.round(ctx.raw * 100 / total) : 0;
                return ' ' + ctx.raw.toLocaleString() + ' (' + pct + '%)';
              }
            }
          }
        },
        scales: {
          x: {
            beginAtZero: true, grace: '42%',
            ticks: { color: tickColor(), font: { size: 10 } },
            grid:  { color: gridColor() }
          },
          y: {
            ticks: { color: tickColor(), font: { size: 11 } },
            grid:  { display: false }
          }
        },
        onClick: function (e, els) {
          if (!els.length) return;
          var label = CHARTS['cm-chart-status'].data.labels[els[0].index];
          var fv = label;
          S.drop_stage = (S.drop_stage === fv) ? '' : fv;
          S.start = 0;
          load();
        }
      }
    });
  }

  /* ---------- render leaderboard ---------- */
  function renderLeaderboard() {
    var emp = DATA.employee_data || [];
    if (!emp.length) {
      $lbGrid.innerHTML = '<div class="cm-lb-empty">No employee data available for this period.</div>';
      return;
    }
    var max = emp[0].total || 1;
    var html = '';
    emp.slice(0, 12).forEach(function (e, i) {
      var rankClass = i === 0 ? 'r1' : i === 1 ? 'r2' : i === 2 ? 'r3' : 'rn';
      var pct = Math.round((e.total / max) * 100);
      html += '<div class="cm-lb-row">';
      html += '<div class="cm-lb-rank ' + rankClass + '">' + (i + 1) + '</div>';
      html += '<div class="cm-lb-name" title="' + (e.employee_name || e.employee || '') + '">' + (e.employee_name || e.employee || 'Unknown') + '</div>';
      html += '<div class="cm-lb-bar-wrap"><div class="cm-lb-bar" style="width:' + pct + '%"></div></div>';
      html += '<div class="cm-lb-count">' + e.total.toLocaleString() + '</div>';
      html += '</div>';
    });
    $lbGrid.innerHTML = html;
  }

  /* ---------- view-data toggle ---------- */
  document.querySelectorAll('.cm-view-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var chart = btn.dataset.chart;
      var tbl   = document.getElementById('cm-tbl-' + chart);
      if (!tbl) return;
      var open = tbl.style.display !== 'none';
      tbl.style.display = open ? 'none' : 'block';
      btn.innerHTML = open ? 'View data &#9660;' : 'Hide data &#9650;';
    });
  });

  /* ---------- render charts ---------- */
  function renderCharts() {
    var st = DATA.status_data    || [];
    var tr = DATA.trend_data     || [];
    renderTrend(tr);
    renderStatusBar(st);

    /* data table for trend */
    var trendRows = tr.map(function (d) {
      return '<tr><td>' + d.date + '</td>'
        + '<td style="text-align:right;font-variant-numeric:tabular-nums">' + d.value + '</td></tr>';
    }).join('');
    var tblTrend = document.getElementById('cm-tbl-trend');
    if (tblTrend) tblTrend.innerHTML =
      '<table><thead><tr><th>' + (DATA.trend_mode === 'hourly' ? 'Hour' : 'Date') + '</th><th style="text-align:right">Calls</th></tr></thead>'
      + '<tbody>' + trendRows + '</tbody></table>';

    /* data table for status */
    var totalSt = st.reduce(function (a, d) { return a + d.value; }, 0);
    var stRows = st.map(function (d) {
      var pct = totalSt ? Math.round(d.value * 100 / totalSt) : 0;
      return '<tr><td>' + (d.label||'–') + '</td>'
        + '<td style="text-align:right;font-variant-numeric:tabular-nums">' + d.value.toLocaleString() + '</td>'
        + '<td style="text-align:right;opacity:.6">' + pct + '%</td></tr>';
    }).join('');
    var tblStatus = document.getElementById('cm-tbl-status');
    if (tblStatus) tblStatus.innerHTML =
      '<table><thead><tr><th>Status</th><th style="text-align:right">Count</th><th style="text-align:right">%</th></tr></thead>'
      + '<tbody>' + stRows + '</tbody></table>';
  }

  /* ---------- render log table ---------- */
  function renderLog() {
    var logs  = DATA.logs        || [];
    var total = DATA.total_count || 0;
    var start = DATA.start       || 0;
    var ps    = DATA.page_size   || 50;

    $logTitle.textContent = 'Call Log (' + total.toLocaleString() + ')';

    var html = logs.map(function (log) {
      var summary = log.custom_ai_summary || '';
      var summaryShort = summary.length > 110 ? summary.substring(0, 110) + 'â¦' : summary;
      return '<tr>'
        + '<td>' + fmtTime(log.start_time) + '</td>'
        + '<td>' + (log.employee_name || log.call_received_by || '–') + '</td>'
        + '<td>' + dirBadge(log.type) + '</td>'
        + '<td>' + statusBadge(log.status) + '</td>'
        + '<td style="font-variant-numeric:tabular-nums">' + (log.to || '–') + '</td>'
        + '<td>' + escH(log.custom_linked_party || '–') + '</td>'
        + '<td>' + stageBadge(log.drop_stage) + '</td>'
        + '<td>' + srcBadge(log.custom_source) + '</td>'
        + '<td style="font-variant-numeric:tabular-nums">' + fmtDur(log.duration) + '</td>'
        + '<td title="' + summary.replace(/"/g, '&quot;') + '">' + (summaryShort || '–') + '</td>'
        + '</tr>';
    }).join('');

    $logBody.innerHTML = html
      || '<tr><td colspan="10" style="text-align:center;padding:28px;color:#9ca3af">No records found</td></tr>';

        var _shown = _accLogs.length, _total = DATA.total_count || 0;
    var _lm = document.getElementById('cm-load-more');
    var _pi = document.getElementById('cm-page-info');
    if (_lm) {
      if (_shown > 0 && _shown < _total) {
        _lm.style.display = '';
        _lm.textContent = 'Load More (' + (_total - _shown).toLocaleString() + ' more)';
        _lm.disabled = false;
      } else {
        _lm.style.display = 'none';
      }
    }
    if (_pi) {
      _pi.textContent = _total === 0 ? 'No records'
        : _shown >= _total ? 'All ' + _total.toLocaleString() + ' records shown'
        : 'Showing ' + _shown.toLocaleString() + ' of ' + _total.toLocaleString();
    }

    /* header range label */
    if ($range && S.from_date && S.to_date) {
      $range.textContent = S.from_date + '  →  ' + S.to_date;
    }
  }

  /* ---------- main load ---------- */
  /* ---------- team filter + roll-up ---------- */
  var teamSel = document.getElementById('cm-team');
  function escH(t) { return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function teamLabel(id) {
    if (id === '__outside__') return 'Not in sales tree';
    var ts = (DATA && DATA.teams) || [];
    for (var i = 0; i < ts.length; i++) { if (ts[i].name === id) return ts[i].label; }
    return id;
  }
  function fmtTalk(s) { s = Math.round(s || 0); if (!s) return '\u2013'; var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); if (h) return h + 'h ' + ('0' + m).slice(-2) + 'm'; if (m) return m + 'm ' + ('0' + (s % 60)).slice(-2) + 's'; return s + 's'; }
  /* ---------- sticky team tabs ---------- */
  function renderTabs() {
    var box = document.getElementById('cm-tabs');
    if (!box || !DATA.teams) return;
    var ts = DATA.teams, by = {}, kids = {};
    ts.forEach(function (t) { by[t.name] = t; (kids[t.parent] = kids[t.parent] || []).push(t); });
    var R = DATA.rollup || {}, root = R.tabs_root || '';
    var admin = !!(DATA.scope && DATA.scope.is_admin), outside = R.outside_calls || 0;
    var chain = [];
    if (S.team && S.team !== '__outside__' && by[S.team]) {
      var c = by[S.team], g = 0;
      while (c && c.name !== root && g++ < 20) { chain.unshift(c); c = c.parent ? by[c.parent] : null; }
      if (root !== '' && (!c || c.name !== root)) chain = [];
    }
    function chip(label, node, n, cls) {
      return '<button type="button" class="cm-tb-chip ' + (cls || '') + '" data-node="' + escH(node) + '">' + escH(label) + (n === null ? '' : '<span class="cm-tb-n">' + Number(n || 0).toLocaleString() + '</span>') + '</button>';
    }
    var html = '';
    for (var lvl = 0; lvl <= chain.length; lvl++) {
      var par = lvl === 0 ? root : chain[lvl - 1].name;
      var list = (kids[par] || []).filter(function (t) { return t.is_group; });
      if (lvl > 0 && !list.length) break;
      if (lvl === 0 && !list.length && !admin) { box.innerHTML = ''; return; }
      var act = list.filter(function (t) { return t.calls > 0; });
      var idle = list.filter(function (t) { return !(t.calls > 0); });
      var sel = chain[lvl] ? chain[lvl].name : '';
      var row = '';
      if (lvl === 0) {
        var tot = (root && by[root] ? by[root].calls : list.reduce(function (a, t) { return a + (t.calls || 0); }, 0)) + (admin ? outside : 0);
        row += chip('All', '', tot, !S.team ? 'cm-tb-on' : '');
      } else {
        row += chip('All ' + chain[lvl - 1].label, chain[lvl - 1].name, chain[lvl - 1].calls, !sel ? 'cm-tb-on' : '');
      }
      act.concat(idle).forEach(function (t) {
        row += chip(t.label, t.name, t.calls, (t.name === sel ? 'cm-tb-on ' : '') + (t.calls > 0 ? '' : 'cm-tb-zero'));
      });
      if (lvl === 0 && admin) row += chip('Not in sales tree', '__outside__', outside, 'cm-tb-warn' + (S.team === '__outside__' ? ' cm-tb-on' : ''));
      html += '<div class="cm-tb-row"><span class="cm-tb-lbl">' + (lvl === 0 ? 'Department' : escH(chain[lvl - 1].label)) + '</span>' + row + '</div>';
    }
    box.innerHTML = html;
  }
  var tabsBox = document.getElementById('cm-tabs');
  if (tabsBox) tabsBox.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.cm-tb-chip') : null;
    if (b) setTeam(b.getAttribute('data-node'));
  });
  function setTrendTitles() {
    var card = document.getElementById('cc-trend');
    if (!card) return;
    var t = card.querySelector('.cm-chart-title'), s = card.querySelector('.cm-chart-sub');
    var hourly = DATA.trend_mode === 'hourly';
    if (t) t.textContent = hourly ? 'Hourly Call Volume' : 'Daily Call Volume';
    if (s) s.textContent = hourly ? 'Calls per hour on the selected day' : 'Calls per day over selected period';
  }
  function setTeam(id) { S.team = id || ''; S.start = 0; load(); }
  function renderTeamSelect() {
    if (!teamSel || !DATA.teams) return;
    var cur = S.team || '';
    var h = '<option value="">' + (DATA.scope && DATA.scope.is_admin ? 'All departments' : 'My departments') + '</option>';
    DATA.teams.forEach(function (t) {
      var pad = ''; for (var i = 0; i < t.depth; i++) pad += '\u00a0\u00a0';
      h += '<option value="' + escH(t.name) + '">' + pad + (t.depth ? '\u2514 ' : '') + escH(t.label) + '</option>';
    });
    if (DATA.scope && DATA.scope.is_admin) h += '<option value="__outside__">Not in sales tree</option>';
    teamSel.innerHTML = h;
    teamSel.value = cur;
  }
  function fixTeamPill() {
    var tp = document.querySelector('.cm-pill[data-key="team"]');
    if (tp && tp.firstChild && S.team) tp.firstChild.nodeValue = 'Department: ' + teamLabel(S.team) + ' ';
  }
  function renderTeamPanel() {
    var R = DATA.rollup, body = document.getElementById('cm-team-body'), cr = document.getElementById('cm-crumbs');
    if (!R || !body || !cr) return;
    var ch = '';
    (R.breadcrumb || []).forEach(function (c, i, a) {
      var last = (i === a.length - 1);
      if (i) ch += '<span class="cm-crumb-sep">\u203a</span>';
      ch += '<button type="button" class="cm-crumb' + (last ? ' cur' : '') + '" data-node="' + escH(c.name) + '"' + (last ? ' disabled' : '') + '>' + escH(c.label) + '</button>';
    });
    cr.innerHTML = ch;
    cr.querySelectorAll('.cm-crumb:not(.cur)').forEach(function (b) {
      b.addEventListener('click', function () { setTeam(b.getAttribute('data-node')); });
    });
    var rows = R.rows || [];
    if (DATA.scope && DATA.scope.no_access) {
      body.innerHTML = '<tr><td colspan="9" class="cm-lb-empty">You are not linked to a sales team yet. Please ask an administrator to link your user to a Sales Person.</td></tr>';
      return;
    }
    if (!rows.length) { body.innerHTML = '<tr><td colspan="9" class="cm-lb-empty">No calls for this selection.</td></tr>'; return; }
    var max = 0; rows.forEach(function (r) { if (r.calls > max) max = r.calls; });
    function cell(v) { return '<td' + (v ? '' : ' class="cm-team-zero"') + '>' + Number(v || 0).toLocaleString() + '</td>'; }
    var h = '';
    rows.forEach(function (r) {
      var click = (r.kind === 'child' || r.kind === 'outside');
      var pct = max ? Math.round(r.calls * 100 / max) : 0;
      var mp = r.calls ? Math.round(r.missed * 100 / r.calls) : 0;
      h += '<tr class="' + (click ? 'cm-team-click' : '') + (r.kind === 'outside' ? ' cm-team-warn' : '') + '" data-node="' + escH(r.node) + '">'
        + '<td><span class="cm-team-name">' + escH(r.label) + '</span>' + (r.kind === 'child' && r.is_group ? '<span class="cm-team-kind">team</span>' : '') + '<div class="cm-team-bar" style="width:' + pct + '%"></div></td>'
        + cell(r.calls) + cell(r.incoming) + cell(r.outgoing)
        + '<td' + (r.missed ? '' : ' class="cm-team-zero"') + '>' + Number(r.missed || 0).toLocaleString() + (r.missed ? '<span class="cm-team-pct">' + mp + '%</span>' : '') + '</td>'
        + cell(r.unmapped)
        + '<td>' + fmtTalk(r.duration_in) + '</td><td>' + fmtTalk(r.duration_out) + '</td><td>' + fmtTalk(r.duration) + '</td></tr>';
    });
    body.innerHTML = h;
    body.querySelectorAll('tr.cm-team-click').forEach(function (tr) {
      tr.addEventListener('click', function () { setTeam(tr.getAttribute('data-node')); });
    });
  }
  if (teamSel) teamSel.addEventListener('change', function () { setTeam(teamSel.value); });

  function nrm(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }
  function cmSortInd() { var k = S.sort_by || 'time', d = S.sort_dir || 'desc'; document.querySelectorAll('#cm-log-table th[data-sort]').forEach(function (el) { var on = el.dataset.sort === k; el.classList.toggle('cm-sorted', on); var ind = el.querySelector('.cm-sort-ind'); if (ind) ind.textContent = on ? (d === 'asc' ? '▲' : '▼') : '↕'; }); }
  function cmGate() { var r = document.getElementById("cm-root"); if (r) r.classList.toggle("cm-gate", !S.employee); cmSortInd(); }
  function load(_append) {
    if (!_append) { _accLogs = []; }
    showLoading(true);
    if ($error) $error.style.display = 'none';

    var args = {};
  if (dir) dir.value = S.direction || '';
  var _cc = document.getElementById('cm-cat'), _rv = document.getElementById('cm-rev');
  if (_cc) _cc.value = S.category || '';
  if (_rv) _rv.value = S.review || '';
  if (teamSel) teamSel.value = S.team || '';
    if (S.from_date) args.from_date = S.from_date;
    if (S.to_date)   args.to_date   = S.to_date;
    if (S.employee)  args.employee  = S.employee;
    if (S.source)    args.source    = S.source;
    if (S.direction) args.direction = S.direction;
    if (S.status)    args.status    = S.status;
  if (S.mapping) args.mapping = S.mapping;
  if (S.category) args.category = S.category;
  if (S.review) args.review = S.review;
  if (S.drop_stage) args.drop_stage = S.drop_stage;
  if (S.conn_bucket) args.conn_bucket = S.conn_bucket;
  if (S.sort_by) args.sort_by = S.sort_by;
  if (S.sort_dir) args.sort_dir = S.sort_dir;
    if (S.qualified) args.qualified = S.qualified;
  if (S.team) args.team = S.team;
  if (S.min_duration !== undefined && S.min_duration !== '') args.min_duration = S.min_duration;
  if (S.max_duration !== undefined && S.max_duration !== '') args.max_duration = S.max_duration;
    args.start = S.start;

    if (_append) { args.start = _accLogs.length; args.page_size = (DATA && DATA.total_count || 0) - _accLogs.length; }
    frappe.call({
      method: 'call_monitoring_data',
      type: 'GET',
      args: args,
      callback: function (r) {
        showLoading(false);
        if (r.exc || !r.message) {
          showError('Error loading data. You may not have permission to view Call Log.');
          return;
        }
        DATA = r.message;
        if (_append) { _accLogs = _accLogs.concat(DATA.logs || []); } else { _accLogs = (DATA.logs || []).slice(); }
        DATA.logs = _accLogs;
        /* populate employee autocomplete */
        if (DATA.employees && $empDL) {
          _empNameToId = {};
          $empDL.innerHTML = '';
          DATA.employees.forEach(function(e) {
            _empNameToId[nrm(e.label)] = e.id;
            var opt = document.createElement('option');
            opt.value = nrm(e.label);
            $empDL.appendChild(opt);
          });
        }
        renderTiles();
        renderPills();
      renderTeamSelect(); renderTeamPanel(); fixTeamPill(); renderTabs(); setTrendTitles();
        renderCharts();
        renderLeaderboard();
        renderLog();
        cmGate();
      },
      error: function (r) {
        showLoading(false);
        if (r && r.status === 403) {
          showError('You do not have permission to view Call Log data.');
        } else {
          showError('Failed to load data. Please try again.');
        }
      }
    });
  }

  /* ---------- wire controls ---------- */
  $apply.addEventListener('click', function () {
    S.from_date = $from.value;
    S.to_date   = $to.value;
    var _ev = nrm($emp.value);
    S.employee  = _empNameToId[_ev] || _ev;
    S.source    = $src.value;
    if (teamSel) S.team = teamSel.value;
    S.direction = dir ? dir.value : S.direction;
    var _c2 = document.getElementById('cm-cat'), _r2 = document.getElementById('cm-rev');
    S.category = _c2 ? _c2.value : S.category;
    S.review = _r2 ? _r2.value : S.review;
    var _u = parseFloat(dunit.value) || 1, _mn = parseFloat(dmin.value), _mx = parseFloat(dmax.value);
    if (!isNaN(_mn) && !isNaN(_mx) && _mn > _mx) { var _t = _mn; _mn = _mx; _mx = _t; dmin.value = _mn; dmax.value = _mx; }
    S.min_duration = isNaN(_mn) ? '' : Math.max(0, _mn * _u);
    S.max_duration = isNaN(_mx) ? '' : Math.max(0, _mx * _u);
    S.start     = 0;
    load();
  });
  $reset.addEventListener('click', function () {
    var _c3 = document.getElementById('cm-cat'), _r3 = document.getElementById('cm-rev'); if (_c3) _c3.value = ''; if (_r3) _r3.value = '';
    if (dir) dir.value = ''; if (teamSel) teamSel.value = ''; if (dmin) dmin.value = ''; if (dmax) dmax.value = ''; if (dunit) dunit.value = '1';
    S = { from_date:'', to_date:'', employee:'', source:'', direction:'', status:'', category:'', review:'', start:0 };
    $from.value = ''; $to.value = ''; $emp.value = ''; $src.value = '';
    S.from_date = todayLocal(); S.to_date = todayLocal(); $from.value = S.from_date; $to.value = S.to_date;
    load();
  });
  /* ---------- instant filters (no Apply button) ---------- */
  (function () {
    var timer = null;
    function fire(delay) { clearTimeout(timer); timer = setTimeout(function () { $apply.click(); }, delay || 0); }
    [$from, $to].forEach(function (el) { if (el) el.addEventListener('change', function () { fire(150); }); });
    [dir, $src, document.getElementById('cm-cat'), document.getElementById('cm-rev')].forEach(function (el) { if (el) el.addEventListener('change', function () { fire(0); }); });
    if (dunit) dunit.addEventListener('change', function () { if (dmin.value !== '' || dmax.value !== '') fire(0); });
    [dmin, dmax].forEach(function (el) {
      if (!el) return;
      el.addEventListener('input', function () { fire(700); });
      el.addEventListener('change', function () { fire(100); });
    });
    function empFire() {
      var v = nrm($emp.value);
      var known = (typeof _empNameToId !== 'undefined') && _empNameToId[v];
      if (v === '' || known || /^[A-Za-z]{1,4}\d{2,}$/.test(v)) fire(350);
    }
    if ($emp) { $emp.addEventListener('input', empFire); $emp.addEventListener('change', empFire); $emp.addEventListener('blur', empFire); }
  })();

    if ($loadMore) $loadMore.addEventListener('click', function() {
    if (!DATA || !DATA.total_count) return;
    var remaining = (DATA.total_count || 0) - _accLogs.length;
    if (remaining <= 0) { $loadMore.style.display = 'none'; return; }
    $loadMore.textContent = 'Loading…';
    $loadMore.disabled = true;
    load(true);
  });

  /* ---------- default date range: today (user can change) ---------- */
  function todayLocal() {
    var d = new Date();
    return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
  }
  $from.value = todayLocal();
  $to.value = todayLocal();
  S.from_date = $from.value;
  S.to_date = $to.value;

  /* ---------- sortable call log ---------- */
  document.querySelectorAll('#cm-log-table th[data-sort]').forEach(function (el) {
    el.addEventListener('click', function () {
      var k = el.dataset.sort;
      if ((S.sort_by || 'time') === k) { S.sort_dir = ((S.sort_dir || 'desc') === 'asc') ? 'desc' : 'asc'; }
      else { S.sort_by = k; S.sort_dir = (k === 'time' || k === 'duration') ? 'desc' : 'asc'; }
      S.start = 0;
      load();
    });
  });

  /* ---------- boot ---------- */
  loadScript('https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js', function () {
    load();
  });

})();



// ============================================================
// TEAM PERFORMANCE EXPAND MODAL (Table + Chart)
// ============================================================
frappe.ready(function() {

  // --- Styles ---
  var style = document.createElement('style');
  style.id = 'cm-expand-style';
  style.textContent = [
    '#cm-expand-btn{margin-left:auto;flex-shrink:0;background:#2563eb;color:#fff;border:none;padding:5px 12px;border-radius:6px;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:5px;}',
    '#cm-expand-btn:hover{background:#1d4ed8;}',
    '#cm-expand-modal{display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);align-items:center;justify-content:center;}',
    '#cm-expand-modal.cm-open{display:flex;}',
    '#cm-expand-box{background:#fff;border-radius:12px;width:90%;max-width:1100px;max-height:88vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.3);overflow:hidden;}',
    '.cm-exp-header{display:flex;align-items:center;gap:10px;padding:14px 20px;border-bottom:1px solid #e5e7eb;flex-wrap:wrap;}',
    '.cm-exp-title{font-size:16px;font-weight:600;color:#111;margin-right:auto;}',
    '.cm-view-toggle{display:flex;border:1px solid #d1d5db;border-radius:7px;overflow:hidden;}',
    '.cm-view-btn{background:#fff;border:none;padding:5px 14px;font-size:12px;cursor:pointer;color:#374151;display:flex;align-items:center;gap:4px;}',
    '.cm-view-btn.active{background:#2563eb;color:#fff;}',
    '.cm-metric-tabs{display:flex;gap:5px;}',
    '.cm-metric-tab{background:#f3f4f6;border:none;padding:4px 10px;border-radius:5px;font-size:11px;cursor:pointer;color:#374151;}',
    '.cm-metric-tab.active{background:#2563eb;color:#fff;}',
    '#cm-exp-search{border:1px solid #d1d5db;border-radius:6px;padding:4px 10px;font-size:12px;width:160px;outline:none;}',
    '.cm-exp-close{background:none;border:none;font-size:20px;cursor:pointer;color:#6b7280;line-height:1;padding:2px 6px;}',
    '.cm-exp-close:hover{color:#111;}',
    '.cm-exp-body{flex:1;overflow:hidden;padding:0;position:relative;}',
    '#cm-exp-table-wrap{height:100%;overflow-y:auto;padding:0 20px;}',
    '#cm-exp-chart-wrap{height:100%;padding:16px 20px;box-sizing:border-box;display:none;}',
    '#cm-exp-chart-canvas{max-height:100%;}',
    '.cm-exp-footer{padding:8px 20px;border-top:1px solid #e5e7eb;font-size:11px;color:#6b7280;display:flex;justify-content:space-between;}',
    '#cm-exp-table{width:100%;border-collapse:collapse;font-size:13px;}',
    '#cm-exp-table thead{position:sticky;top:0;background:#fff;z-index:1;}',
    '#cm-exp-table thead th{padding:10px 8px;text-align:left;font-size:11px;font-weight:600;color:#6b7280;border-bottom:2px solid #e5e7eb;white-space:nowrap;cursor:pointer;user-select:none;}',
    '#cm-exp-table thead th:hover{color:#111;}',
    '#cm-exp-table thead th.sort-asc::after{content:" ↑";color:#2563eb;}',
    '#cm-exp-table thead th.sort-desc::after{content:" ↓";color:#2563eb;}',
    '#cm-exp-table tbody tr:hover{background:#f9fafb;}',
    '#cm-exp-table tbody td{padding:9px 8px;border-bottom:1px solid #f3f4f6;color:#111;}',
    '.cm-exp-rank{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;font-size:11px;font-weight:700;}',
    '.cm-exp-rank.r1{background:#fef3c7;color:#92400e;}',
    '.cm-exp-rank.r2{background:#f1f5f9;color:#334155;}',
    '.cm-exp-rank.r3{background:#fef2f2;color:#7c2d12;}',
    '.cm-exp-rank.rn{background:#f9fafb;color:#6b7280;}',
    '.cm-mini-bar{height:5px;border-radius:3px;background:#3b82f6;margin-top:3px;}',
    '.cm-missed-red{color:#ef4444!important;font-weight:600;}',
    '.cm-loading-msg{display:flex;align-items:center;justify-content:center;height:200px;color:#6b7280;font-size:14px;}'
  ].join('');
  document.head.appendChild(style);

  // --- Modal HTML ---
  var modal = document.createElement('div');
  modal.id = 'cm-expand-modal';
  modal.innerHTML = '<div id="cm-expand-box">' +
    '<div class="cm-exp-header">' +
      '<span class="cm-exp-title">&#x1F465; Salesperson-wise Performance</span>' +
      '<div class="cm-view-toggle">' +
        '<button class="cm-view-btn active" data-view="table">&#x1F4CB; Table</button>' +
        '<button class="cm-view-btn" data-view="chart">&#x1F4CA; Chart</button>' +
      '</div>' +
      '<div class="cm-metric-tabs" style="display:none">' +
        '<button class="cm-metric-tab active" data-metric="total">Total</button>' +
        '<button class="cm-metric-tab" data-metric="outgoing">Outgoing</button>' +
        '<button class="cm-metric-tab" data-metric="incoming">Incoming</button>' +
        '<button class="cm-metric-tab" data-metric="missed">Missed</button>' +
      '</div>' +
      '<input id="cm-exp-search" type="text" placeholder="Search..." />' +
      '<button class="cm-exp-close" id="cm-exp-close-btn">&times;</button>' +
    '</div>' +
    '<div class="cm-exp-body">' +
      '<div id="cm-exp-table-wrap"><div class="cm-loading-msg">Loading data...</div></div>' +
      '<div id="cm-exp-chart-wrap"><canvas id="cm-exp-chart-canvas"></canvas></div>' +
    '</div>' +
    '<div class="cm-exp-footer">' +
      '<span id="cm-exp-count">-</span>' +
      '<span id="cm-exp-sort-info">Sorted by Total Calls</span>' +
    '</div>' +
  '</div>';
  document.body.appendChild(modal);

  // --- Expand button ---
  var teamHead = document.getElementById('cm-team-head');
  if (teamHead) {
    teamHead.style.display = 'flex';
    teamHead.style.alignItems = 'center';
    var btn = document.createElement('button');
    btn.id = 'cm-expand-btn';
    btn.innerHTML = '&#x26F6; Expand';
    teamHead.appendChild(btn);
  }

  // --- State ---
  var allData = [];
  var sortCol = 'total';
  var sortDir = 'desc';
  var searchQ = '';
  var activeView = 'table';
  var activeMetric = 'total';
  var chartInst = null;
  var dataLoaded = false;

  var METRIC_COLORS = { total:'#3b82f6', outgoing:'#10b981', incoming:'#8b5cf6', missed:'#ef4444' };
  var METRIC_LABELS = { total:'Total Calls', outgoing:'Outgoing', incoming:'Incoming', missed:'Missed' };

  function fmtDur(secs) {
    secs = parseInt(secs) || 0;
    if (secs < 60) return secs + 's';
    var h = Math.floor(secs / 3600);
    var m = Math.floor((secs % 3600) / 60);
    var s = secs % 60;
    if (h > 0) return h + 'h ' + String(m).padStart(2,'0') + 'm';
    return m + 'm ' + String(s).padStart(2,'0') + 's';
  }

  function getFilteredSorted() {
    var data = allData.filter(function(d) {
      if (!searchQ) return true;
      return (d.employee_name || '').toLowerCase().indexOf(searchQ.toLowerCase()) >= 0;
    });
    data = data.slice().sort(function(a, b) {
      var av = sortCol === 'employee_name' ? (a.employee_name || '') : (parseInt(a[sortCol]) || 0);
      var bv = sortCol === 'employee_name' ? (b.employee_name || '') : (parseInt(b[sortCol]) || 0);
      if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    return data;
  }

  function renderTable() {
    var wrap = document.getElementById('cm-exp-table-wrap');
    if (!wrap) return;
    var data = getFilteredSorted();
    var maxTotal = Math.max.apply(null, allData.map(function(d){ return parseInt(d.total)||0; }).concat([1]));
    var html = '<table id="cm-exp-table"><thead><tr>' +
      '<th style="width:36px">#</th>' +
      '<th class="sort-' + (sortCol==='employee_name'?sortDir:'') + '" data-col="employee_name">SALESPERSON</th>' +
      '<th class="sort-' + (sortCol==='total'?sortDir:'') + '" data-col="total" style="text-align:center">TOTAL CALLS</th>' +
      '<th class="sort-' + (sortCol==='outgoing'?sortDir:'') + '" data-col="outgoing" style="text-align:center">OUTGOING</th>' +
      '<th class="sort-' + (sortCol==='incoming'?sortDir:'') + '" data-col="incoming" style="text-align:center">INCOMING</th>' +
      '<th class="sort-' + (sortCol==='missed'?sortDir:'') + '" data-col="missed" style="text-align:center">MISSED</th>' +
      '<th class="sort-' + (sortCol==='total_dur'?sortDir:'') + '" data-col="total_dur" style="text-align:center">TALK TIME</th>' +
      '</tr></thead><tbody>';
    data.forEach(function(d, i) {
      var rank = i + 1;
      var rankCls = rank===1?'r1':rank===2?'r2':rank===3?'r3':'rn';
      var barW = Math.round((parseInt(d.total)||0) / maxTotal * 100);
      var missed = parseInt(d.missed) || 0;
      html += '<tr>' +
        '<td><span class="cm-exp-rank ' + rankCls + '">' + rank + '</span></td>' +
        '<td><div style="font-weight:500">' + (d.employee_name||d.employee||'-') + '</div>' +
        '<div class="cm-mini-bar" style="width:' + barW + '%"></div></td>' +
        '<td style="text-align:center;font-weight:700">' + (d.total||0) + '</td>' +
        '<td style="text-align:center">' + (d.outgoing||0) + '</td>' +
        '<td style="text-align:center">' + (d.incoming||0) + '</td>' +
        '<td style="text-align:center" class="' + (missed>0?'cm-missed-red':'') + '">' + missed + '</td>' +
        '<td style="text-align:center">' + fmtDur(d.total_dur) + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    wrap.querySelectorAll('thead th[data-col]').forEach(function(th) {
      th.style.cursor = 'pointer';
      th.addEventListener('click', function() {
        var col = th.getAttribute('data-col');
        if (sortCol === col) sortDir = sortDir==='desc'?'asc':'desc';
        else { sortCol = col; sortDir = 'desc'; }
        document.getElementById('cm-exp-sort-info').textContent = 'Sorted by ' + th.textContent.trim();
        renderTable();
      });
    });
    document.getElementById('cm-exp-count').textContent = data.length + ' salesperson' + (data.length!==1?'s':'');
  }

  function renderChart() {
    var wrap = document.getElementById('cm-exp-chart-wrap');
    if (!wrap) return;
    if (chartInst) { chartInst.destroy(); chartInst = null; }
    var data = allData.slice().sort(function(a,b){ return (parseInt(b[activeMetric])||0)-(parseInt(a[activeMetric])||0); });
    var labels = data.map(function(d){ return d.employee_name||d.employee||'?'; });
    var values = data.map(function(d){ return parseInt(d[activeMetric])||0; });
    var color = METRIC_COLORS[activeMetric];
    var label = METRIC_LABELS[activeMetric];
    var canvas = document.getElementById('cm-exp-chart-canvas');
    chartInst = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{ label: label, data: values, backgroundColor: color+'cc', borderColor: color, borderWidth:1, borderRadius:4 }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position:'top', labels:{ boxWidth:12, font:{size:11} } },
          tooltip: { callbacks: { label: function(ctx){ return ' '+ctx.parsed.x; } } }
        },
        scales: {
          x: { beginAtZero:true, title:{ display:true, text:'Number of Calls', font:{size:11} } },
          y: { ticks:{ font:{size:11} } }
        }
      }
    });
    document.getElementById('cm-exp-count').textContent = data.length + ' salesperson' + (data.length!==1?'s':'');
    document.getElementById('cm-exp-sort-info').textContent = 'Showing ' + label;
  }

  function switchView(view) {
    activeView = view;
    var tableWrap = document.getElementById('cm-exp-table-wrap');
    var chartWrap = document.getElementById('cm-exp-chart-wrap');
    var metricTabs = document.querySelector('.cm-metric-tabs');
    var searchEl = document.getElementById('cm-exp-search');
    document.querySelectorAll('.cm-view-btn').forEach(function(b){ b.classList.toggle('active', b.getAttribute('data-view')===view); });
    if (view === 'table') {
      tableWrap.style.display = 'block';
      chartWrap.style.display = 'none';
      if (metricTabs) metricTabs.style.display = 'none';
      if (searchEl) searchEl.style.display = '';
      renderTable();
    } else {
      tableWrap.style.display = 'none';
      chartWrap.style.display = 'block';
      if (metricTabs) metricTabs.style.display = 'flex';
      if (searchEl) searchEl.style.display = 'none';
      renderChart();
    }
  }

  function fetchData() {
    return new Promise(function(resolve) {
      var from = (document.getElementById('cm-from')||{}).value || frappe.datetime.get_today();
      var to = (document.getElementById('cm-to')||{}).value || frappe.datetime.get_today();
      var emp = (document.getElementById('cm-emp')||{}).value || '';
      var team = (document.getElementById('cm-team')||{}).value || '';
      var dir = (document.getElementById('cm-dir')||{}).value || '';
      var src = (document.getElementById('cm-src')||{}).value || '';
      var dmin = (document.getElementById('cm-dmin')||{}).value || '';
      var dmax = (document.getElementById('cm-dmax')||{}).value || '';
      frappe.call({
        method: 'call_monitoring_data',
        args: { from_date:from, to_date:to, employee:emp, team:team, direction:dir, source:src, dur_min:dmin, dur_max:dmax, category:((document.getElementById('cm-cat')||{}).value||''), review:((document.getElementById('cm-rev')||{}).value||''), page:1, page_size:0 },
        callback: function(r) { resolve((r.message||{}).employee_data||[]); },
        error: function() { resolve([]); }
      });
      setTimeout(function(){ resolve([]); }, 8000);
    });
  }

  function openModal() {
    var m = document.getElementById('cm-expand-modal');
    if (!m) return;
    m.classList.add('cm-open');
    if (!dataLoaded) {
      document.getElementById('cm-exp-table-wrap').innerHTML = '<div class="cm-loading-msg">Loading data...</div>';
      fetchData().then(function(empData) {
        allData = empData;
        dataLoaded = true;
        activeView = 'table'; sortCol = 'total'; sortDir = 'desc'; searchQ = '';
        var s = document.getElementById('cm-exp-search'); if(s) s.value = '';
        switchView('table');
      });
    } else {
      activeView = 'table'; sortCol = 'total'; sortDir = 'desc'; searchQ = '';
      var s = document.getElementById('cm-exp-search'); if(s) s.value = '';
      switchView('table');
    }
  }

  function closeModal() {
    document.getElementById('cm-expand-modal').classList.remove('cm-open');
    if (chartInst) { chartInst.destroy(); chartInst = null; }
  }

  // Events
  var expandBtn = document.getElementById('cm-expand-btn');
  if (expandBtn) expandBtn.addEventListener('click', function(e){ e.stopPropagation(); openModal(); });

  modal.addEventListener('click', closeModal);
  var box = document.getElementById('cm-expand-box');
  if (box) box.addEventListener('click', function(e){ e.stopPropagation(); });

  var closeBtn = document.getElementById('cm-exp-close-btn');
  if (closeBtn) closeBtn.addEventListener('click', closeModal);

  document.addEventListener('keydown', function(e){ if(e.key==='Escape') closeModal(); });

  document.querySelectorAll('.cm-view-btn').forEach(function(btn){
    btn.addEventListener('click', function(){ switchView(btn.getAttribute('data-view')); });
  });

  document.querySelectorAll('.cm-metric-tab').forEach(function(tab){
    tab.addEventListener('click', function(){
      activeMetric = tab.getAttribute('data-metric');
      document.querySelectorAll('.cm-metric-tab').forEach(function(t){ t.classList.toggle('active', t===tab); });
      if (activeView==='chart') renderChart();
    });
  });

  var searchEl = document.getElementById('cm-exp-search');
  if (searchEl) searchEl.addEventListener('input', function(){ searchQ=searchEl.value; if(activeView==='table') renderTable(); });

  ['cm-apply','cm-reset'].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.addEventListener('click', function(){ dataLoaded=false; allData=[]; });
  });

});
// ============================================================


// ============================================================
// DEPARTMENT-WISE TABLES IN TEAM PERFORMANCE (NSH + ESH)
// ============================================================
frappe.ready(function() {

  // --- Styles ---
  var deptStyle = document.createElement('style');
  deptStyle.id = 'cm-dept-table-style';
  deptStyle.textContent = [
    '#cm-dept-tables{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 0 0 0;border-top:1px solid #e5e7eb;margin-top:12px;}',
    '.cm-dept-tbl-box{background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;}',
    '.cm-dept-tbl-head{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:#fff;border-bottom:1px solid #e5e7eb;}',
    '.cm-dept-tbl-title{font-size:13px;font-weight:600;color:#111;display:flex;align-items:center;gap:6px;}',
    '.cm-dept-tbl-badge{font-size:10px;font-weight:600;background:#dbeafe;color:#1e40af;padding:2px 7px;border-radius:10px;}',
    '.cm-dept-tbl-badge.green{background:#dcfce7;color:#14532d;}',
    '.cm-dept-tbl-wrap{max-height:300px;overflow-y:auto;}',
    '.cm-dept-tbl{width:100%;border-collapse:collapse;font-size:12px;}',
    '.cm-dept-tbl thead{position:sticky;top:0;background:#f3f4f6;z-index:1;}',
    '.cm-dept-tbl thead th{padding:7px 8px;text-align:left;font-size:10px;font-weight:600;color:#6b7280;border-bottom:1px solid #e5e7eb;white-space:nowrap;cursor:pointer;user-select:none;}',
    '.cm-dept-tbl thead th:hover{color:#111;}',
    '.cm-dept-tbl thead th.sort-asc::after{content:" ↑";color:#2563eb;}',
    '.cm-dept-tbl thead th.sort-desc::after{content:" ↓";color:#2563eb;}',
    '.cm-dept-tbl tbody tr:hover{background:#f0f9ff;}',
    '.cm-dept-tbl tbody td{padding:7px 8px;border-bottom:1px solid #f3f4f6;color:#111;}',
    '.cm-dept-tbl tbody tr:last-child td{border-bottom:none;}',
    '.cm-dept-rank{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;font-size:10px;font-weight:700;}',
    '.cm-dept-rank.r1{background:#fef3c7;color:#92400e;}',
    '.cm-dept-rank.r2{background:#f1f5f9;color:#334155;}',
    '.cm-dept-rank.r3{background:#fef2f2;color:#7c2d12;}',
    '.cm-dept-rank.rn{background:#f9fafb;color:#6b7280;}',
    '.cm-dept-mini-bar{height:4px;border-radius:2px;margin-top:2px;}',
    '.cm-dept-tbl-footer{padding:6px 14px;font-size:10px;color:#9ca3af;text-align:right;border-top:1px solid #f3f4f6;background:#f9fafb;}',
    '.cm-dept-missed{color:#ef4444;font-weight:600;}',
    '.cm-dept-loading-row td{text-align:center;color:#9ca3af;padding:20px;}'
  ].join('');
  document.head.appendChild(deptStyle);

  // --- HTML ---
  var deptSection = document.createElement('div');
  deptSection.id = 'cm-dept-tables';
  deptSection.innerHTML =
    '<div class="cm-dept-tbl-box" id="cm-dept-nsh-box">' +
      '<div class="cm-dept-tbl-head"><span class="cm-dept-tbl-title">' +
        '<span style="color:#3b82f6">&#9632;</span> National Sales Head' +
        '<span class="cm-dept-tbl-badge" id="cm-dept-nsh-badge">&mdash;</span>' +
      '</span></div>' +
      '<div class="cm-dept-tbl-wrap"><table class="cm-dept-tbl" id="cm-dept-nsh-tbl">' +
        '<thead><tr>' +
          '<th style="width:30px">#</th>' +
          '<th data-dept="nsh" data-col="employee_name">SALESPERSON</th>' +
          '<th class="sort-desc" data-dept="nsh" data-col="total" style="text-align:center">TOTAL</th>' +
          '<th data-dept="nsh" data-col="outgoing" style="text-align:center">OUT</th>' +
          '<th data-dept="nsh" data-col="incoming" style="text-align:center">IN</th>' +
          '<th data-dept="nsh" data-col="missed" style="text-align:center">MISSED</th>' +
          '<th data-dept="nsh" data-col="total_dur" style="text-align:center">TALK TIME</th>' +
        '</tr></thead>' +
        '<tbody><tr class="cm-dept-loading-row"><td colspan="7">Loading...</td></tr></tbody>' +
      '</table></div>' +
      '<div class="cm-dept-tbl-footer" id="cm-dept-nsh-footer">&mdash;</div>' +
    '</div>' +
    '<div class="cm-dept-tbl-box" id="cm-dept-esh-box">' +
      '<div class="cm-dept-tbl-head"><span class="cm-dept-tbl-title">' +
        '<span style="color:#10b981">&#9632;</span> Exports Sales Head' +
        '<span class="cm-dept-tbl-badge green" id="cm-dept-esh-badge">&mdash;</span>' +
      '</span></div>' +
      '<div class="cm-dept-tbl-wrap"><table class="cm-dept-tbl" id="cm-dept-esh-tbl">' +
        '<thead><tr>' +
          '<th style="width:30px">#</th>' +
          '<th data-dept="esh" data-col="employee_name">SALESPERSON</th>' +
          '<th class="sort-desc" data-dept="esh" data-col="total" style="text-align:center">TOTAL</th>' +
          '<th data-dept="esh" data-col="outgoing" style="text-align:center">OUT</th>' +
          '<th data-dept="esh" data-col="incoming" style="text-align:center">IN</th>' +
          '<th data-dept="esh" data-col="missed" style="text-align:center">MISSED</th>' +
          '<th data-dept="esh" data-col="total_dur" style="text-align:center">TALK TIME</th>' +
        '</tr></thead>' +
        '<tbody><tr class="cm-dept-loading-row"><td colspan="7">Loading...</td></tr></tbody>' +
      '</table></div>' +
      '<div class="cm-dept-tbl-footer" id="cm-dept-esh-footer">&mdash;</div>' +
    '</div>';

  var teamCard = document.getElementById('cm-team-card');
  if (teamCard) teamCard.appendChild(deptSection);

  // --- State & Logic ---
  var deptState = {
    nsh: { data: null, sortCol: 'total', sortDir: 'desc' },
    esh: { data: null, sortCol: 'total', sortDir: 'desc' }
  };

  function fmtDurD(secs) {
    secs = parseInt(secs) || 0;
    if (secs < 60) return secs + 's';
    var h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60), s = secs%60;
    if (h > 0) return h + 'h ' + String(m).padStart(2,'0') + 'm';
    return m + 'm ' + String(s).padStart(2,'0') + 's';
  }

  function getDeptFilters() {
    return {
      from_date: (document.getElementById('cm-from')||{}).value || frappe.datetime.get_today(),
      to_date:   (document.getElementById('cm-to')||{}).value || frappe.datetime.get_today(),
      direction: (document.getElementById('cm-dir')||{}).value || '',
      source:    (document.getElementById('cm-src')||{}).value || '',
      dur_min:   (document.getElementById('cm-dmin')||{}).value || '',
      dur_max:   (document.getElementById('cm-dmax')||{}).value || ''
    };
  }

  function fetchDeptData(teamName) {
    return new Promise(function(resolve) {
      var f = getDeptFilters();
      frappe.call({
        method: 'call_monitoring_data',
        args: { from_date:f.from_date, to_date:f.to_date, employee:'', team:teamName, direction:f.direction, source:f.source, dur_min:f.dur_min, dur_max:f.dur_max, page:1, page_size:0 },
        callback: function(r) { resolve((r.message||{}).employee_data||[]); },
        error: function() { resolve([]); }
      });
      setTimeout(function(){ resolve([]); }, 8000);
    });
  }

  function renderDeptTbl(key, data) {
    var tbl = document.getElementById('cm-dept-' + key + '-tbl');
    var footer = document.getElementById('cm-dept-' + key + '-footer');
    var badge = document.getElementById('cm-dept-' + key + '-badge');
    if (!tbl) return;
    var tbody = tbl.querySelector('tbody');
    if (!data || data.length === 0) {
      tbody.innerHTML = '<tr class="cm-dept-loading-row"><td colspan="7">No data for this period</td></tr>';
      if (footer) footer.textContent = '0 salespersons';
      if (badge) badge.textContent = '0';
      return;
    }
    var sc = deptState[key].sortCol, sd = deptState[key].sortDir;
    var sorted = data.slice().sort(function(a,b){
      var av = sc==='employee_name'?(a.employee_name||''):(parseInt(a[sc])||0);
      var bv = sc==='employee_name'?(b.employee_name||''):(parseInt(b[sc])||0);
      if (typeof av==='string') return sd==='asc'?av.localeCompare(bv):bv.localeCompare(av);
      return sd==='asc'?av-bv:bv-av;
    });
    var maxTotal = Math.max.apply(null, data.map(function(d){ return parseInt(d.total)||0; }).concat([1]));
    var barColor = key==='nsh'?'#3b82f6':'#10b981';
    var html = '';
    sorted.forEach(function(d,i){
      var rank=i+1, cls=rank===1?'r1':rank===2?'r2':rank===3?'r3':'rn';
      var barW = Math.round((parseInt(d.total)||0)/maxTotal*100);
      var missed=parseInt(d.missed)||0;
      html += '<tr>' +
        '<td><span class="cm-dept-rank '+cls+'">' + rank + '</span></td>' +
        '<td><div style="font-weight:500;font-size:12px">' + (d.employee_name||d.employee||'-') + '</div>' +
          '<div class="cm-dept-mini-bar" style="width:'+barW+'%;background:'+barColor+'"></div></td>' +
        '<td style="text-align:center;font-weight:700">' + (d.total||0) + '</td>' +
        '<td style="text-align:center">' + (d.outgoing||0) + '</td>' +
        '<td style="text-align:center">' + (d.incoming||0) + '</td>' +
        '<td style="text-align:center" class="' + (missed>0?'cm-dept-missed':'') + '">' + missed + '</td>' +
        '<td style="text-align:center">' + fmtDurD(d.total_dur) + '</td>' +
        '</tr>';
    });
    tbody.innerHTML = html;
    tbl.querySelectorAll('thead th[data-col]').forEach(function(th){
      th.className = th.getAttribute('data-col')===sc ? 'sort-'+sd : '';
    });
    if (footer) footer.textContent = sorted.length + ' salesperson' + (sorted.length!==1?'s':'') + ' · Sorted by ' + sc.replace('_',' ');
    if (badge) badge.textContent = sorted.length;
  }

  function loadDept(key, teamName) {
    var tbody = document.querySelector('#cm-dept-' + key + '-tbl tbody');
    if (tbody) tbody.innerHTML = '<tr class="cm-dept-loading-row"><td colspan="7">Loading...</td></tr>';
    fetchDeptData(teamName).then(function(data) {
      deptState[key].data = data;
      renderDeptTbl(key, data);
    });
  }

  // Sort headers
  document.querySelectorAll('.cm-dept-tbl thead th[data-col][data-dept]').forEach(function(th) {
    th.style.cursor = 'pointer';
    th.addEventListener('click', function(){
      var key = th.getAttribute('data-dept'), col = th.getAttribute('data-col');
      if (deptState[key].sortCol===col) deptState[key].sortDir = deptState[key].sortDir==='desc'?'asc':'desc';
      else { deptState[key].sortCol=col; deptState[key].sortDir='desc'; }
      if (deptState[key].data) renderDeptTbl(key, deptState[key].data);
    });
  });

  // Refresh on filter apply/reset
  ['cm-apply','cm-reset'].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.addEventListener('click', function(){
      setTimeout(function(){
        deptState.nsh.data=null; deptState.esh.data=null;
        loadDept('nsh','National Sales Head'); loadDept('esh','Exports Sales Head');
      }, 500);
    });
  });

  // Initial load
  loadDept('nsh', 'National Sales Head');
  loadDept('esh', 'Exports Sales Head');

});
// ============================================================
