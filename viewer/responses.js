// Step 4 tab: browse the collected LLM answers.
// Extracted from the standalone prompt/response_viewer.html. The element ids
// carry an r_ prefix so they cannot clash with the Step 3 tab in the same page.
(function () {

    const DEFAULT_CSV_FILE = '../prompt/output_prompt/responses.csv';
    const UNASSIGNED_CATEGORY_FILTER_VALUE = '__unassigned__';

    const state = {
      headers: [],
      allRows: [],
      filteredRows: [],
      filteredSkills: [],
      allModels: [],
      visibleCount: 25,
      pageSize: 25,
      activeSkillKey: null,
      activeDetailsTab: 'skill',
      activePromptBySkill: {},
      activeModelBySkillPrompt: {},
      activeResponseRenderModeBySkillPromptModel: {},
      sourceName: DEFAULT_CSV_FILE,
      copyFeedback: '',
      copyFeedbackType: '',
      isResizingGrid: false,
      resizeStartY: 0,
      resizeStartHeight: 0,
      resizeStartDetailsHeight: 0,
    };

    const el = {
      sourceLabel: document.getElementById('r_sourceLabel'),
      search: document.getElementById('r_search'),
      promptNumber: document.getElementById('r_promptNumber'),
      llmFilter: document.getElementById('r_llmFilter'),
      categoryFilter: document.getElementById('r_categoryFilter'),
      promptCategory: document.getElementById('r_promptCategory'),
      pageSize: document.getElementById('r_pageSize'),
      csvFileInput: document.getElementById('r_csvFileInput'),
      reloadDefaultBtn: document.getElementById('r_reloadDefaultBtn'),
      resetBtn: document.getElementById('r_resetBtn'),
      statusText: document.getElementById('r_statusText'),
      tableWrap: document.getElementById('r_tableWrap'),
      tableBody: document.getElementById('r_tableBody'),
      pageInfo: document.getElementById('r_pageInfo'),
      loadMoreBtn: document.getElementById('r_loadMoreBtn'),
      panelDivider: document.getElementById('r_panelDivider'),
      detailsPanel: document.getElementById('r_detailsPanel'),
      details: document.getElementById('r_details'),
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

      if (rows.length === 0) return { headers: [], records: [] };

      const headers = rows[0].map(h => h.trim());
      const records = rows
        .slice(1)
        .filter(r => r.some(v => v !== ''))
        .map((r, idx) => {
          const obj = {};
          headers.forEach((h, colIdx) => {
            obj[h] = r[colIdx] ?? '';
          });
          obj.__id = `row_${idx + 1}`;
          const conceptUri = (obj.conceptUri || '').trim();
          const preferredLabel = (obj.preferredLabel || '').trim();
          obj.__skillKey = conceptUri || preferredLabel || `skill_${idx + 1}`;
          return obj;
        });

      return { headers, records };
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }

    function normalize(value) {
      return String(value ?? '').trim();
    }

    function readCategory(row) {
      // The thematic macro-category (G1-G8) comes from Step 2. Older exports
      // used a plain 'Category' column, so accept either.
      return normalize(row.Category) || normalize(row.thematicCategory);
    }

    function toCategoryShortLabel(value) {
      const text = normalize(value);
      if (!text) return '';
      const idx = text.indexOf(' — ');
      return idx >= 0 ? text.slice(0, idx) : text;
    }

    function shortenUri(uri, maxLen = 44) {
      const text = normalize(uri);
      if (!text) return '';
      if (text.length <= maxLen) return text;
      return `${text.slice(0, maxLen - 1)}…`;
    }

    function uniqueSorted(values, numeric = false) {
      const list = [...new Set(values.map(v => normalize(v)).filter(Boolean))];
      if (numeric) {
        return list.sort((a, b) => {
          const an = Number.parseInt(a, 10);
          const bn = Number.parseInt(b, 10);
          if (Number.isFinite(an) && Number.isFinite(bn)) return an - bn;
          return a.localeCompare(b);
        });
      }
      return list.sort((a, b) => a.localeCompare(b));
    }

    function fillFilter(selectEl, values, labelFn = value => value) {
      selectEl.innerHTML = '<option value="">All</option>';
      values.forEach(value => {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = labelFn(value);
        selectEl.appendChild(opt);
      });
      selectEl.disabled = values.length === 0;
    }

    function hasError(row) {
      return normalize(row.error).length > 0;
    }

    function hasResponse(row) {
      return normalize(row.response_text).length > 0;
    }

    function statusFromRow(row) {
      if (!row) return 'missing';
      if (hasError(row)) return 'error';
      if (hasResponse(row)) return 'success';
      return 'missing';
    }

    function parseDateMs(value) {
      const text = normalize(value);
      if (!text) return Number.NEGATIVE_INFINITY;
      const t = Date.parse(text);
      return Number.isFinite(t) ? t : Number.NEGATIVE_INFINITY;
    }

    function buildSkillGroups(rows) {
      const map = new Map();

      rows.forEach(row => {
        const key = row.__skillKey;
        if (!map.has(key)) {
          map.set(key, {
            key,
            rows: [],
            preferredLabel: normalize(row.preferredLabel),
            conceptUri: normalize(row.conceptUri),
            category: readCategory(row),
            prompts: new Set(),
            models: new Set(),
            successCount: 0,
            errorCount: 0,
            emptyCount: 0,
          });
        }

        const group = map.get(key);
        group.rows.push(row);

        if (!group.preferredLabel && normalize(row.preferredLabel)) {
          group.preferredLabel = normalize(row.preferredLabel);
        }
        if (!group.conceptUri && normalize(row.conceptUri)) {
          group.conceptUri = normalize(row.conceptUri);
        }
        if (!group.category && readCategory(row)) {
          group.category = readCategory(row);
        }

        const promptNumber = normalize(row.prompt_number);
        if (promptNumber) group.prompts.add(promptNumber);

        const model = normalize(row.llm);
        if (model) group.models.add(model);

        const rowStatus = statusFromRow(row);
        if (rowStatus === 'success') group.successCount += 1;
        else if (rowStatus === 'error') group.errorCount += 1;
        else group.emptyCount += 1;
      });

      return [...map.values()].sort((a, b) => {
        const la = a.preferredLabel || a.conceptUri || a.key;
        const lb = b.preferredLabel || b.conceptUri || b.key;
        return la.localeCompare(lb);
      });
    }

    function getSkillGroupByKey(skillKey) {
      return state.filteredSkills.find(g => g.key === skillKey) || null;
    }

    function chooseDefaultSkill() {
      if (state.filteredSkills.length === 0) {
        state.activeSkillKey = null;
        return;
      }

      const exists = state.filteredSkills.some(g => g.key === state.activeSkillKey);
      if (exists) return;
      state.activeSkillKey = state.filteredSkills[0].key;
    }

    function getModelScope() {
      const filteredModel = normalize(el.llmFilter.value);
      if (filteredModel) return [filteredModel];
      return [...state.allModels];
    }

    function getPromptOptionsForGroup(group) {
      if (!group) return [];
      return uniqueSorted(group.rows.map(row => row.prompt_number), true);
    }

    function getActivePromptForGroup(group) {
      if (!group) return '';

      const globalPromptFilter = normalize(el.promptNumber.value);
      const options = getPromptOptionsForGroup(group);
      if (globalPromptFilter) {
        return globalPromptFilter;
      }

      const remembered = state.activePromptBySkill[group.key];
      if (remembered && options.includes(remembered)) {
        return remembered;
      }

      const fallback = options[0] || '';
      state.activePromptBySkill[group.key] = fallback;
      return fallback;
    }

    function getModelStateKey(skillKey, promptNumber) {
      return `${skillKey}::${promptNumber || '__no_prompt__'}`;
    }

    function getRenderModeStateKey(skillKey, promptNumber, model) {
      return `${skillKey}::${promptNumber || '__no_prompt__'}::${model || '__no_model__'}`;
    }

    function getActiveRenderMode(skillKey, promptNumber, model) {
      const key = getRenderModeStateKey(skillKey, promptNumber, model);
      const mode = state.activeResponseRenderModeBySkillPromptModel[key];
      return mode === 'raw' ? 'raw' : 'markdown';
    }

    function setActiveRenderMode(skillKey, promptNumber, model, mode) {
      const key = getRenderModeStateKey(skillKey, promptNumber, model);
      state.activeResponseRenderModeBySkillPromptModel[key] = mode === 'raw' ? 'raw' : 'markdown';
    }

    function markdownLibsAvailable() {
      return typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined';
    }

    function hardenSanitizedLinks(html) {
      const template = document.createElement('template');
      template.innerHTML = html;
      template.content.querySelectorAll('a').forEach(link => {
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
      });
      return template.innerHTML;
    }

    function renderResponseAsMarkdownHtml(text) {
      if (!text) return '';

      if (!markdownLibsAvailable()) {
        return escapeHtml(text).replace(/\n/g, '<br>');
      }

      marked.setOptions({
        gfm: true,
        breaks: false,
      });

      const rawHtml = marked.parse(text);
      const cleanHtml = DOMPurify.sanitize(rawHtml, {
        USE_PROFILES: { html: true },
      });

      return hardenSanitizedLinks(cleanHtml);
    }

    function getResponseRenderWarning(mode) {
      if (mode !== 'markdown') return '';
      if (markdownLibsAvailable()) return '';
      return 'Markdown libraries unavailable; showing safe plain-text fallback.';
    }

    function getActiveModelFor(skillKey, promptNumber, modelScope) {
      const scope = modelScope && modelScope.length ? modelScope : getModelScope();
      if (scope.length === 0) return '';

      const stateKey = getModelStateKey(skillKey, promptNumber);
      const remembered = state.activeModelBySkillPrompt[stateKey];
      if (remembered && scope.includes(remembered)) return remembered;

      const fallback = scope[0];
      state.activeModelBySkillPrompt[stateKey] = fallback;
      return fallback;
    }

    function rowsForSkillPromptModel(group, promptNumber, model) {
      if (!group) return [];
      const p = normalize(promptNumber);
      const m = normalize(model);

      return group.rows.filter(row => {
        if (p && normalize(row.prompt_number) !== p) return false;
        if (m && normalize(row.llm) !== m) return false;
        return true;
      });
    }

    function chooseBestRow(rows) {
      if (!rows.length) return null;
      return [...rows].sort((a, b) => {
        const dateDiff = parseDateMs(b.datetime) - parseDateMs(a.datetime);
        if (dateDiff !== 0) return dateDiff;
        return String(a.__id).localeCompare(String(b.__id));
      })[0];
    }

    function getPromptRepresentativeRow(group, promptNumber) {
      if (!group) return null;
      const p = normalize(promptNumber);
      const candidates = group.rows.filter(row => {
        if (!p) return true;
        return normalize(row.prompt_number) === p;
      });
      return chooseBestRow(candidates);
    }

    function formatStatusSummary(group) {
      return `S:${group.successCount} • E:${group.errorCount} • Empty:${group.emptyCount}`;
    }

    function buildSearchHaystack(row) {
      const fields = [
        row.preferredLabel,
        row.description,
        row.conceptUri,
        readCategory(row),
        row.prompt_number,
        row.prompt_name,
        row.prompt_category,
        row.llm,
        row.response_text,
        row.error,
      ];
      return fields.map(v => String(v || '')).join(' ').toLowerCase();
    }

    function applyFilters() {
      const q = normalize(el.search.value).toLowerCase();
      const promptNumber = normalize(el.promptNumber.value);
      const llm = normalize(el.llmFilter.value);
      const category = normalize(el.categoryFilter.value);
      const promptCategory = normalize(el.promptCategory.value);

      state.filteredRows = state.allRows.filter(row => {
        if (promptNumber && normalize(row.prompt_number) !== promptNumber) return false;
        if (llm && normalize(row.llm) !== llm) return false;
        const rowCategory = readCategory(row);
        if (category === UNASSIGNED_CATEGORY_FILTER_VALUE && rowCategory) return false;
        if (category && category !== UNASSIGNED_CATEGORY_FILTER_VALUE && rowCategory !== category) return false;
        if (promptCategory && normalize(row.prompt_category) !== promptCategory) return false;
        if (!q) return true;
        return buildSearchHaystack(row).includes(q);
      });

      state.filteredSkills = buildSkillGroups(state.filteredRows);
      state.visibleCount = state.pageSize;
      chooseDefaultSkill();

      if (state.filteredSkills.length === 0) {
        state.copyFeedback = '';
        state.copyFeedbackType = '';
      }

      render();
    }

    function getVisibleSkills() {
      return state.filteredSkills.slice(0, state.visibleCount);
    }

    function ensureVisibleRowsForScroll() {
      const wrap = el.tableWrap;
      if (!wrap) return;

      let safety = 0;
      while (
        wrap.scrollHeight <= wrap.clientHeight + 4
        && state.visibleCount < state.filteredSkills.length
        && safety < 10
      ) {
        state.visibleCount = Math.min(state.filteredSkills.length, state.visibleCount + state.pageSize);
        renderTable();
        renderPaginationAndStatus();
        safety += 1;
      }
    }

    function maybeLoadMoreRows() {
      const wrap = el.tableWrap;
      if (!wrap) return;

      const nearBottom = wrap.scrollTop + wrap.clientHeight >= wrap.scrollHeight - 80;
      if (!nearBottom) return;
      if (state.visibleCount >= state.filteredSkills.length) return;

      state.visibleCount = Math.min(state.filteredSkills.length, state.visibleCount + state.pageSize);
      render();
    }

    function renderTable() {
      const skills = getVisibleSkills();

      if (skills.length === 0) {
        el.tableBody.innerHTML = '<tr><td colspan="6" class="empty">No skills match your current filters.</td></tr>';
        return;
      }

      el.tableBody.innerHTML = skills.map(group => {
        const activeClass = group.key === state.activeSkillKey ? 'active' : '';

        return `
          <tr class="${activeClass}" data-skill-key="${escapeHtml(group.key)}">
            <td title="${escapeHtml(group.preferredLabel || group.key)}" class="ellipsis">${escapeHtml(group.preferredLabel || '(No label)')}</td>
            <td title="${escapeHtml(group.conceptUri)}" class="mono ellipsis">${escapeHtml(shortenUri(group.conceptUri || ''))}</td>
            <td title="${escapeHtml(group.category)}" class="ellipsis">${escapeHtml(toCategoryShortLabel(group.category || ''))}</td>
            <td>${group.prompts.size}</td>
            <td>${group.models.size}</td>
            <td>${escapeHtml(formatStatusSummary(group))}</td>
          </tr>
        `;
      }).join('');
    }

    function detailsField(label, value, isMono = false) {
      return `
        <dt>${escapeHtml(label)}</dt>
        <dd class="${isMono ? 'mono' : ''}">${escapeHtml(value || '')}</dd>
      `;
    }

    function renderDetailsTabs(activeTab) {
      const tabs = [
        { key: 'skill', label: 'Skill Info' },
        { key: 'prompt', label: 'Prompt' },
        { key: 'response', label: 'LLM Response' },
        { key: 'meta', label: 'Run Metadata' },
      ];

      return `
        <div class="tabs">
          ${tabs.map(tab => `
            <button
              type="button"
              class="tab-btn ${tab.key === activeTab ? 'active' : ''}"
              data-details-tab="${escapeHtml(tab.key)}"
            >${escapeHtml(tab.label)}</button>
          `).join('')}
        </div>
      `;
    }

    function renderModelTabs(modelScope, activeModel, tabTarget) {
      if (!modelScope.length) {
        return '<div class="empty">No models are available in this dataset.</div>';
      }

      return `
        <div class="subtabs">
          ${modelScope.map(model => `
            <button
              type="button"
              class="subtab-btn ${model === activeModel ? 'active' : ''}"
              data-model-tab="${escapeHtml(model)}"
              data-model-target="${escapeHtml(tabTarget)}"
            >${escapeHtml(model)}</button>
          `).join('')}
        </div>
      `;
    }

    function renderPromptSelector(group, activePrompt) {
      const promptOptions = getPromptOptionsForGroup(group);
      const globalPromptFilter = normalize(el.promptNumber.value);
      const disabled = Boolean(globalPromptFilter) || promptOptions.length <= 1;

      return `
        <div class="field details-controls">
          <label for="detailsPromptSelect">Prompt number</label>
          <select id="detailsPromptSelect" ${disabled ? 'disabled' : ''}>
            ${promptOptions.map(value => `
              <option value="${escapeHtml(value)}" ${value === activePrompt ? 'selected' : ''}>${escapeHtml(value)}</option>
            `).join('')}
          </select>
        </div>
      `;
    }

    function renderSkillInfoTab(representativeRow) {
      if (!representativeRow) {
        return '<div class="empty">No skill data available for current filters.</div>';
      }

      return `
        <dl class="details-grid">
          ${detailsField('preferredLabel', representativeRow.preferredLabel)}
          ${detailsField('conceptUri', representativeRow.conceptUri, true)}
          ${detailsField('description', representativeRow.description)}
          ${detailsField('skillType', representativeRow.skillType)}
          ${detailsField('reuseLevel', representativeRow.reuseLevel)}
          ${detailsField('status', representativeRow.status)}
          ${detailsField('Category', readCategory(representativeRow))}
          ${detailsField('thematicCategory', representativeRow.thematicCategory)}
          ${detailsField('escoLevel0Pillar', representativeRow.escoLevel0Pillar)}
          ${detailsField('escoLevel1Category', representativeRow.escoLevel1Category)}
        </dl>
      `;
    }

    function renderPromptTab(promptRow, activePrompt) {
      if (!promptRow) {
        return `<div class="empty">No prompt data found for prompt ${escapeHtml(activePrompt || '(none)')}.</div>`;
      }

      return `
        <dl class="details-grid">
          ${detailsField('prompt_number', promptRow.prompt_number)}
          ${detailsField('prompt_name', promptRow.prompt_name)}
          ${detailsField('prompt_category', promptRow.prompt_category)}
          ${detailsField('category_name', promptRow.category_name)}
          ${detailsField('bloom_level', promptRow.bloom_level)}
          ${detailsField('variation', promptRow.variation)}
          ${detailsField('source', promptRow.source)}
          ${detailsField('system_prompt_rendered', promptRow.system_prompt_rendered)}
          ${detailsField('prompt_text', promptRow.prompt_text)}
        </dl>
      `;
    }

    function renderResponseTab(group, activePrompt) {
      const modelScope = getModelScope();
      const activeModel = getActiveModelFor(group.key, activePrompt, modelScope);
      const rows = rowsForSkillPromptModel(group, activePrompt, activeModel);
      const row = chooseBestRow(rows);

      const status = statusFromRow(row);
      const statusLabel = status === 'success' ? 'Success' : status === 'error' ? 'Error' : 'No response';
      const responseText = row ? normalize(row.response_text) : '';
      const hasCopyable = responseText.length > 0;
      const renderMode = getActiveRenderMode(group.key, activePrompt, activeModel);
      const renderWarning = getResponseRenderWarning(renderMode);

      let feedbackClass = 'feedback';
      if (state.copyFeedbackType === 'ok') feedbackClass += ' ok';
      if (state.copyFeedbackType === 'error') feedbackClass += ' error';

      const responseHtml = hasCopyable && renderMode === 'markdown'
        ? renderResponseAsMarkdownHtml(responseText)
        : escapeHtml(responseText || 'No response available for this model and prompt.');

      return `
        ${renderModelTabs(modelScope, activeModel, 'response')}
        <span class="status-badge ${status === 'success' ? 'success' : status === 'error' ? 'error' : 'missing'}">${escapeHtml(statusLabel)}</span>
        <div class="inline-actions">
          <button
            type="button"
            class="secondary"
            data-copy-response="true"
            data-copy-model="${escapeHtml(activeModel)}"
            data-copy-prompt="${escapeHtml(activePrompt)}"
            ${hasCopyable ? '' : 'disabled'}
          >Copy response</button>
          <div class="toggle-group" role="group" aria-label="Response render mode">
            <button
              type="button"
              class="toggle-btn ${renderMode === 'markdown' ? 'active' : ''}"
              data-render-mode="markdown"
              data-render-model="${escapeHtml(activeModel)}"
              data-render-prompt="${escapeHtml(activePrompt)}"
            >Markdown</button>
            <button
              type="button"
              class="toggle-btn ${renderMode === 'raw' ? 'active' : ''}"
              data-render-mode="raw"
              data-render-model="${escapeHtml(activeModel)}"
              data-render-prompt="${escapeHtml(activePrompt)}"
            >Raw text</button>
          </div>
          <span class="${feedbackClass}">${escapeHtml(state.copyFeedback || '')}</span>
        </div>
        ${renderWarning ? `<div class="feedback error">${escapeHtml(renderWarning)}</div>` : ''}
        <div class="response-box ${renderMode === 'markdown' ? 'markdown' : 'raw'}">${responseHtml}</div>
        ${row && normalize(row.error) ? `<div class="error-box"><strong>Error:</strong> ${escapeHtml(row.error)}</div>` : ''}
      `;
    }

    function renderMetaTab(group, activePrompt) {
      const modelScope = getModelScope();
      const activeModel = getActiveModelFor(group.key, activePrompt, modelScope);
      const rows = rowsForSkillPromptModel(group, activePrompt, activeModel);
      const row = chooseBestRow(rows);

      if (!row) {
        return `
          ${renderModelTabs(modelScope, activeModel, 'meta')}
          <div class="empty">No response for this model and prompt.</div>
        `;
      }

      const status = statusFromRow(row);
      const statusLabel = status === 'success' ? 'Success' : status === 'error' ? 'Error' : 'No response';

      return `
        ${renderModelTabs(modelScope, activeModel, 'meta')}
        <span class="status-badge ${status === 'success' ? 'success' : status === 'error' ? 'error' : 'missing'}">${escapeHtml(statusLabel)}</span>
        <div class="meta-card">
          <dl class="details-grid">
            ${detailsField('llm', row.llm)}
            ${detailsField('litellm_model', row.litellm_model)}
            ${detailsField('datetime', row.datetime)}
            ${detailsField('prompt_tokens', row.prompt_tokens)}
            ${detailsField('completion_tokens', row.completion_tokens)}
            ${detailsField('total_tokens', row.total_tokens)}
            ${detailsField('time_taken_s', row.time_taken_s)}
            ${detailsField('cost', row.cost)}
            ${detailsField('error', row.error)}
            ${detailsField('params_json', row.params_json)}
          </dl>
        </div>
      `;
    }

    function renderDetails() {
      const group = getSkillGroupByKey(state.activeSkillKey);
      if (!group) {
        el.details.className = 'empty';
        el.details.textContent = 'Click a skill row to inspect prompt context, responses per model, and metadata.';
        return;
      }

      const activePrompt = getActivePromptForGroup(group);
      const promptRow = getPromptRepresentativeRow(group, activePrompt);
      const representativeRow = chooseBestRow(group.rows);

      let tabContent = '';
      if (state.activeDetailsTab === 'skill') {
        tabContent = renderSkillInfoTab(representativeRow);
      } else if (state.activeDetailsTab === 'prompt') {
        tabContent = renderPromptTab(promptRow, activePrompt);
      } else if (state.activeDetailsTab === 'response') {
        tabContent = renderResponseTab(group, activePrompt);
      } else {
        tabContent = renderMetaTab(group, activePrompt);
      }

      const promptSelectorHtml = renderPromptSelector(group, activePrompt);
      const titleLabel = representativeRow?.preferredLabel || group.preferredLabel || '(No label)';
      const titleUri = representativeRow?.conceptUri || group.conceptUri || '';

      el.details.className = '';
      el.details.innerHTML = `
        <div class="details-header">
          <div class="details-title">
            <h2>Entry details</h2>
            <div class="skill-name">${escapeHtml(titleLabel)}</div>
            <div class="skill-uri">${escapeHtml(titleUri)}</div>
          </div>
          ${promptSelectorHtml}
        </div>
        ${renderDetailsTabs(state.activeDetailsTab)}
        ${tabContent}
      `;
    }

    function renderPaginationAndStatus() {
      const totalSkills = state.filteredSkills.length;
      const visibleSkills = Math.min(state.visibleCount, totalSkills);
      const loadedRows = state.allRows.length;
      const filteredRows = state.filteredRows.length;

      el.pageInfo.textContent = `Showing ${visibleSkills} of ${totalSkills} skills`;
      el.loadMoreBtn.disabled = visibleSkills >= totalSkills;

      el.statusText.className = 'status ok';
      el.statusText.textContent = `${loadedRows} rows loaded • ${filteredRows} rows after filters • ${totalSkills} skills in grid`;
    }

    function render() {
      renderTable();
      renderDetails();
      renderPaginationAndStatus();
      ensureVisibleRowsForScroll();
    }

    function initFilters() {
      const previous = {
        promptNumber: el.promptNumber.value,
        llmFilter: el.llmFilter.value,
        categoryFilter: el.categoryFilter.value,
        promptCategory: el.promptCategory.value,
      };

      fillFilter(el.promptNumber, uniqueSorted(state.allRows.map(r => r.prompt_number), true));
      fillFilter(el.llmFilter, uniqueSorted(state.allRows.map(r => r.llm)));
      fillFilter(el.promptCategory, uniqueSorted(state.allRows.map(r => r.prompt_category)));

      const categoryValues = uniqueSorted(state.allRows.map(r => r.Category));
      fillFilter(el.categoryFilter, categoryValues, toCategoryShortLabel);
      const unassigned = document.createElement('option');
      unassigned.value = UNASSIGNED_CATEGORY_FILTER_VALUE;
      unassigned.textContent = 'Unassigned';
      el.categoryFilter.insertBefore(unassigned, el.categoryFilter.children[1] || null);
      el.categoryFilter.disabled = false;

      state.allModels = uniqueSorted(state.allRows.map(r => r.llm));

      const restore = (selectEl, value) => {
        if (!value) {
          selectEl.value = '';
          return;
        }
        const hasValue = [...selectEl.options].some(opt => opt.value === value);
        selectEl.value = hasValue ? value : '';
      };

      restore(el.promptNumber, previous.promptNumber);
      restore(el.llmFilter, previous.llmFilter);
      restore(el.categoryFilter, previous.categoryFilter);
      restore(el.promptCategory, previous.promptCategory);
    }

    function normalizeParsedRows(headers, records) {
      const normalizedHeaders = headers.length ? headers : [];
      const required = [
        'conceptUri',
        'preferredLabel',
        'description',
        'Category',
        'prompt_number',
        'prompt_name',
        'prompt_category',
        'prompt_text',
        'system_prompt_rendered',
        'llm',
        'response_text',
        'error',
      ];

      required.forEach(header => {
        if (!normalizedHeaders.includes(header)) {
          normalizedHeaders.push(header);
        }
      });

      records.forEach((row, idx) => {
        required.forEach(header => {
          if (!(header in row)) row[header] = '';
        });

        row.__id = row.__id || `row_${idx + 1}`;
        const conceptUri = normalize(row.conceptUri);
        const preferredLabel = normalize(row.preferredLabel);
        row.__skillKey = conceptUri || preferredLabel || `skill_${idx + 1}`;
      });

      return { headers: normalizedHeaders, records };
    }

    function loadParsedCsv(headers, records, sourceName) {
      if (!headers.length) {
        throw new Error('CSV has no header row.');
      }

      const normalized = normalizeParsedRows([...headers], [...records]);
      state.headers = normalized.headers;
      state.allRows = normalized.records;
      state.sourceName = sourceName;
      state.visibleCount = state.pageSize;
      state.activeSkillKey = null;
      state.activeDetailsTab = 'skill';
      state.activePromptBySkill = {};
      state.activeModelBySkillPrompt = {};
      state.activeResponseRenderModeBySkillPromptModel = {};
      state.copyFeedback = '';
      state.copyFeedbackType = '';

      el.sourceLabel.textContent = sourceName;
      initFilters();
      applyFilters();
    }

    async function loadDefaultCsv() {
      el.statusText.className = 'status';
      el.statusText.textContent = `Loading ${DEFAULT_CSV_FILE}...`;

      try {
        const response = await fetch(DEFAULT_CSV_FILE);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const csvText = await response.text();
        const { headers, records } = parseCSV(csvText);
        loadParsedCsv(headers, records, DEFAULT_CSV_FILE);
      } catch (error) {
        el.statusText.className = 'status error';
        el.statusText.textContent = `Failed to load CSV: ${error.message}`;
        el.tableBody.innerHTML = `
          <tr>
            <td colspan="6" class="empty">
              Could not load <code>${escapeHtml(DEFAULT_CSV_FILE)}</code>.<br>
              Start a local server in this folder and open the page through HTTP (not file://).<br>
              Example: <code>python3 -m http.server 8000</code>
            </td>
          </tr>
        `;
        el.details.className = 'empty';
        el.details.textContent = 'No data loaded.';
      }
    }

    async function loadCsvFile(file) {
      el.statusText.className = 'status';
      el.statusText.textContent = `Loading ${file.name}...`;

      try {
        const csvText = await file.text();
        const { headers, records } = parseCSV(csvText);
        loadParsedCsv(headers, records, file.name);
      } catch (error) {
        el.statusText.className = 'status error';
        el.statusText.textContent = `Failed to load ${file.name}: ${error.message}`;
      }
    }

    function clearFilters() {
      el.search.value = '';
      el.promptNumber.value = '';
      el.llmFilter.value = '';
      el.categoryFilter.value = '';
      el.promptCategory.value = '';
      state.copyFeedback = '';
      state.copyFeedbackType = '';
    }

    function onGridResizeMove(clientY) {
      if (!state.isResizingGrid) return;

      const delta = clientY - state.resizeStartY;
      const newGridHeight = Math.max(220, state.resizeStartHeight + delta);
      const newDetailsHeight = Math.max(260, state.resizeStartDetailsHeight - delta);

      el.tableWrap.style.height = `${newGridHeight}px`;
      el.detailsPanel.style.height = `${newDetailsHeight}px`;
      ensureVisibleRowsForScroll();
    }

    function startGridResize(clientY) {
      state.isResizingGrid = true;
      state.resizeStartY = clientY;
      state.resizeStartHeight = el.tableWrap.getBoundingClientRect().height;
      state.resizeStartDetailsHeight = el.detailsPanel.getBoundingClientRect().height;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'ns-resize';
    }

    function stopGridResize() {
      if (!state.isResizingGrid) return;
      state.isResizingGrid = false;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    }

    function setCopyFeedback(message, type = '') {
      state.copyFeedback = message;
      state.copyFeedbackType = type;
      renderDetails();
    }

    async function copyActiveResponse(model, promptNumber) {
      const group = getSkillGroupByKey(state.activeSkillKey);
      if (!group) return;

      const rows = rowsForSkillPromptModel(group, promptNumber, model);
      const row = chooseBestRow(rows);
      const text = row ? normalize(row.response_text) : '';
      if (!text) {
        setCopyFeedback('No response text to copy.', 'error');
        return;
      }

      try {
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
          throw new Error('Clipboard API unavailable in this browser context.');
        }
        await navigator.clipboard.writeText(text);
        setCopyFeedback(`Copied ${model} response to clipboard.`, 'ok');
      } catch (error) {
        setCopyFeedback(`Copy failed: ${error.message}`, 'error');
      }
    }

    function setupEvents() {
      el.search.addEventListener('input', applyFilters);
      el.promptNumber.addEventListener('change', () => {
        state.copyFeedback = '';
        state.copyFeedbackType = '';
        applyFilters();
      });
      el.llmFilter.addEventListener('change', () => {
        state.copyFeedback = '';
        state.copyFeedbackType = '';
        applyFilters();
      });
      el.categoryFilter.addEventListener('change', applyFilters);
      el.promptCategory.addEventListener('change', applyFilters);

      el.pageSize.addEventListener('change', () => {
        state.pageSize = Number(el.pageSize.value);
        state.visibleCount = state.pageSize;
        render();
      });

      el.resetBtn.addEventListener('click', () => {
        clearFilters();
        applyFilters();
      });

      el.reloadDefaultBtn.addEventListener('click', () => {
        clearFilters();
        loadDefaultCsv();
      });

      el.csvFileInput.addEventListener('change', async () => {
        const file = el.csvFileInput.files?.[0];
        if (!file) return;
        await loadCsvFile(file);
        el.csvFileInput.value = '';
      });

      el.tableWrap.addEventListener('scroll', maybeLoadMoreRows);

      el.loadMoreBtn.addEventListener('click', () => {
        if (state.visibleCount >= state.filteredSkills.length) return;
        state.visibleCount = Math.min(state.filteredSkills.length, state.visibleCount + state.pageSize);
        render();
      });

      el.tableBody.addEventListener('click', event => {
        const rowEl = event.target.closest('tr[data-skill-key]');
        if (!rowEl) return;

        const newKey = rowEl.dataset.skillKey;
        if (!newKey) return;

        state.activeSkillKey = newKey;
        state.copyFeedback = '';
        state.copyFeedbackType = '';
        render();
      });

      el.details.addEventListener('click', event => {
        const tabBtn = event.target.closest('button[data-details-tab]');
        if (tabBtn) {
          state.activeDetailsTab = tabBtn.dataset.detailsTab || 'skill';
          renderDetails();
          return;
        }

        const modelBtn = event.target.closest('button[data-model-tab]');
        if (modelBtn) {
          const model = modelBtn.dataset.modelTab;
          const group = getSkillGroupByKey(state.activeSkillKey);
          if (!group || !model) return;

          const activePrompt = getActivePromptForGroup(group);
          const key = getModelStateKey(group.key, activePrompt);
          state.activeModelBySkillPrompt[key] = model;
          state.copyFeedback = '';
          state.copyFeedbackType = '';
          renderDetails();
          return;
        }

        const renderModeBtn = event.target.closest('button[data-render-mode]');
        if (renderModeBtn) {
          const group = getSkillGroupByKey(state.activeSkillKey);
          if (!group) return;

          const mode = renderModeBtn.dataset.renderMode || 'markdown';
          const model = renderModeBtn.dataset.renderModel || '';
          const prompt = renderModeBtn.dataset.renderPrompt || getActivePromptForGroup(group);

          setActiveRenderMode(group.key, prompt, model, mode);
          state.copyFeedback = '';
          state.copyFeedbackType = '';
          renderDetails();
          return;
        }

        const copyBtn = event.target.closest('button[data-copy-response]');
        if (copyBtn) {
          const model = copyBtn.dataset.copyModel || '';
          const prompt = copyBtn.dataset.copyPrompt || '';
          copyActiveResponse(model, prompt);
        }
      });

      el.details.addEventListener('change', event => {
        const promptSelect = event.target.closest('#detailsPromptSelect');
        if (!promptSelect) return;

        const group = getSkillGroupByKey(state.activeSkillKey);
        if (!group) return;

        const nextPrompt = normalize(promptSelect.value);
        state.activePromptBySkill[group.key] = nextPrompt;
        state.copyFeedback = '';
        state.copyFeedbackType = '';
        renderDetails();
      });

      el.panelDivider.addEventListener('mousedown', event => {
        event.preventDefault();
        startGridResize(event.clientY);
      });

      el.panelDivider.addEventListener('touchstart', event => {
        const touch = event.touches?.[0];
        if (!touch) return;
        startGridResize(touch.clientY);
      }, { passive: true });

      window.addEventListener('mousemove', event => {
        onGridResizeMove(event.clientY);
      });

      window.addEventListener('touchmove', event => {
        const touch = event.touches?.[0];
        if (!touch) return;
        onGridResizeMove(touch.clientY);
      }, { passive: true });

      window.addEventListener('mouseup', stopGridResize);
      window.addEventListener('touchend', stopGridResize);
      window.addEventListener('touchcancel', stopGridResize);
    }

    setupEvents();
    loadDefaultCsv();
})();
