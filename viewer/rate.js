// Step 5 tab: score the collected answers one at a time.
//
// Writes the same columns as evaluate/rate.py, so evaluate/stats.py reads either.
// Scores are kept in localStorage while you work and are only written to disk when
// you press Export ratings, because a browser page cannot save files on its own.
(function () {
  const DEFAULT_CSV_FILE = '../prompt/output_prompt/responses.csv';
  const CRITERIA = [
    { key: 'clarity', label: 'Clarity', hint: 'understandable, well structured, accessible' },
    { key: 'depth', label: 'Depth', hint: 'real context and sustainability reasoning' },
    { key: 'relevance', label: 'Relevance', hint: 'matches the intended ESCO skill' },
    { key: 'pedagogical', label: 'Pedagogical value', hint: 'steps, examples, guides the learner' },
  ];
  const STORAGE_KEY = 'greenSkillsRatings.v1';

  const state = {
    rows: [],          // every answer worth rating
    queue: [],         // the answers still to rate, in order
    index: 0,          // where we are in the queue
    ratings: {},       // "rater||conceptUri||prompt||llm" -> record
    rater: '',
    draft: {},         // the scores of the answer on screen
  };

  const el = {
    rater: document.getElementById('v_rater'),
    status: document.getElementById('v_statusText'),
    progress: document.getElementById('v_progress'),
    modelFilter: document.getElementById('v_modelFilter'),
    promptFilter: document.getElementById('v_promptFilter'),
    card: document.getElementById('v_card'),
    skill: document.getElementById('v_skill'),
    meta: document.getElementById('v_meta'),
    question: document.getElementById('v_question'),
    answer: document.getElementById('v_answer'),
    criteria: document.getElementById('v_criteria'),
    saveBtn: document.getElementById('v_saveBtn'),
    skipBtn: document.getElementById('v_skipBtn'),
    backBtn: document.getElementById('v_backBtn'),
    exportBtn: document.getElementById('v_exportBtn'),
    importInput: document.getElementById('v_importInput'),
    clearBtn: document.getElementById('v_clearBtn'),
    summary: document.getElementById('v_summary'),
  };

  // ---------- CSV ----------

  function parseCSV(text) {
    const rows = [];
    let row = [];
    let field = '';
    let inQuotes = false;

    for (let i = 0; i < text.length; i++) {
      const char = text[i];

      if (inQuotes) {
        if (char === '"') {
          if (text[i + 1] === '"') {
            field += '"';
            i++;
          } else {
            inQuotes = false;
          }
        } else {
          field += char;
        }
        continue;
      }

      if (char === '"') {
        inQuotes = true;
      } else if (char === ',') {
        row.push(field);
        field = '';
      } else if (char === '\n') {
        row.push(field);
        rows.push(row);
        row = [];
        field = '';
      } else if (char === '\r') {
        // ignore CR in CRLF
      } else {
        field += char;
      }
    }

    if (field.length > 0 || row.length > 0) {
      row.push(field);
      rows.push(row);
    }

    if (rows.length === 0) return [];

    const headers = rows[0].map(h => h.trim());
    return rows
      .slice(1)
      .filter(r => r.some(v => v !== ''))
      .map(r => {
        const obj = {};
        headers.forEach((h, i) => { obj[h] = r[i] ?? ''; });
        return obj;
      });
  }

  function toCsv(records, columns) {
    const esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [columns.map(esc).join(',')];
    for (const rec of records) lines.push(columns.map(c => esc(rec[c])).join(','));
    return lines.join('\n') + '\n';
  }

  function download(filename, text) {
    const blob = new Blob([text], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // ---------- storage ----------

  function ratingKey(rater, row) {
    return [rater, row.conceptUri, row.prompt_number, row.llm].join('||');
  }

  function loadRatings() {
    try {
      state.ratings = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    } catch (err) {
      state.ratings = {};
    }
  }

  function saveRatings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.ratings));
    } catch (err) {
      setStatus('Could not save to browser storage: ' + err.message, true);
    }
  }

  // ---------- helpers ----------

  function setStatus(text, isError) {
    el.status.textContent = text;
    el.status.classList.toggle('error', Boolean(isError));
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function uniqueSorted(values) {
    return [...new Set(values.filter(v => v !== ''))].sort((a, b) =>
      a.localeCompare(b, undefined, { numeric: true }));
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

  // ---------- the queue ----------

  function rebuildQueue() {
    const model = el.modelFilter.value;
    const prompt = el.promptFilter.value;

    state.queue = state.rows.filter(row => {
      if (model && row.llm !== model) return false;
      if (prompt && row.prompt_number !== prompt) return false;
      return !state.ratings[ratingKey(state.rater, row)];
    });

    state.index = 0;
    state.draft = {};
    render();
  }

  function currentRow() {
    return state.queue[state.index] || null;
  }

  // ---------- rendering ----------

  function renderCriteria(row) {
    el.criteria.innerHTML = '';

    for (const crit of CRITERIA) {
      const wrap = document.createElement('div');
      wrap.className = 'criterion';

      const label = document.createElement('div');
      label.className = 'criterion-label';
      label.innerHTML = `${escapeHtml(crit.label)} <span class="subtle">${escapeHtml(crit.hint)}</span>`;
      wrap.appendChild(label);

      const group = document.createElement('div');
      group.className = 'score-group';
      for (let score = 1; score <= 5; score++) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'score-btn' + (state.draft[crit.key] === score ? ' active' : '');
        btn.textContent = String(score);
        btn.addEventListener('click', () => {
          state.draft[crit.key] = score;
          renderCriteria(row);
          updateSaveState();
        });
        group.appendChild(btn);
      }
      wrap.appendChild(group);
      el.criteria.appendChild(wrap);
    }
  }

  function updateSaveState() {
    const complete = CRITERIA.every(c => state.draft[c.key]);
    el.saveBtn.disabled = !complete;
    el.saveBtn.textContent = complete
      ? 'Save and next'
      : `Save and next (${CRITERIA.filter(c => state.draft[c.key]).length}/${CRITERIA.length} scored)`;
  }

  function renderSummary() {
    const mine = Object.values(state.ratings).filter(r => r.rater === state.rater);
    if (mine.length === 0) {
      el.summary.textContent = 'No ratings stored yet.';
      return;
    }

    const perModel = {};
    for (const r of mine) {
      const overall = CRITERIA.reduce((sum, c) => sum + Number(r[c.key]), 0) / CRITERIA.length;
      perModel[r.llm] = perModel[r.llm] || { n: 0, sum: 0 };
      perModel[r.llm].n += 1;
      perModel[r.llm].sum += overall;
    }

    const parts = Object.entries(perModel)
      .sort()
      .map(([m, v]) => `${escapeHtml(m)} ${(v.sum / v.n).toFixed(2)} (n=${v.n})`);
    el.summary.innerHTML = `<b>${mine.length}</b> rated by ${escapeHtml(state.rater)} &mdash; mean so far: ${parts.join(', ')}`;
  }

  function render() {
    renderSummary();

    const total = state.queue.length;
    if (!state.rater) {
      el.card.hidden = true;
      el.progress.textContent = '';
      setStatus('Type your name above to start rating.');
      return;
    }

    if (total === 0) {
      el.card.hidden = true;
      el.progress.textContent = '';
      setStatus(state.rows.length
        ? 'Nothing left to rate with these filters. Export your ratings when you are done.'
        : 'No answers to rate yet.');
      el.backBtn.disabled = true;
      return;
    }

    const row = currentRow();
    el.card.hidden = false;
    el.progress.textContent = `${state.index + 1} of ${total} left`;
    setStatus(`Rating as ${state.rater}.`);

    el.skill.textContent = row.preferredLabel || row.conceptUri;
    const bits = [
      `model ${row.llm}`,
      `prompt P${row.prompt_number}`,
      row.prompt_category ? `${row.prompt_category} ${row.category_name || ''}`.trim() : '',
      row.bloom_level ? `Bloom: ${row.bloom_level}` : '',
    ].filter(Boolean);
    el.meta.textContent = bits.join('  |  ');
    el.question.textContent = row.prompt_text;
    el.answer.textContent = row.response_text;
    el.backBtn.disabled = state.index === 0;

    renderCriteria(row);
    updateSaveState();
  }

  // ---------- actions ----------

  function saveCurrent() {
    const row = currentRow();
    if (!row) return;
    if (!CRITERIA.every(c => state.draft[c.key])) return;

    const record = {
      conceptUri: row.conceptUri,
      preferredLabel: row.preferredLabel,
      prompt_number: row.prompt_number,
      prompt_category: row.prompt_category || '',
      llm: row.llm,
      rater: state.rater,
    };
    for (const c of CRITERIA) record[c.key] = state.draft[c.key];

    state.ratings[ratingKey(state.rater, row)] = record;
    saveRatings();

    // the rated answer leaves the queue, so the index already points at the next one
    state.queue.splice(state.index, 1);
    if (state.index >= state.queue.length) state.index = Math.max(0, state.queue.length - 1);
    state.draft = {};
    render();
  }

  function skipCurrent() {
    if (state.index < state.queue.length - 1) state.index += 1;
    state.draft = {};
    render();
  }

  function goBack() {
    if (state.index > 0) state.index -= 1;
    state.draft = {};
    render();
  }

  function exportRatings() {
    const mine = Object.values(state.ratings).filter(r => r.rater === state.rater);
    if (mine.length === 0) {
      setStatus('Nothing to export yet.', true);
      return;
    }

    const columns = ['conceptUri', 'preferredLabel', 'prompt_number', 'prompt_category',
      'llm', 'rater', ...CRITERIA.map(c => c.key)];
    download(`ratings_${state.rater}.csv`, toCsv(mine, columns));
    setStatus(`Exported ${mine.length} ratings. Save the file in evaluate/output_evaluate/.`);
  }

  async function importRatings(file) {
    const text = await file.text();
    const records = parseCSV(text);
    let added = 0;

    for (const rec of records) {
      if (!rec.conceptUri || !rec.rater) continue;
      state.ratings[ratingKey(rec.rater, rec)] = rec;
      added += 1;
    }

    saveRatings();
    setStatus(`Loaded ${added} ratings from ${file.name}.`);
    rebuildQueue();
  }

  function clearMine() {
    const mine = Object.values(state.ratings).filter(r => r.rater === state.rater);
    if (mine.length === 0) return;
    if (!confirm(`Delete the ${mine.length} ratings stored for ${state.rater}? Export them first if you want to keep them.`)) return;

    for (const key of Object.keys(state.ratings)) {
      if (state.ratings[key].rater === state.rater) delete state.ratings[key];
    }
    saveRatings();
    rebuildQueue();
  }

  // ---------- loading ----------

  function acceptRows(records) {
    const needed = ['conceptUri', 'prompt_number', 'llm', 'response_text', 'prompt_text'];
    const first = records[0] || {};
    const missing = needed.filter(c => !(c in first));
    if (missing.length) {
      setStatus(`The responses file is missing the columns: ${missing.join(', ')}`, true);
      return;
    }

    state.rows = records.filter(r => (r.error || '') === '' && (r.response_text || '') !== '');
    fillFilter(el.modelFilter, uniqueSorted(state.rows.map(r => r.llm)));
    fillFilter(el.promptFilter, uniqueSorted(state.rows.map(r => r.prompt_number)));
    rebuildQueue();
  }

  async function loadDefaultCsv() {
    setStatus(`Loading ${DEFAULT_CSV_FILE}...`);
    try {
      const response = await fetch(DEFAULT_CSV_FILE);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      acceptRows(parseCSV(await response.text()));
    } catch (err) {
      setStatus(`Could not load ${DEFAULT_CSV_FILE} (${err.message}). Run Step 4 first, and serve the project root folder.`, true);
    }
  }

  // ---------- events ----------

  function setupEvents() {
    el.rater.addEventListener('change', () => {
      state.rater = el.rater.value.trim();
      localStorage.setItem(STORAGE_KEY + '.rater', state.rater);
      rebuildQueue();
    });

    el.modelFilter.addEventListener('change', rebuildQueue);
    el.promptFilter.addEventListener('change', rebuildQueue);
    el.saveBtn.addEventListener('click', saveCurrent);
    el.skipBtn.addEventListener('click', skipCurrent);
    el.backBtn.addEventListener('click', goBack);
    el.exportBtn.addEventListener('click', exportRatings);
    el.clearBtn.addEventListener('click', clearMine);

    el.importInput.addEventListener('change', async () => {
      const file = el.importInput.files?.[0];
      if (!file) return;
      await importRatings(file);
      el.importInput.value = '';
    });

    // 1 to 5 fills the next criterion that has no score, Enter saves
    document.addEventListener('keydown', event => {
      const tab = document.getElementById('tabRate');
      if (!tab || tab.hidden) return;
      if (event.target.matches('input, textarea, select')) return;

      if (event.key >= '1' && event.key <= '5') {
        const next = CRITERIA.find(c => !state.draft[c.key]);
        if (next) {
          state.draft[next.key] = Number(event.key);
          renderCriteria(currentRow());
          updateSaveState();
          event.preventDefault();
        }
      } else if (event.key === 'Enter') {
        saveCurrent();
        event.preventDefault();
      }
    });
  }

  loadRatings();
  state.rater = localStorage.getItem(STORAGE_KEY + '.rater') || '';
  el.rater.value = state.rater;
  setupEvents();
  loadDefaultCsv();
})();
