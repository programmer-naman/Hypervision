(() => {
  'use strict';
  const data = window.HyperVisionData || {};
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  let currentTable = 'sales';
  let latestExperimentId = null;
  let currentVM = null;
  let toastTimer = null;
  let autoRefreshInterval = null;

  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `API request failed (${response.status})`);
    }
    return payload;
  }

  function toast(message) {
    const el = $('#toast');
    if (!el) return;
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
  }

  function showModal(title, message) {
    const modalTitle = $('#modalTitle');
    const modalMessage = $('#modalMessage');
    const backdrop = $('#modalBackdrop');
    if (modalTitle) modalTitle.textContent = title;
    if (modalMessage) modalMessage.textContent = message;
    if (backdrop) backdrop.classList.remove('hidden');
  }

  function closeModal() {
    const backdrop = $('#modalBackdrop');
    if (backdrop) backdrop.classList.add('hidden');
  }

  // ---------------------------------------------------------------------------
  // Page Navigation
  // ---------------------------------------------------------------------------

  function goToPage(page) {
    const target = $(`#page-${page}`);
    if (!target) return;
    $$('.page-view').forEach(view => view.classList.toggle('active', view === target));
    $$('.nav-item[data-page]').forEach(button => button.classList.toggle('active', button.dataset.page === page));

    const nav = $(`.nav-item[data-page="${page}"]`);
    if ($('#breadcrumbTitle')) {
      $('#breadcrumbTitle').textContent = nav ? nav.textContent.trim().replace(/\s+/g, ' ') : page;
    }

    $('#sidebar')?.classList.remove('open');
    window.scrollTo({ top: 0, behavior: 'smooth' });

    if (page === 'overview') loadOverviewData();
    if (page === 'database') { loadTables(); loadIndexes(); }
    if (page === 'monitoring') { refreshMetrics(); loadVMStatus(); loadVMHistory(); loadProcesses(); }
    if (page === 'analysis') { loadAnalysis(); }
    if (page === 'experiments') { loadHistory(); populateCompareDropdowns(); }
  }

  // ---------------------------------------------------------------------------
  // Overview Dashboard
  // ---------------------------------------------------------------------------

  async function loadOverviewData() {
    try {
      const payload = await api('/api/overview');
      if ($('#metricQueries')) {
        const succ = payload.successful_queries ?? payload.total_queries;
        const fail = payload.failed_queries ?? 0;
        $('#metricQueries').innerHTML = `${Number(payload.total_queries).toLocaleString()}<small style="display:block;font-size:10px;color:#27997d;margin-top:4px">${succ} success · ${fail} err</small>`;
      }
      if ($('#metricLatency')) {
        const avg = payload.avg_latency_ms ? Number(payload.avg_latency_ms).toFixed(1) : '—';
        const latest = payload.latest_latency_ms ? ` (Latest: ${payload.latest_latency_ms} ms)` : '';
        $('#metricLatency').textContent = avg;
      }

      const m = payload.metrics || {};
      const cpu = Number(m.cpu_percent || 0);
      const mem = Number(m.memory_percent || 0);
      if ($('#metricCpu')) $('#metricCpu').textContent = cpu.toFixed(1);
      if ($('#cpuProgress')) $('#cpuProgress').style.width = `${Math.min(100, cpu)}%`;
      if ($('#legendCpu')) $('#legendCpu').textContent = `${cpu.toFixed(1)}%`;
      if ($('#legendMemory')) $('#legendMemory').textContent = `${mem.toFixed(1)}%`;
      if ($('#legendHeadroom')) $('#legendHeadroom').textContent = `${Math.max(0, 100 - mem).toFixed(1)}%`;
      if ($('#donutCpuText')) $('#donutCpuText').textContent = `${cpu.toFixed(0)}%`;
      if ($('#resourceDonut')) {
        $('#resourceDonut').style.background = `conic-gradient(#8370ee 0 ${cpu}%, #6d9aee ${cpu}% ${Math.min(100, cpu + mem / 2)}%, #edf0f7 ${Math.min(100, cpu + mem / 2)}% 100%)`;
      }
      if ($('#metricMemory')) $('#metricMemory').textContent = Number(m.process_memory_mb || 0).toFixed(1);

      // Slowest queries
      const slowestContainer = $('#slowestQueriesList');
      if (slowestContainer && payload.slowest_queries) {
        if (!payload.slowest_queries.length) {
          slowestContainer.innerHTML = '<div style="color:#858ba0;font-size:10px">No queries executed yet.</div>';
        } else {
          slowestContainer.innerHTML = payload.slowest_queries.map(q => `
            <div class="slowest-item">
              <span class="slowest-sql" title="${escapeHTML(q.sql)}">${escapeHTML(q.sql)}</span>
              <span class="type-label" style="background:#fff3e7;color:#d46b08">${Number(q.latency_ms).toFixed(1)} ms</span>
            </div>
          `).join('');
        }
      }

      // Insights & Bottlenecks
      const insightsContainer = $('#overviewInsightsList');
      const bList = payload.detected_bottlenecks || [];
      const recs = payload.latest_recommendations || [];
      if ($('#overviewInsightCount')) $('#overviewInsightCount').textContent = String(bList.length + recs.length);

      if (insightsContainer) {
        let html = '';
        bList.forEach(b => {
          html += `
            <div class="insight-item">
              <div class="insight-icon ${b.category.includes('Optimal') ? 'good' : 'warn'}">${b.category.includes('Optimal') ? '✓' : '!'}</div>
              <div>
                <strong>${escapeHTML(b.source)}: ${escapeHTML(b.category)}</strong>
                <p>Confidence: ${(Number(b.confidence || 0) * 100).toFixed(1)}%</p>
                <button data-goto="analysis">View details →</button>
              </div>
              <span class="severity ${b.category.includes('Optimal') ? 'low' : 'medium'}">${escapeHTML(b.category.toUpperCase().slice(0, 8))}</span>
            </div>
          `;
        });
        recs.slice(0, 2).forEach(r => {
          html += `
            <div class="insight-item">
              <div class="insight-icon purple">✳</div>
              <div>
                <strong>${escapeHTML(r.title || r.action || 'Optimization')}</strong>
                <p>${escapeHTML(r.description || r.reason || '')}</p>
                <button data-goto="analysis">Review action →</button>
              </div>
              <span class="severity medium">SUGGESTION</span>
            </div>
          `;
        });
        insightsContainer.innerHTML = html || '<div class="schema-tip"><strong>Optimal state</strong><p>Queries running smoothly with no immediate bottlenecks detected.</p></div>';
        $$('[data-goto="analysis"]', insightsContainer).forEach(btn => btn.addEventListener('click', () => goToPage('analysis')));
      }

      // Recent queries table
      renderRecentQueries(payload.recent_history || []);
      renderPerformanceChartFromHistory(payload.recent_history || []);

    } catch (err) {
      console.warn('Could not load overview stats:', err);
    }
  }

  function renderRecentQueries(history) {
    const tbody = $('#recentQueriesBody');
    if (!tbody) return;
    if (!history.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#858ba0">No queries executed yet. Run a query in the SQL Query Lab.</td></tr>';
      return;
    }
    tbody.innerHTML = history.slice(0, 8).map(q => {
      const type = q.statement_type || (q.sql ? q.sql.trim().split(/\s+/)[0].toUpperCase() : 'SELECT');
      return `
        <tr>
          <td><span class="query-code" title="${escapeHTML(q.sql)}">${escapeHTML(q.sql.length > 55 ? q.sql.slice(0, 55) + '…' : q.sql)}</span></td>
          <td><span class="type-label">${escapeHTML(type)}</span></td>
          <td>${Number(q.latency_ms || 0).toFixed(2)} ms</td>
          <td>${Number(q.row_count || q.rows_affected || 0).toLocaleString()}</td>
          <td><span class="status-label">Complete</span></td>
        </tr>
      `;
    }).join('');
  }

  function renderPerformanceChartFromHistory(history) {
    const svg = $('#performanceChart');
    if (!svg) return;
    const items = history.slice(0, 12).reverse();
    const values = items.map(q => Number(q.latency_ms || 0));
    if (!values.length) {
      svg.innerHTML = '';
      if ($('#performanceLabels')) $('#performanceLabels').innerHTML = '<span>No queries executed yet</span>';
      return;
    }
    const width = 700, height = 230, pad = 12;
    const maxVal = Math.max(10, Math.max(...values) * 1.15);
    const x = i => pad + (width - pad * 2) * (values.length === 1 ? 0.5 : i / (values.length - 1));
    const y = val => height - pad - (val / maxVal) * (height - pad * 2);

    const points = values.map((val, i) => `${x(i)},${y(val)}`).join(' ');
    const area = `${x(0)},${height} ${points} ${x(values.length - 1)},${height}`;

    svg.innerHTML = `
      <defs>
        <linearGradient id="chartGradient" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#7865e8" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#7865e8" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="${area}" fill="url(#chartGradient)"/>
      <polyline points="${points}" fill="none" stroke="#7865e8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      ${values.map((val, i) => `<circle cx="${x(i)}" cy="${y(val)}" r="4" fill="#fff" stroke="#7865e8" stroke-width="2"><title>${items[i].id || ''}: ${val.toFixed(2)} ms</title></circle>`).join('')}
    `;

    if ($('#performanceLabels')) {
      $('#performanceLabels').innerHTML = items.map(item => `<span>${escapeHTML((item.id || item.experiment_id || '').replace('EXP-', ''))}</span>`).join('');
    }
    if ($('#performanceYAxis')) {
      $('#performanceYAxis').innerHTML = `<span>${maxVal.toFixed(0)}</span><span>${(maxVal*0.75).toFixed(0)}</span><span>${(maxVal*0.5).toFixed(0)}</span><span>${(maxVal*0.25).toFixed(0)}</span><span>0</span>`;
    }
  }

  // ---------------------------------------------------------------------------
  // SQL Query Lab
  // ---------------------------------------------------------------------------

  const PRESETS = {
    custom: '',
    aggregate: 'SELECT department, AVG(salary) AS avg_salary, COUNT(*) AS emp_count\nFROM sales\nWHERE salary > 50000\nGROUP BY department\nORDER BY avg_salary DESC\nLIMIT 10;',
    join: 'SELECT sales.department, orders.amount, orders.order_id\nFROM sales\nJOIN orders ON sales.employee_id = orders.employee_id\nWHERE orders.amount > 2000\nLIMIT 20;',
    filter: 'SELECT employee_id, department, region, salary\nFROM sales\nWHERE (region = \'North\' OR region = \'South\') AND salary >= 80000\nORDER BY salary DESC\nLIMIT 15;',
    insert: 'INSERT INTO sales (employee_id, department, region, salary)\nVALUES (90001, \'Engineering\', \'West\', 95000);',
    update: 'UPDATE sales\nSET salary = 105000\nWHERE employee_id = 90001;',
    delete: 'DELETE FROM sales\nWHERE employee_id = 90001;',
    create_index: 'CREATE INDEX idx_sales_salary ON sales (salary) USING BTREE;'
  };

  function initQueryLab() {
    const presetSelect = $('#queryPreset');
    const editor = $('#sqlEditor');
    if (presetSelect && editor) {
      presetSelect.addEventListener('change', () => {
        const val = presetSelect.value;
        if (PRESETS[val]) {
          editor.value = PRESETS[val];
          updateLineNumbers();
        }
      });
      editor.addEventListener('input', updateLineNumbers);
      editor.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          executeActiveQuery();
        }
      });
      updateLineNumbers();
    }

    $('#clearQuery')?.addEventListener('click', () => {
      if (editor) { editor.value = ''; updateLineNumbers(); }
    });
    $('#runQuery')?.addEventListener('click', executeActiveQuery);
    $('#schemaRefreshBtn')?.addEventListener('click', loadTables);
  }

  function updateLineNumbers() {
    const editor = $('#sqlEditor');
    const lineNumbers = $('#lineNumbers');
    const charCount = $('#editorCharCount');
    if (!editor || !lineNumbers) return;
    const lines = editor.value.split('\n').length;
    lineNumbers.innerHTML = Array.from({ length: lines }, (_, i) => i + 1).join('<br>');
    if (charCount) charCount.textContent = `${editor.value.length} characters`;
  }

  async function executeActiveQuery() {
    const editor = $('#sqlEditor');
    const runBtn = $('#runQuery');
    if (!editor || !editor.value.trim()) {
      toast('Enter a SQL statement first.');
      return;
    }
    const sql = editor.value.trim();
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = 'Executing…'; }

    try {
      const payload = await api('/api/query', {
        method: 'POST',
        body: JSON.stringify({ sql })
      });

      renderQueryResult(payload);
      toast(`Executed in ${Number(payload.latency_ms || 0).toFixed(2)} ms`);
      // Refresh background history & overview
      loadOverviewData();
      loadTables();
      loadIndexes();
    } catch (err) {
      renderQueryError(err.message);
    } finally {
      if (runBtn) { runBtn.disabled = false; runBtn.textContent = '▶ Run query'; }
    }
  }

  function renderQueryResult(result) {
    const summary = $('#resultSummary');
    const durationChip = $('#queryDuration');
    const indexBadge = $('#indexBadge');
    const emptyArea = $('#queryResultArea');
    const tableWrap = $('#queryResultTableWrap');
    const thead = $('#queryResultHead');
    const tbody = $('#queryResultBody');
    const planArea = $('#queryPlan');
    const envelopeArea = $('#runEnvelope');
    const bottleneckArea = $('#queryBottleneck');

    if (emptyArea) emptyArea.classList.add('hidden');
    if (tableWrap) tableWrap.classList.remove('hidden');

    if (durationChip) durationChip.textContent = `${Number(result.latency_ms || 0).toFixed(2)} ms`;

    // Index badge
    if (indexBadge) {
      if (result.index_used) {
        indexBadge.textContent = `INDEX USED: ${result.index_used}`;
        indexBadge.classList.remove('hidden');
      } else {
        indexBadge.classList.add('hidden');
      }
    }

    if (result.statement_type === 'SELECT') {
      const rowCount = result.row_count || (result.rows ? result.rows.length : 0);
      if (summary) summary.textContent = `Returned ${rowCount} rows in ${Number(result.latency_ms).toFixed(2)} ms`;

      // Render columns
      const cols = result.columns || [];
      if (thead) {
        thead.innerHTML = `<tr>${cols.map(c => `<th>${escapeHTML(c).toUpperCase()}</th>`).join('')}</tr>`;
      }
      // Render rows
      if (tbody) {
        const rows = result.rows || [];
        if (!rows.length) {
          tbody.innerHTML = `<tr><td colspan="${cols.length || 1}" style="text-align:center;color:#858ba0">Empty result set (0 rows).</td></tr>`;
        } else {
          tbody.innerHTML = rows.slice(0, 100).map(row => {
            const vals = Array.isArray(row) ? row : cols.map(c => row[c]);
            return `<tr>${vals.map(v => `<td>${v === null ? '<em style="color:#b5b8c8">NULL</em>' : escapeHTML(v)}</td>`).join('')}</tr>`;
          }).join('');
        }
      }

      // Plan steps
      if (planArea && result.plan) {
        planArea.classList.remove('hidden');
        const stepsContainer = $('#planSteps');
        if (stepsContainer) {
          stepsContainer.innerHTML = (result.operator_stats || result.plan).map(step => {
            const opName = typeof step === 'string' ? step : (step.op || 'OPERATOR');
            const ms = step.ms !== undefined ? `${step.ms} ms` : '';
            const rowsInfo = step.rows_out !== undefined ? `${step.rows_in} in → ${step.rows_out} out` : '';
            return `
              <div class="plan-step">
                <span>OPERATOR</span>
                <strong>${escapeHTML(opName)}</strong>
                ${ms ? `<small>${ms}</small>` : ''}
                ${rowsInfo ? `<small>${rowsInfo}</small>` : ''}
              </div>
            `;
          }).join('');
        }
      }

      // Envelope
      if (envelopeArea && result.settings) {
        envelopeArea.classList.remove('hidden');
        if ($('#runEnvelopeTitle')) $('#runEnvelopeTitle').textContent = `Experiment ID: ${result.experiment_id || 'TEMP'}`;
        if ($('#runEnvelopeDetail')) {
          $('#runEnvelopeDetail').textContent = `Threads: ${result.settings.thread_count || 1} · Pushdown: ${result.settings.pushdown_filter} · Streaming Agg: ${result.settings.streaming_aggregation} · Compiled: ${result.settings.compiled_predicates}`;
        }
        if ($('#runEnvelopeMetrics')) {
          $('#runEnvelopeMetrics').innerHTML = `
            <span><small>LATENCY</small><strong>${Number(result.latency_ms).toFixed(1)} ms</strong></span>
            <span><small>CPU TIME</small><strong>${Number(result.cpu_time_ms || 0).toFixed(1)} ms</strong></span>
            <span><small>HEAP PEAK</small><strong>${result.heap_peak_mb != null ? Number(result.heap_peak_mb).toFixed(2) + ' MB' : '—'}</strong></span>
            <span><small>ROWS PROCESSED</small><strong>${result.row_count}</strong></span>
          `;
        }
      }

      // Bottleneck mini
      if (bottleneckArea) {
        bottleneckArea.classList.remove('hidden');
        const bBody = $('#queryBottleneckBody');
        const cls = result.classification || {};
        if (bBody) {
          bBody.innerHTML = `
            <div style="display:grid;grid-template-columns:1fr;gap:12px;margin-top:10px">
              <div style="padding:12px;background:#fbfbfe;border:1px solid #eef0f8;border-radius:9px">
                <span class="eyebrow">RULE-BASED CLASSIFIER</span>
                <strong style="display:block;font-size:14px;color:#353b52;margin:4px 0">${escapeHTML(cls.classification || 'Unknown')}</strong>
                <p style="font-size:11px;color:#787e95;margin:0">${escapeHTML(result.recommendation || '')}</p>
              </div>
            </div>
          `;
        }
      }

      // Render actionable recommendations and Before/After comparison
      renderRecommendations(result);
      if (result.comparison) {
        renderComparisonTable(result.comparison, '#queryComparisonPanel');
      } else {
        $('#queryComparisonPanel')?.classList.add('hidden');
      }

    } else {
      // DML or DDL
      if (summary) summary.textContent = result.message || 'Operation completed successfully.';
      if (thead) thead.innerHTML = '<tr><th>STATUS</th><th>RESULT MESSAGE</th><th>ROWS AFFECTED</th><th>LATENCY</th></tr>';
      if (tbody) {
        tbody.innerHTML = `
          <tr>
            <td><span class="status-label">Success</span></td>
            <td><strong>${escapeHTML(result.message || 'Statement executed')}</strong></td>
            <td>${result.rows_affected ?? 0}</td>
            <td>${Number(result.latency_ms).toFixed(2)} ms</td>
          </tr>
        `;
      }
      if (planArea) planArea.classList.add('hidden');
      if (envelopeArea) envelopeArea.classList.add('hidden');
      if (bottleneckArea) bottleneckArea.classList.add('hidden');
      $('#queryRecommendationsPanel')?.classList.add('hidden');
      $('#queryComparisonPanel')?.classList.add('hidden');
    }
  }

  function renderQueryError(errorMessage) {
    const summary = $('#resultSummary');
    const tableWrap = $('#queryResultTableWrap');
    const emptyArea = $('#queryResultArea');
    const thead = $('#queryResultHead');
    const tbody = $('#queryResultBody');
    if (summary) summary.textContent = 'Execution failed.';
    if (emptyArea) emptyArea.classList.add('hidden');
    if (tableWrap) tableWrap.classList.remove('hidden');
    $('#queryRecommendationsPanel')?.classList.add('hidden');
    $('#queryComparisonPanel')?.classList.add('hidden');
    if (thead) thead.innerHTML = '<tr><th style="color:#cf1322">ERROR</th><th>DETAILS</th></tr>';
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td><span class="tag tag-warn" style="background:#fff2f0;color:#cf1322">SYNTAX / RUNTIME ERROR</span></td>
          <td style="color:#a8071a;font-weight:600;font-family:monospace">${escapeHTML(errorMessage)}</td>
        </tr>
      `;
    }
    toast(`Error: ${errorMessage}`);
  }

  // ---------------------------------------------------------------------------
  // Database Explorer (Tables, Indexes)
  // ---------------------------------------------------------------------------

  async function loadTables() {
    try {
      const payload = await api('/api/tables');
      const tables = (payload.tables || []);
      const container = $('#schemaBrowserContainer');
      const dbList = $('#dbTablesList');
      const indexSelect = $('#indexTableSelect');
      const benchmarkSelect = $('#benchmarkTableSelect');

      if (indexSelect) indexSelect.innerHTML = tables.map(t => `<option value="${t.name}">${t.name}</option>`).join('');
      if (benchmarkSelect) benchmarkSelect.innerHTML = tables.map(t => `<option value="${t.name}">${t.name}</option>`).join('');

      if (container) {
        container.innerHTML = tables.map(t => `
          <div class="schema-table">
            <div class="schema-table-head">
              <span>▦</span><strong>${escapeHTML(t.name)}</strong>
              <small>${Number(t.row_count).toLocaleString()} rows</small>
            </div>
            <div class="schema-columns">
              ${t.columns.map(c => `
                <div>
                  <span class="type-icon">${(t.column_types && t.column_types[c] === 'INT') ? '#' : 'Aa'}</span>
                  ${escapeHTML(c)} <em>${escapeHTML((t.column_types && t.column_types[c]) || 'VARCHAR')}</em>
                </div>
              `).join('')}
            </div>
          </div>
        `).join('');
      }

      if (dbList) {
        dbList.innerHTML = tables.map((t, idx) => `
          <button class="db-table-card ${(currentTable ? t.name === currentTable : idx === 0) ? 'selected' : ''}" data-table="${escapeHTML(t.name)}">
            <span class="db-table-icon">▦</span>
            <span>
              <strong>${escapeHTML(t.name)}</strong>
              <small>${escapeHTML(t.columns.join(', '))}</small>
            </span>
            <em>${Number(t.row_count).toLocaleString()} rows</em>
          </button>
        `).join('');

        $$('.db-table-card', dbList).forEach(btn => {
          btn.addEventListener('click', () => {
            $$('.db-table-card', dbList).forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            currentTable = btn.dataset.table;
            loadTableSample(currentTable);
          });
        });
      }

      if (tables.length) {
        if (!currentTable || !tables.some(t => t.name === currentTable)) {
          currentTable = tables[0].name;
        }
        loadTableSample(currentTable);
      }
    } catch (err) {
      console.warn('Error loading tables:', err);
    }
  }

  async function loadTableSample(tableName) {
    try {
      const payload = await api(`/api/tables/${encodeURIComponent(tableName)}?limit=25`);
      if ($('#tableDetailTitle')) $('#tableDetailTitle').textContent = payload.name;
      if ($('#tableDetailSubtitle')) $('#tableDetailSubtitle').textContent = `${Number(payload.row_count).toLocaleString()} rows · Table Schema & Sample Records`;

      const thead = $('#dbTableHead');
      const tbody = $('#dbTableBody');
      if (thead) thead.innerHTML = `<tr>${payload.columns.map(c => `<th>${escapeHTML(c).toUpperCase()}</th>`).join('')}</tr>`;
      if (tbody) {
        tbody.innerHTML = payload.rows.map(row => {
          return `<tr>${payload.columns.map(c => `<td>${row[c] === null ? '<em>NULL</em>' : escapeHTML(row[c])}</td>`).join('')}</tr>`;
        }).join('');
      }
    } catch (err) {
      console.warn('Error loading table sample:', err);
    }
  }

  // --- Indexes ---
  async function loadIndexes() {
    try {
      const payload = await api('/api/indexes');
      const tbody = $('#indexesTableBody');
      if (!tbody) return;
      if (!payload.indexes.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#858ba0">No indexes created yet. Create one above!</td></tr>';
        return;
      }
      tbody.innerHTML = payload.indexes.map(idx => `
        <tr>
          <td><strong>${escapeHTML(idx.name)}</strong></td>
          <td>${escapeHTML(idx.table_name)}</td>
          <td><code>${escapeHTML(idx.column)}</code></td>
          <td><span class="feature-badge ${idx.index_type === 'BTREE' ? 'badge-btree' : 'badge-hash'}">${escapeHTML(idx.index_type)}</span></td>
          <td>${idx.entries_count}</td>
          <td><button class="action-btn danger btn-drop-index" data-name="${escapeHTML(idx.name)}" data-table="${escapeHTML(idx.table_name)}">Drop</button></td>
        </tr>
      `).join('');

      $$('.btn-drop-index', tbody).forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm(`Drop index '${btn.dataset.name}'?`)) return;
          try {
            await api('/api/indexes/drop', {
              method: 'POST',
              body: JSON.stringify({ name: btn.dataset.name, table: btn.dataset.table })
            });
            toast(`Index '${btn.dataset.name}' dropped.`);
            loadIndexes();
          } catch (e) {
            toast(e.message);
          }
        });
      });
    } catch (err) {
      console.warn('Error loading indexes:', err);
    }
  }

  function initIndexForm() {
    const form = $('#createIndexForm');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const table = $('#indexTableSelect').value;
      const column = $('#indexColumnInput').value.trim();
      const index_type = $('#indexTypeSelect').value;
      const name = $('#indexNameInput').value.trim();

      try {
        const payload = await api('/api/indexes', {
          method: 'POST',
          body: JSON.stringify({ table, column, index_type, name })
        });
        toast(payload.message || 'Index created.');
        $('#indexColumnInput').value = '';
        $('#indexNameInput').value = '';
        loadIndexes();
      } catch (err) {
        toast(`Error: ${err.message}`);
      }
    });
  }

  // ---------------------------------------------------------------------------
  // System Monitoring & VM Controls
  // ---------------------------------------------------------------------------

  async function refreshMetrics() {
    try {
      const payload = await api('/api/metrics');
      const m = payload.metrics || {};
      const cpu = Number(m.cpu_percent || 0);
      const mem = Number(m.memory_percent || 0);

      if ($('#monitorCpu')) $('#monitorCpu').textContent = cpu.toFixed(1);
      if ($('#monitorCpuBar')) $('#monitorCpuBar').style.width = `${Math.min(100, cpu)}%`;
      if ($('#monitorMem')) $('#monitorMem').textContent = mem.toFixed(1);
      if ($('#monitorMemBar')) $('#monitorMemBar').style.width = `${Math.min(100, mem)}%`;
      if ($('#monitorMemDetail')) $('#monitorMemDetail').textContent = `${Number(m.memory_used_mb || 0).toLocaleString()} MB of ${Number(m.memory_total_mb || 0).toLocaleString()} MB`;
      if ($('#monitorThreads')) $('#monitorThreads').textContent = m.process_threads || 0;
      if ($('#monitorProcCount')) $('#monitorProcCount').textContent = `${m.process_count || 0} active OS processes`;

      // Resource chart
      const history = payload.history || [];
      renderResourceHistoryChart(history);
    } catch (err) {
      console.warn('Error refreshing metrics:', err);
    }
  }

  function renderResourceHistoryChart(history) {
    const svg = $('#resourceChart');
    if (!svg || !history.length) return;
    const width = 700, height = 230, pad = 10;
    const cpuVals = history.slice(-20).map(x => Number(x.cpu_percent || 0));
    const memVals = history.slice(-20).map(x => Number(x.memory_percent || 0));

    const x = i => pad + (width - pad * 2) * (cpuVals.length === 1 ? 0.5 : i / (cpuVals.length - 1));
    const y = val => height - pad - (Math.min(100, Math.max(0, val)) / 100) * (height - pad * 2);

    const cpuPoints = cpuVals.map((v, i) => `${x(i)},${y(v)}`).join(' ');
    const memPoints = memVals.map((v, i) => `${x(i)},${y(v)}`).join(' ');

    svg.innerHTML = `
      <polyline points="${cpuPoints}" fill="none" stroke="#7865e8" stroke-width="2.5" stroke-linecap="round"/>
      <polyline points="${memPoints}" fill="none" stroke="#5d8de9" stroke-width="2.5" stroke-linecap="round"/>
      ${cpuVals.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="#7865e8"><title>CPU: ${v.toFixed(1)}%</title></circle>`).join('')}
      ${memVals.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="#5d8de9"><title>Memory: ${v.toFixed(1)}%</title></circle>`).join('')}
    `;
  }

  async function loadProcesses() {
    try {
      const payload = await api('/api/processes');
      const tbody = $('#processTableBody');
      if (!tbody) return;
      tbody.innerHTML = (payload.processes || []).map((proc, index) => `
        <tr>
          <td><span class="process-name"><i class="process-dot ${index % 2 ? 'blue-dot-bg' : ''}"></i>${escapeHTML(proc.name)}</span></td>
          <td>${proc.pid}</td>
          <td>${Number(proc.cpu_percent || 0).toFixed(1)}%</td>
          <td>${Number(proc.memory_mb || 0).toFixed(1)} MB</td>
          <td><span class="tag tag-demo">LIVE</span></td>
        </tr>
      `).join('');
    } catch (err) {
      console.warn('Error loading processes:', err);
    }
  }

  async function loadVMStatus() {
    try {
      const payload = await api('/api/vm');
      currentVM = payload.vm || {};
      if ($('#vmCurrentState')) $('#vmCurrentState').textContent = `${currentVM.vm_name || 'demo-vm'} (${currentVM.status || 'running'})`;
      if ($('#vmCurrentDetails')) $('#vmCurrentDetails').textContent = `${currentVM.vcpus || 2} vCPUs · ${Number(currentVM.memory_mb || 2048).toLocaleString()} MB RAM · Backend: ${currentVM.backend}`;
      if ($('#vmModeBadge')) {
        $('#vmModeBadge').textContent = currentVM.dry_run ? 'DRY-RUN (SIMULATION)' : `${String(currentVM.backend).toUpperCase()} HYPERVISOR`;
        $('#vmModeBadge').className = currentVM.dry_run ? 'tag tag-warn' : 'tag tag-demo';
      }
      if ($('#vmEnvLabel')) $('#vmEnvLabel').textContent = currentVM.dry_run ? 'Simulation mode (Dry-run)' : `Connected to ${currentVM.backend.toUpperCase()}`;
      if ($('#vmEnvTag')) $('#vmEnvTag').textContent = currentVM.dry_run ? 'SIMULATED' : 'LIVE HYPERVISOR';
      if ($('#vmVcpus') && currentVM.vcpus) $('#vmVcpus').value = currentVM.vcpus;
      if ($('#vmMemory') && currentVM.memory_mb) $('#vmMemory').value = currentVM.memory_mb;
      loadVMHistory();
    } catch (err) {
      console.warn('Error loading VM status:', err);
    }
  }

  async function loadVMHistory() {
    try {
      const payload = await api('/api/vm/history');
      const tbody = $('#vmHistoryTableBody');
      if (!tbody) return;
      const history = payload.history || [];
      if (!history.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#858ba0">No configuration changes recorded yet.</td></tr>';
        return;
      }
      tbody.innerHTML = history.slice().reverse().map(h => `
        <tr>
          <td>${escapeHTML(h.timestamp ? new Date(h.timestamp).toLocaleTimeString() : '—')}</td>
          <td><span class="type-label">${escapeHTML(h.action || 'configure')}</span></td>
          <td>${h.old_vcpus ?? '—'}</td>
          <td><strong>${h.new_vcpus ?? '—'}</strong></td>
          <td>${h.old_memory_mb ? h.old_memory_mb + ' MB' : '—'}</td>
          <td><strong>${h.new_memory_mb ? h.new_memory_mb + ' MB' : '—'}</strong></td>
          <td><span class="tag ${h.dry_run ? 'tag-warn' : 'tag-demo'}">${h.dry_run ? 'SIMULATION' : 'REAL HYPERVISOR'}</span></td>
          <td><span class="status-label">${h.verified ? 'Verified' : 'Pending'}</span></td>
        </tr>
      `).join('');
    } catch (err) {
      console.warn('Error loading VM history:', err);
    }
  }

  function initVMControls() {
    $('#vmConfigure')?.addEventListener('click', async () => {
      const backend = $('#vmBackend').value;
      const vm_name = $('#vmName').value.trim();
      const host = $('#vmHost').value.trim();
      const config_path = $('#vmConfigPath').value.trim();
      if (backend !== 'dryrun' && !confirm(`Connect to REAL hypervisor '${backend}'? Confirm before changing.`)) return;
      try {
        const res = await api('/api/vm/configure', {
          method: 'POST',
          body: JSON.stringify({ backend, vm_name, host, config_path, confirm_real_backend: backend !== 'dryrun' })
        });
        toast(res.message);
        loadVMStatus();
      } catch (e) { toast(e.message); }
    });

    $('#vmVerifyBtn')?.addEventListener('click', async () => {
      const noteEl = $('#vmVerificationNote');
      if (noteEl) noteEl.textContent = 'Verifying with hypervisor…';
      try {
        const res = await api('/api/vm/verify');
        const v = res.verification || {};
        if (noteEl) {
          noteEl.innerHTML = `<span style="color:${v.verified ? '#27997d' : '#cf1322'};font-weight:700">● ${escapeHTML(v.note || (v.verified ? 'Verified' : 'Verification failed'))}</span>`;
        }
        toast(v.note || 'Verification complete.');
        loadVMStatus();
      } catch (e) {
        if (noteEl) noteEl.innerHTML = `<span style="color:#cf1322">Verification error: ${escapeHTML(e.message)}</span>`;
        toast(`Verification error: ${e.message}`);
      }
    });

    const runAction = async (action, extra = {}) => {
      if (currentVM && !currentVM.dry_run && !confirm(`Execute '${action}' on hypervisor?`)) return;
      try {
        const res = await api('/api/vm/action', {
          method: 'POST',
          body: JSON.stringify({ action, confirmed: true, force_confirmed: action === 'force_stop', ...extra })
        });
        toast(res.message);
        loadVMStatus();
      } catch (e) { toast(e.message); }
    };

    $('#vmStart')?.addEventListener('click', () => runAction('start'));
    $('#vmShutdown')?.addEventListener('click', () => runAction('shutdown'));
    $('#vmForceStop')?.addEventListener('click', () => runAction('force_stop'));
    $('#vmRestore')?.addEventListener('click', () => runAction('restore'));
    $('#vmApplyResources')?.addEventListener('click', async () => {
      const vcpus = parseInt($('#vmVcpus').value) || 2;
      const memory_mb = parseInt($('#vmMemory').value) || 2048;
      if (currentVM && !currentVM.dry_run) {
        if (!confirm(`Apply configuration: ${vcpus} vCPUs and ${memory_mb} MB RAM to VM '${currentVM.vm_name}'? Confirm before executing.`)) return;
      }
      try {
        const res = await api('/api/vm/apply_resources', {
          method: 'POST',
          body: JSON.stringify({ vcpus, memory_mb, confirmed: true })
        });
        toast(res.message);
        const noteEl = $('#vmVerificationNote');
        if (noteEl && res.verification) {
          noteEl.innerHTML = `<span style="color:${res.verification.verified ? '#27997d' : '#cf1322'};font-weight:700">● ${escapeHTML(res.verification.note)}</span>`;
        }
        loadVMStatus();
      } catch (e) { toast(e.message); }
    });
  }

  // ---------------------------------------------------------------------------
  // Bottleneck Analysis
  // ---------------------------------------------------------------------------

  async function loadAnalysis() {
    try {
      const payload = await api('/api/analysis');
      latestExperimentId = payload.experiment_id || null;

      const cls = payload.classification || {};
      const firstRec = (payload.recommendations || [])[0] || {};

      if ($('#analysisPrimaryClassTitle')) $('#analysisPrimaryClassTitle').textContent = cls.classification || 'No query analysed yet';
      if ($('#analysisPrimaryClassDesc')) {
        $('#analysisPrimaryClassDesc').textContent = cls.classification
          ? (firstRec.title ? `Top suggested action: ${firstRec.title}` : 'Rule-based diagnosis from the latest query execution.')
          : 'Run a query in the SQL Query Lab to generate a bottleneck diagnosis.';
      }

      // Findings list
      const findingsList = $('#findingsList');
      if (findingsList) {
        const findings = payload.findings || [];
        if (!findings.length) {
          findingsList.innerHTML = '<div class="schema-tip"><strong>No findings recorded yet.</strong><p>Run a query in the SQL Query Lab to generate automated diagnosis.</p></div>';
        } else {
          findingsList.innerHTML = findings.map(f => `
            <div class="finding">
              <div class="finding-top">
                <span class="severity ${f.priority === 'high' ? 'warn' : 'low'}">${escapeHTML((f.priority || 'INFO').toUpperCase())}</span>
                <strong>${escapeHTML(f.title)}</strong>
              </div>
              <p>${escapeHTML(f.description)}</p>
              <div class="finding-evidence">Evidence: ${escapeHTML(f.evidence)}</div>
              ${f.actions && f.actions.length ? `
                <div class="recommendation-actions">
                  ${f.actions.map(act => `
                    <div class="recommendation-action">
                      <strong>${escapeHTML(act.action || act.title || 'Action')}</strong>
                      <small>${escapeHTML(act.reason || act.impact || '')}</small>
                      ${act.changes ? `<button class="action-btn primary btn-apply-rec" data-exp="${payload.experiment_id}" data-id="${act.id || 'act_1'}">Apply &amp; Benchmark</button>` : ''}
                    </div>
                  `).join('')}
                </div>
              ` : ''}
            </div>
          `).join('');

          $$('.btn-apply-rec', findingsList).forEach(btn => {
            btn.addEventListener('click', async () => {
              btn.disabled = true;
              btn.textContent = 'Benchmarking…';
              try {
                const res = await api('/api/recommendations/apply', {
                  method: 'POST',
                  body: JSON.stringify({ experiment_id: btn.dataset.exp, action_id: btn.dataset.id })
                });
                showModal('Benchmark Result', `${res.message}\nResults Match: ${res.result?.comparison?.results_match ? 'Yes (Verified)' : 'Unverified'}`);
                loadOverviewData();
                loadAnalysis();
              } catch (e) {
                toast(`Error: ${e.message}`);
              } finally {
                btn.disabled = false;
                btn.textContent = 'Apply & Benchmark';
              }
            });
          });
        }
      }
    } catch (err) {
      console.warn('Error loading analysis:', err);
    }
  }

  function initAnalysisControls() {
    $('#analyzeBtn')?.addEventListener('click', () => {
      loadAnalysis();
      toast('Analysis refreshed.');
    });
  }

  // ---------------------------------------------------------------------------
  // Performance Experiments & Benchmarking
  // ---------------------------------------------------------------------------

  async function loadHistory() {
    try {
      const payload = await api('/api/history');
      const tbody = $('#experimentsBody');
      const countEl = $('#experimentCount');
      if (countEl) countEl.textContent = String(payload.total || 0);

      const items = payload.history || [];
      if ($('#bestLatency')) {
        const minLat = items.length ? Math.min(...items.map(x => Number(x.latency_ms || 9999))) : null;
        $('#bestLatency').textContent = minLat !== null ? `${minLat.toFixed(1)} ms` : '—';
      }

      if (tbody) {
        if (!items.length) {
          tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#858ba0">No experiments saved yet.</td></tr>';
        } else {
          tbody.innerHTML = items.map(q => `
            <tr>
              <td><strong>${escapeHTML(q.id)}</strong></td>
              <td><code title="${escapeHTML(q.sql)}">${escapeHTML(q.sql.length > 40 ? q.sql.slice(0, 40) + '…' : q.sql)}</code></td>
              <td>Threads: ${q.settings?.thread_count || 1} ${q.index_used ? `· ${q.index_used}` : ''}</td>
              <td><strong>${Number(q.latency_ms || 0).toFixed(2)} ms</strong></td>
              <td><span class="type-label">${escapeHTML(q.classification || 'Complete')}</span></td>
              <td><span class="status-label">${q.result_fingerprint ? 'Verified' : 'Ready'}</span></td>
            </tr>
          `).join('');
        }
      }
      populateCompareDropdowns();
    } catch (err) {
      console.warn('Error loading history:', err);
    }
  }

  async function populateCompareDropdowns() {
    try {
      const payload = await api('/api/history');
      const beforeSel = $('#compareBefore');
      const afterSel = $('#compareAfter');
      const items = payload.history || [];
      if (!beforeSel || !afterSel) return;
      if (!items.length) {
        beforeSel.innerHTML = '<option value="">No experiments saved yet</option>';
        afterSel.innerHTML = '<option value="">No experiments saved yet</option>';
        return;
      }
      const prevBefore = beforeSel.value;
      const prevAfter = afterSel.value;
      const options = items.map(q => `<option value="${escapeHTML(q.id)}">${escapeHTML(q.id)} — ${Number(q.latency_ms || 0).toFixed(1)} ms (${escapeHTML((q.sql || '').slice(0, 32))}…)</option>`).join('');
      beforeSel.innerHTML = options;
      afterSel.innerHTML = options;

      if (prevBefore && items.some(q => q.id === prevBefore)) {
        beforeSel.value = prevBefore;
      } else {
        beforeSel.selectedIndex = 0;
      }

      if (prevAfter && items.some(q => q.id === prevAfter)) {
        afterSel.value = prevAfter;
      } else if (items.length > 1) {
        afterSel.selectedIndex = 1;
      } else {
        afterSel.selectedIndex = 0;
      }
    } catch (err) {
      console.warn('Error populating compare dropdowns:', err);
    }
  }


  // ---------------------------------------------------------------------------
  // Compare two saved experiments (robust, delegated, with inline feedback)
  // ---------------------------------------------------------------------------

  function showCompareMessage(kind, title, message) {
    const target = $('#experimentComparisonResult');
    if (!target) { toast(message); return; }
    const color = kind === 'error' ? '#c0392b' : (kind === 'loading' ? '#5c50a6' : '#5a6177');
    target.innerHTML = `<div class="result-empty"><strong style="color:${color}">${escapeHTML(title)}</strong><p>${escapeHTML(message)}</p></div>`;
  }

  let compareInFlight = false;

  async function runCompareExperiments() {
    if (compareInFlight) return;
    const beforeSel = $('#compareBefore');
    const afterSel = $('#compareAfter');
    const btn = $('#compareExperimentsBtn');

    // Dropdowns may be empty if history was never loaded; try once more.
    if (!beforeSel?.value || !afterSel?.value) {
      try { await populateCompareDropdowns(); } catch (err) { console.warn('populateCompareDropdowns failed', err); }
    }
    const before_id = (beforeSel?.value || '').trim();
    const after_id = (afterSel?.value || '').trim();

    if (!before_id || !after_id) {
      showCompareMessage('info', 'Nothing to compare', 'Please select two saved experiments from the dropdowns first.');
      toast('Please select two experiments first.');
      return;
    }
    if (before_id === after_id) {
      showCompareMessage('info', 'Pick two different experiments', 'Baseline and Candidate are the same experiment (' + before_id + '). Choose two distinct experiment IDs.');
      toast('Select two distinct experiment IDs to compare.');
      return;
    }

    compareInFlight = true;
    if (btn) { btn.disabled = true; btn.textContent = 'Comparing…'; }
    showCompareMessage('loading', 'Comparing…', `Comparing ${before_id} with ${after_id}`);

    try {
      const payload = await api('/api/experiments/compare', {
        method: 'POST',
        body: JSON.stringify({ before_id, after_id })
      });
      if (!payload || !payload.comparison) {
        throw new Error('Server returned no comparison data.');
      }
      renderComparisonResult(payload);
      toast(`Compared ${before_id} vs ${after_id}`);
    } catch (e) {
      console.error('Compare experiments failed:', e);
      showCompareMessage('error', 'Comparison failed', e && e.message ? e.message : String(e));
      toast(`Comparison error: ${e && e.message ? e.message : e}`);
    } finally {
      compareInFlight = false;
      if (btn) { btn.disabled = false; btn.textContent = 'Compare experiments'; }
    }
  }

  function initCompareDelegation() {
    // Delegation on document: works even if the button is re-rendered and
    // even if another init step failed earlier.
    document.addEventListener('click', (event) => {
      const btn = event.target && event.target.closest ? event.target.closest('#compareExperimentsBtn') : null;
      if (!btn) return;
      event.preventDefault();
      runCompareExperiments();
    });
  }
  initCompareDelegation();

  function initExperimentControls() {
    // Compare handler is registered via event delegation in initCompareDelegation()

    $('#btnRunIndexBenchmark')?.addEventListener('click', async () => {
      const table = $('#benchmarkTableSelect').value;
      const column = $('#benchmarkColumnInput').value.trim();
      const index_type = $('#benchmarkTypeSelect').value;
      const sql = $('#benchmarkSqlInput').value.trim();
      const area = $('#indexBenchmarkResultArea');
      const btn = $('#btnRunIndexBenchmark');

      if (!column || !sql) { toast('Column and SQL query required.'); return; }
      if (btn) { btn.disabled = true; btn.textContent = 'Benchmarking…'; }

      try {
        const payload = await api('/api/experiments/compare_index', {
          method: 'POST',
          body: JSON.stringify({ table, column, index_type, sql })
        });
        const c = payload.comparison;
        if (area) {
          area.innerHTML = `
            <div style="padding:14px;background:#fbfaff;border:1px solid #e7e2fa;border-radius:10px;margin-top:10px">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <strong style="font-size:14px;color:#3c336b">A/B Index Benchmark Result</strong>
                <span class="tag tag-demo">${c.results_match ? 'RESULTS EQUIVALENT (VERIFIED)' : 'WARNING: RESULTS DIFFER'}</span>
              </div>
              <p style="font-size:11px;color:#5a6177;margin:0 0 10px">${escapeHTML(c.conclusion)}</p>
              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">
                <div style="background:#fff;padding:8px;border-radius:6px;border:1px solid #eee">
                  <small style="color:#858ba0">BASELINE (NO INDEX)</small>
                  <strong style="display:block;font-size:13px">${c.before_latency_ms.toFixed(2)} ms</strong>
                </div>
                <div style="background:#fff;padding:8px;border-radius:6px;border:1px solid #eee">
                  <small style="color:#858ba0">WITH ${escapeHTML(c.index_type)} INDEX</small>
                  <strong style="display:block;font-size:13px;color:#27997d">${c.after_latency_ms.toFixed(2)} ms</strong>
                </div>
                <div style="background:#fff;padding:8px;border-radius:6px;border:1px solid #eee">
                  <small style="color:#858ba0">LATENCY CHANGE</small>
                  <strong style="display:block;font-size:13px;color:${c.diff_pct < 0 ? '#27997d' : '#d46b08'}">${c.diff_pct > 0 ? '+' : ''}${c.diff_pct}%</strong>
                </div>
                <div style="background:#fff;padding:8px;border-radius:6px;border:1px solid #eee">
                  <small style="color:#858ba0">SPEEDUP FACTOR</small>
                  <strong style="display:block;font-size:13px">${c.speedup_factor}x</strong>
                </div>
              </div>
            </div>
          `;
        }
        toast('Index benchmark complete.');
        loadIndexes();
        loadHistory();
      } catch (e) {
        toast(`Benchmark error: ${e.message}`);
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '⚡ Benchmark Index'; }
      }
    });

    $('#runWorkloadBtn')?.addEventListener('click', async () => {
      const btn = $('#runWorkloadBtn');
      if (btn) { btn.disabled = true; btn.textContent = 'Running workload…'; }
      try {
        const payload = await api('/api/experiments/workload', {
          method: 'POST',
          body: JSON.stringify({ iterations: 2 })
        });
        const w = payload.workload;
        showModal('Workload Completed', `Executed ${w.queries_executed} queries in ${w.wall_time_s}s.\nAverage query latency: ${w.average_latency_ms} ms.\nAll experiments logged.`);
        loadOverviewData();
        loadHistory();
      } catch (e) {
        toast(`Workload error: ${e.message}`);
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '▶ Run Test Workload'; }
      }
    });

    $('#exportExperiments')?.addEventListener('click', () => {
      window.location.href = '/api/history/export';
    });
    $('#exportOverview')?.addEventListener('click', () => {
      window.location.href = '/api/history/export';
    });
    $('#exportResults')?.addEventListener('click', () => {
      window.location.href = '/api/history/export';
    });
  }

  let currentEditExpId = null;
  let currentEditActionId = null;

  function formatMetricVal(val, unit) {
    if (val === null || val === undefined) return '<em style="color:#a0a5b8">N/A</em>';
    if (typeof val === 'object') return escapeHTML(JSON.stringify(val));
    if (typeof val === 'number') {
      if (!Number.isFinite(val)) return '<em style="color:#a0a5b8">N/A</em>';
      if (unit === 'ms') return val.toFixed(2) + ' ms';
      if (unit === 'MB') return val.toFixed(2) + ' MB';
      if (unit === '%') return val.toFixed(1) + '%';
      if (unit === 'rows') return val.toLocaleString() + ' rows';
      if (unit === 'threads') return val + ' threads';
      if (unit === 'vCPUs') return val + ' vCPUs';
      return val.toLocaleString() + (unit ? ' ' + unit : '');
    }
    return String(val) + (unit ? ' ' + unit : '');
  }

  function formatChange(changeDisplay, changePct, verdict) {
    if (changeDisplay && changeDisplay !== 'N/A') {
      const cls = verdict === 'better' ? 'pct-better' : (verdict === 'worse' ? 'pct-worse' : 'pct-same');
      return `<span class="${cls}">${escapeHTML(changeDisplay)}</span>`;
    }
    if (changePct !== null && changePct !== undefined) {
      const cls = verdict === 'better' ? 'pct-better' : (verdict === 'worse' ? 'pct-worse' : 'pct-same');
      const sign = changePct > 0 ? '+' : '';
      return `<span class="${cls}">${sign}${Number(changePct).toFixed(1)}%</span>`;
    }
    return '<span class="pct-same">N/A</span>';
  }

  function formatResultBadge(verdict, resultLabel) {
    const label = resultLabel || (verdict === 'better' ? 'Better ✔' : (verdict === 'worse' ? 'Worse ✘' : (verdict === 'same' ? 'Same ≈' : 'Informational')));
    const cls = verdict === 'better' ? 'res-better' : (verdict === 'worse' ? 'res-worse' : (verdict === 'same' ? 'res-same' : 'res-info'));
    return `<span class="res-badge ${cls}">${escapeHTML(label)}</span>`;
  }

  function renderComparisonTable(comparison, targetSelector = '#queryComparisonPanel') {
    const panel = $(targetSelector);
    if (!panel || !comparison) return;
    panel.classList.remove('hidden');

    const titleEl = $('#compTitle', panel) || $('h3', panel);
    if (titleEl) {
      titleEl.textContent = `Performance Comparison: ${comparison.before_id || 'Baseline'} vs ${comparison.after_id || 'Optimized'}`;
    }

    const badgeEl = $('#compEquivalenceBadge', panel) || $('.tag', panel);
    if (badgeEl) {
      if (comparison.results_match === true) {
        badgeEl.textContent = 'RESULTS EQUIVALENT (VERIFIED)';
        badgeEl.className = 'tag tag-demo';
      } else if (comparison.results_match === false) {
        if (comparison.same_query === false) {
          badgeEl.textContent = 'RESULTS DIFFER (FILTER INTENTIONALLY NARROWED)';
          badgeEl.className = 'tag tag-warn';
        } else {
          badgeEl.textContent = 'WARNING: RESULTS DIFFER';
          badgeEl.className = 'tag tag-warn';
        }
      } else {
        badgeEl.textContent = 'STATUS: READY';
        badgeEl.className = 'tag tag-muted';
      }
    }

    const summaryEl = $('#compSummaryBanner', panel);
    if (summaryEl) {
      summaryEl.innerHTML = `<strong>Optimization Result:</strong> ${escapeHTML(comparison.improvement_summary || 'Benchmark completed.')} <span style="color:#8b91a6;font-size:10px">(Noise band: ±${escapeHTML(comparison.noise_pct || 5)}%)</span>`;
    }

    const tbody = $('#compTableBody', panel) || $('tbody', panel);
    if (tbody && comparison.metrics) {
      tbody.innerHTML = comparison.metrics.map(m => `
        <tr>
          <td>${escapeHTML(m.label || m.key)}${m.unit ? ` <small style="color:#8f95aa">(${m.unit})</small>` : ''}</td>
          <td>${formatMetricVal(m.before, m.unit)}</td>
          <td>${formatMetricVal(m.after, m.unit)}</td>
          <td>${formatChange(m.change_display, m.change_pct, m.verdict)}</td>
          <td>${formatResultBadge(m.verdict, m.result)}</td>
        </tr>
      `).join('');
    }

    const metaGrid = $('#compMetaGrid', panel);
    if (metaGrid) {
      const appliedStr = Array.isArray(comparison.applied_actions) && comparison.applied_actions.length ? comparison.applied_actions.join(', ') : 'Tuned execution settings';
      metaGrid.innerHTML = `
        <div class="comparison-meta-card">
          <strong>BASELINE SQL</strong>
          <code>${escapeHTML(comparison.before_sql || '—')}</code>
        </div>
        <div class="comparison-meta-card">
          <strong>OPTIMIZED SQL</strong>
          <code>${escapeHTML(comparison.after_sql || comparison.before_sql || '—')}</code>
        </div>
        <div class="comparison-meta-card">
          <strong>APPLIED OPTIMIZATIONS</strong>
          <span style="color:#5c50a6;font-weight:700">${escapeHTML(appliedStr)}</span>
        </div>
        <div class="comparison-meta-card">
          <strong>RESULT VERIFICATION</strong>
          <span>${comparison.results_match ? 'Fingerprints matched exactly.' : (comparison.same_query === false ? 'Expected difference: selective filter reduced row count.' : 'Row hashes differ.')}</span>
        </div>
      `;
    }

    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function renderRecommendations(result) {
    const panel = $('#queryRecommendationsPanel');
    const cardsList = $('#queryRecCardsList');
    const countBadge = $('#recCountBadge');
    const applyAllBtn = $('#btnApplyAllRecs');
    if (!panel || !cardsList) return;

    const recs = result.recommendations || [];
    if (!recs.length) {
      panel.classList.add('hidden');
      return;
    }

    panel.classList.remove('hidden');
    if (countBadge) countBadge.textContent = `${recs.length} RECOMMENDATION${recs.length > 1 ? 'S' : ''}`;

    const compatibleSettings = recs.filter(r => r.kind === 'setting' && r.can_apply_all !== false);
    if (applyAllBtn) {
      applyAllBtn.style.display = compatibleSettings.length > 0 ? 'inline-flex' : 'none';
      applyAllBtn.onclick = () => applyAllRecommendations(result.experiment_id, applyAllBtn);
    }

    cardsList.innerHTML = recs.map((rec, i) => {
      const badges = (rec.impact_badges || []).map(b => {
        const cls = b.includes('RAM') ? 'ram' : (b.includes('CPU') ? 'cpu' : 'latency');
        return `<span class="impact-badge ${cls}">${escapeHTML(b)}</span>`;
      }).join(' ');

      let actionBtn = '';
      if (rec.kind === 'setting') {
        actionBtn = `<button class="btn-rec-apply btn-apply-single" data-exp="${result.experiment_id}" data-id="${rec.id}">⚡ Apply &amp; Benchmark</button>`;
      } else if (rec.kind === 'manual' && rec.proposed_sql) {
        actionBtn = `<button class="btn-rec-edit-sql btn-open-edit-sql" data-exp="${result.experiment_id}" data-id="${rec.id}" data-orig="${escapeHTML(rec.current_sql || result.sql)}" data-prop="${escapeHTML(rec.proposed_sql)}">✏ Review &amp; Edit SQL ↗</button>`;
      } else if (rec.kind === 'vm') {
        actionBtn = `<button class="btn-rec-apply btn-apply-vm" data-exp="${result.experiment_id}" data-id="${rec.id}">⚙ Apply VM Tuning</button>`;
      }

      return `
        <div class="rec-card">
          <div class="rec-card-top">
            <div class="rec-card-title-group">
              <div class="rec-card-title">[${i + 1}] ${escapeHTML(rec.title)}</div>
              <div class="rec-impact-badges">${badges}</div>
            </div>
            <div class="rec-card-actions">${actionBtn}</div>
          </div>
          ${rec.observation ? `<div class="rec-card-observation">ⓘ Observation: ${escapeHTML(rec.observation)}</div>` : ''}
          <div class="rec-card-reason">${escapeHTML(rec.reason || rec.detail)}</div>
        </div>
      `;
    }).join('');

    $$('.btn-apply-single', cardsList).forEach(btn => {
      btn.addEventListener('click', () => applySingleRecommendation(btn.dataset.exp, btn.dataset.id, btn));
    });

    $$('.btn-apply-vm', cardsList).forEach(btn => {
      btn.addEventListener('click', () => applySingleRecommendation(btn.dataset.exp, btn.dataset.id, btn));
    });

    $$('.btn-open-edit-sql', cardsList).forEach(btn => {
      btn.addEventListener('click', () => {
        openSqlEditModal(btn.dataset.exp, btn.dataset.id, btn.dataset.orig, btn.dataset.prop);
      });
    });
  }

  async function applySingleRecommendation(expId, actionId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Benchmarking…'; }
    try {
      const payload = await api('/api/recommendations/apply', {
        method: 'POST',
        body: JSON.stringify({ experiment_id: expId, action_id: actionId })
      });
      toast(payload.message);
      if (payload.comparison || payload.result?.comparison) {
        renderComparisonTable(payload.comparison || payload.result?.comparison, '#queryComparisonPanel');
      }
      loadOverviewData();
      loadHistory();
    } catch (err) {
      toast(`Error: ${err.message}`);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '⚡ Apply & Benchmark'; }
    }
  }

  async function applyAllRecommendations(expId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Applying all…'; }
    try {
      const payload = await api('/api/recommendations/apply', {
        method: 'POST',
        body: JSON.stringify({ experiment_id: expId, apply_all: true })
      });
      toast(payload.message);
      if (payload.comparison || payload.result?.comparison) {
        renderComparisonTable(payload.comparison || payload.result?.comparison, '#queryComparisonPanel');
      }
      loadOverviewData();
      loadHistory();
    } catch (err) {
      toast(`Error: ${err.message}`);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '⚡ Apply All Compatible'; }
    }
  }

  function openSqlEditModal(expId, actionId, origSql, propSql) {
    currentEditExpId = expId;
    currentEditActionId = actionId;
    const modal = $('#sqlEditModalBackdrop');
    if (!modal) return;
    if ($('#modalOriginalSql')) $('#modalOriginalSql').textContent = origSql;
    if ($('#modalProposedSql')) $('#modalProposedSql').value = propSql;
    modal.classList.remove('hidden');
  }

  function initSqlEditModal() {
    $('#sqlEditModalClose')?.addEventListener('click', () => {
      $('#sqlEditModalBackdrop')?.classList.add('hidden');
    });
    $('#modalCancelSqlEdit')?.addEventListener('click', () => {
      $('#sqlEditModalBackdrop')?.classList.add('hidden');
    });
    $('#modalExecuteSqlEdit')?.addEventListener('click', async () => {
      const btn = $('#modalExecuteSqlEdit');
      const modifiedSql = ($('#modalProposedSql')?.value || '').trim();
      if (!modifiedSql) { toast('Enter a valid SQL query.'); return; }
      if (btn) { btn.disabled = true; btn.textContent = 'Benchmarking…'; }

      try {
        const payload = await api('/api/recommendations/apply_sql', {
          method: 'POST',
          body: JSON.stringify({
            experiment_id: currentEditExpId,
            action_id: currentEditActionId || 'narrow_where',
            modified_sql: modifiedSql
          })
        });
        $('#sqlEditModalBackdrop')?.classList.add('hidden');
        toast(payload.message);
        if (payload.comparison || payload.result?.comparison) {
          renderComparisonTable(payload.comparison || payload.result?.comparison, '#queryComparisonPanel');
        }
        loadOverviewData();
        loadHistory();
      } catch (err) {
        toast(`Error: ${err.message}`);
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '▶ Execute Edited SQL & Benchmark'; }
      }
    });
  }

  function renderComparisonResult(data) {
    const comparison = data && data.comparison;
    const target = $('#experimentComparisonResult');
    if (!target) return;
    if (!comparison) { showCompareMessage('error', 'Comparison failed', 'No comparison data returned.'); return; }

    let badgeClass = 'tag-demo';
    let badgeText = 'RESULTS EQUIVALENT (VERIFIED)';
    if (comparison.results_match === false) {
      badgeClass = 'tag-warn';
      badgeText = comparison.same_query === false ? 'RESULTS DIFFER (FILTER NARROWED)' : 'WARNING: RESULTS DIFFER';
    } else if (comparison.results_match === null || comparison.results_match === undefined) {
      badgeClass = 'tag-muted';
      badgeText = 'STATUS: READY';
    }

    const appliedStr = (comparison.applied_actions || []).length ? comparison.applied_actions.join(', ') : 'Direct experiment comparison';

    const metricsRows = (comparison.metrics || []).map(m => `
      <tr>
        <td>${escapeHTML(m.label || m.key)}${m.unit ? ` <small style="color:#8f95aa">(${m.unit})</small>` : ''}</td>
        <td>${formatMetricVal(m.before, m.unit)}</td>
        <td>${formatMetricVal(m.after, m.unit)}</td>
        <td>${formatChange(m.change_display, m.change_pct, m.verdict)}</td>
        <td>${formatResultBadge(m.verdict, m.result)}</td>
      </tr>
    `).join('');

    target.innerHTML = `
      <div class="comparison-container" style="display:block;margin-top:0">
        <div class="comparison-header-row">
          <div>
            <h3>Performance Comparison: ${escapeHTML(comparison.before_id || 'Baseline')} vs ${escapeHTML(comparison.after_id || 'Candidate')}</h3>
            <p>Side-by-side benchmark verifying latency, CPU time, memory, settings, and result equivalence</p>
          </div>
          <div class="panel-actions">
            <span class="tag ${badgeClass}">${badgeText}</span>
          </div>
        </div>
        <div class="comparison-summary-banner">
          <strong>Optimization Result:</strong> ${escapeHTML(comparison.improvement_summary || 'Comparison completed.')}
          <span style="color:#8b91a6;font-size:10px">(Noise band: ±${comparison.noise_pct || 5}%)</span>
        </div>
        <div class="table-scroll">
          <table class="comparison-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Before (${escapeHTML(comparison.before_id || 'Baseline')})</th>
                <th>After (${escapeHTML(comparison.after_id || 'Candidate')})</th>
                <th>Change</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              ${metricsRows}
            </tbody>
          </table>
        </div>
        <div class="comparison-meta-grid">
          <div class="comparison-meta-card">
            <strong>BASELINE SQL (${escapeHTML(comparison.before_id || 'Before')})</strong>
            <code>${escapeHTML(comparison.before_sql || data.before?.sql || '—')}</code>
          </div>
          <div class="comparison-meta-card">
            <strong>CANDIDATE SQL (${escapeHTML(comparison.after_id || 'After')})</strong>
            <code>${escapeHTML(comparison.after_sql || data.after?.sql || comparison.before_sql || '—')}</code>
          </div>
          <div class="comparison-meta-card">
            <strong>APPLIED CONFIGURATION</strong>
            <span style="color:#5c50a6;font-weight:700">${escapeHTML(appliedStr)}</span>
          </div>
          <div class="comparison-meta-card">
            <strong>RESULT VERIFICATION</strong>
            <span>${comparison.results_match ? 'Fingerprints matched exactly (Verified).' : (comparison.same_query === false ? 'Expected difference: queries or filter conditions differ.' : 'Row hashes differ.')}</span>
          </div>
        </div>
      </div>
    `;

    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------

  async function init() {
    // Navigation handlers
    $$('.nav-item[data-page]').forEach(button => {
      button.addEventListener('click', () => goToPage(button.dataset.page));
    });
    $$('[data-goto]').forEach(btn => {
      btn.addEventListener('click', () => goToPage(btn.dataset.goto));
    });
    $('#mobileMenu')?.addEventListener('click', () => $('#sidebar')?.classList.toggle('open'));
    $('#refreshBtn')?.addEventListener('click', () => {
      loadOverviewData();
      refreshMetrics();
      loadVMStatus();
      loadVMHistory();
      toast('Refreshed data.');
    });
    $('#modalClose')?.addEventListener('click', closeModal);
    $('#modalOk')?.addEventListener('click', closeModal);

    [initQueryLab, initSqlEditModal, initIndexForm, initVMControls, initAnalysisControls, initExperimentControls].forEach(fn => {
      try { fn(); } catch (err) { console.error('Init step failed: ' + fn.name, err); }
    });

    // Check backend connection
    try {
      const health = await api('/api/health');
      if ($('#topbarStatusText')) $('#topbarStatusText').textContent = 'Backend connected';
      if ($('#sidebarBackendTitle')) $('#sidebarBackendTitle').textContent = 'HyperVision Live API';
      if ($('#sidebarBackendDesc')) $('#sidebarBackendDesc').textContent = `${health.tables.length} tables · Ready`;
    } catch (e) {
      if ($('#topbarStatusText')) $('#topbarStatusText').textContent = 'Backend offline';
    }

    // Initial data load
    try { await loadOverviewData(); } catch (err) { console.warn('loadOverviewData failed', err); }
    try { await loadTables(); } catch (err) { console.warn('loadTables failed', err); }
    try { await loadVMStatus(); } catch (err) { console.warn('loadVMStatus failed', err); }
    try { await loadVMHistory(); } catch (err) { console.warn('loadVMHistory failed', err); }
    try { await loadHistory(); } catch (err) { console.warn('loadHistory failed', err); }
    await populateCompareDropdowns();

    // Auto-refresh monitoring interval
    autoRefreshInterval = setInterval(() => {
      if ($('#autoRefreshMonitor')?.checked) {
        refreshMetrics();
        loadProcesses();
      }
    }, 3000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
