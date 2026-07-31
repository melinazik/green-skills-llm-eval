// Step 5 tab: show what the LLM judges scored. Read only.
//
// The page cannot run the judges: a static page cannot start a process. Produce the
// data with `python evaluate/judge.py`, then read it here.
(function () {
  const DEFAULT_CSV_FILE = '../evaluate/output_evaluate/llm_judgements.csv';
  const CRITERIA = ['clarity', 'depth', 'relevance', 'pedagogical'];
  // a criterion repeated on this share of the answers carries no information
  const CONSTANT_THRESHOLD = 90;

  const state = { rows: [], filtered: [] };

  const el = {
    status: document.getElementById('j_statusText'),
    judgeFilter: document.getElementById('j_judgeFilter'),
    modelFilter: document.getElementById('j_modelFilter'),
    promptFilter: document.getElementById('j_promptFilter'),
    reloadBtn: document.getElementById('j_reloadBtn'),
    summary: document.getElementById('j_summary'),
    warning: document.getElementById('j_warning'),
    tableBody: document.getElementById('j_tableBody'),
    pageInfo: document.getElementById('j_pageInfo'),
  };

  function parseCSV(text) {
    const rows = [];
    let row = [];
    let field = '';
    let inQuotes = false;

    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      if (inQuotes) {
        if (char === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; } else { inQuotes = false; }
        } else { field += char; }
        continue;
      }
      if (char === '"') inQuotes = true;
      else if (char === ',') { row.push(field); field = ''; }
      else if (char === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
      else if (char !== '\r') field += char;
    }
    if (field.length > 0 || row.length > 0) { row.push(field); rows.push(row); }
    if (rows.length === 0) return [];

    const headers = rows[0].map(h => h.trim());
    return rows.slice(1).filter(r => r.some(v => v !== '')).map(r => {
      const obj = {};
      headers.forEach((h, i) => { obj[h] = r[i] ?? ''; });
      return obj;
    });
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function setStatus(text, isError) {
    el.status.textContent = text;
    el.status.classList.toggle('error', Boolean(isError));
  }

  function overall(row) {
    return CRITERIA.reduce((sum, c) => sum + Number(row[c] || 0), 0) / CRITERIA.length;
  }

  function mean(values) {
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  }

  function uniqueSorted(values) {
    return [...new Set(values.filter(v => v !== ''))]
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  }

  function fillFilter(select, values) {
    const keep = select.value;
    select.innerHTML = '<option value="">All</option>';
    for (const v of values) {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      select.appendChild(opt);
    }
    if (values.includes(keep)) select.value = keep;
  }

  // ---------- the two things worth reading before the numbers ----------

  function renderWarning() {
    // Same check as evaluate/stats.py: a judge that repeats one value is not rating.
    const flagged = [];
    const byJudge = {};
    for (const row of state.rows) (byJudge[row.rater] = byJudge[row.rater] || []).push(row);

    for (const [judge, rows] of Object.entries(byJudge)) {
      for (const c of CRITERIA) {
        const counts = {};
        for (const r of rows) counts[r[c]] = (counts[r[c]] || 0) + 1;
        const [value, n] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
        const share = (n / rows.length) * 100;
        if (share >= CONSTANT_THRESHOLD) flagged.push({ judge, c, value, share });
      }
    }

    if (flagged.length === 0) {
      el.warning.hidden = true;
      return;
    }

    el.warning.hidden = false;
    el.warning.innerHTML =
      '<b>These criteria are effectively constant.</b> The judge gave almost every ' +
      'answer the same score, so the mean and the agreement figures below carry no ' +
      'information for them. Treat them as missing data, not as evidence.<ul>' +
      flagged.map(f =>
        `<li><code>${escapeHtml(f.judge)}</code> gave <b>${escapeHtml(f.c)} = ${escapeHtml(f.value)}</b> ` +
        `to ${f.share.toFixed(0)}% of the answers</li>`).join('') +
      '</ul>';
  }

  function renderSummary() {
    const judges = uniqueSorted(state.rows.map(r => r.rater));
    const models = uniqueSorted(state.rows.map(r => r.llm));
    if (judges.length === 0) { el.summary.innerHTML = ''; return; }

    let html = '<h3>Mean score per model, by judge</h3>';
    html += '<div class="table-wrap"><table><thead><tr><th>Judge</th>';
    for (const m of models) html += `<th>${escapeHtml(m)}</th>`;
    html += '</tr></thead><tbody>';

    for (const judge of judges) {
      html += `<tr><td class="mono">${escapeHtml(judge)}</td>`;
      for (const m of models) {
        const rows = state.rows.filter(r => r.rater === judge && r.llm === m);
        const self = judge === `llm:${m}`;
        const value = rows.length ? mean(rows.map(overall)).toFixed(2) : '&mdash;';
        html += `<td${self ? ' title="the judge grading its own answers"' : ''}>` +
          `${value}${self && rows.length ? ' <span class="subtle">(own)</span>' : ''}</td>`;
      }
      html += '</tr>';
    }
    html += '</tbody></table></div>';

    // self-preference: own score minus what the other judges gave the same answers
    const bias = [];
    for (const judge of judges) {
      const author = judge.replace(/^llm:/, '');
      const own = state.rows.filter(r => r.rater === judge && r.llm === author);
      if (own.length === 0) continue;

      const keyOf = r => `${r.conceptUri}||${r.prompt_number}`;
      const ownKeys = new Set(own.map(keyOf));
      const others = state.rows.filter(r =>
        r.rater !== judge && r.llm === author && ownKeys.has(keyOf(r)));
      if (others.length === 0) continue;

      bias.push({ judge, own: mean(own.map(overall)), others: mean(others.map(overall)), n: own.length });
    }

    if (bias.length) {
      html += '<h3>Self-preference</h3><p class="subtle">Each judge on its own answers, ' +
        'against what the other judges gave the same answers.</p><ul>';
      for (const b of bias) {
        const gap = b.own - b.others;
        html += `<li><code>${escapeHtml(b.judge)}</code>: ${b.own.toFixed(2)} vs ` +
          `${b.others.toFixed(2)} from the others &rarr; <b>${gap >= 0 ? '+' : ''}${gap.toFixed(2)}</b> ` +
          `<span class="subtle">(n=${b.n})</span></li>`;
      }
      html += '</ul>';
    }

    el.summary.innerHTML = html;
  }

  function renderTable() {
    const judge = el.judgeFilter.value;
    const model = el.modelFilter.value;
    const prompt = el.promptFilter.value;

    state.filtered = state.rows.filter(r =>
      (!judge || r.rater === judge) &&
      (!model || r.llm === model) &&
      (!prompt || r.prompt_number === prompt));

    el.tableBody.innerHTML = state.filtered.map(r => `
      <tr>
        <td class="ellipsis" title="${escapeHtml(r.preferredLabel)}">${escapeHtml(r.preferredLabel)}</td>
        <td class="mono">P${escapeHtml(r.prompt_number)} ${escapeHtml(r.prompt_category)}</td>
        <td class="mono">${escapeHtml(r.llm)}</td>
        <td class="mono">${escapeHtml(r.rater)}${r.self_judged === 'yes' ? ' <span class="subtle">(own)</span>' : ''}</td>
        ${CRITERIA.map(c => `<td>${escapeHtml(r[c])}</td>`).join('')}
        <td>${overall(r).toFixed(2)}</td>
        <td class="ellipsis" title="${escapeHtml(r.rationale)}">${escapeHtml(r.rationale)}</td>
      </tr>`).join('');

    el.pageInfo.textContent =
      `${state.filtered.length} of ${state.rows.length} judgements shown`;
  }

  function renderAll() {
    renderWarning();
    renderSummary();
    renderTable();
  }

  async function load() {
    setStatus(`Loading ${DEFAULT_CSV_FILE}...`);
    try {
      const response = await fetch(DEFAULT_CSV_FILE, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      state.rows = parseCSV(await response.text());
      if (state.rows.length === 0) {
        setStatus('The judgements file is empty.', true);
        return;
      }

      fillFilter(el.judgeFilter, uniqueSorted(state.rows.map(r => r.rater)));
      fillFilter(el.modelFilter, uniqueSorted(state.rows.map(r => r.llm)));
      fillFilter(el.promptFilter, uniqueSorted(state.rows.map(r => r.prompt_number)));

      const judges = new Set(state.rows.map(r => r.rater)).size;
      setStatus(`${state.rows.length} judgements from ${judges} judge(s).`);
      renderAll();
    } catch (err) {
      setStatus(`No judgements yet (${err.message}). Run: python evaluate/judge.py`, true);
      el.summary.innerHTML = '';
      el.warning.hidden = true;
      el.tableBody.innerHTML = '';
      el.pageInfo.textContent = '';
    }
  }

  el.judgeFilter.addEventListener('change', renderTable);
  el.modelFilter.addEventListener('change', renderTable);
  el.promptFilter.addEventListener('change', renderTable);
  el.reloadBtn.addEventListener('click', load);

  load();
})();
