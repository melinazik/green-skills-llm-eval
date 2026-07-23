    const DEFAULT_CSV_FILE = '../categorize/output_categorize/greenSkillsCollection_enhanced_with_thematic.csv';
    const CATEGORY_VALUES = [
      'G1 Renewable Energy & Energy Systems — generating renewable energy + storage/grid (solar, wind, biomass, geothermal, hydro, hydrogen, batteries, smart grid).',
      'G2 Energy Efficiency & Built Environment — reducing energy demand + green building/construction/retrofit (audits, heat pumps, insulation, HVAC, installation).',
      'G3 Circular Economy, Waste & Resource — reduce/reuse/recycle/recover; waste streams; resource & water efficiency; sustainable materials & textiles.',
      'G4 Environmental Protection & Natural Capital — protect air/water/soil/ecosystems/wildlife; pollution control, remediation, biodiversity, climate impact.',
      'G5 Sustainable Agriculture, Forestry & Food — sustainable crops/livestock/forestry/fisheries; soil & land management; food systems & food-waste.',
      'G6 Sustainable Mobility & Green Manufacturing — low-impact transport/mobility + clean/sustainable industrial production.',
      'G7 Green Management, Policy, Finance & Governance — environmental law/compliance/permits, policy, economics, ESG reporting, green finance, strategy, advisory.',
      'G8 Green Technology, Data & Digital Innovation — computing/data/sensing/AI applied to sustainability (green IT, environmental data, IoT, monitoring tech).',
    ];
    const UNASSIGNED_CATEGORY_FILTER_VALUE = '__unassigned__';

    const state = {
      headers: [],
      allRows: [],
      filteredRows: [],
      visibleCount: 25,
      pageSize: 25,
      activeRowId: null,
      sourceName: DEFAULT_CSV_FILE,
      selectionColumnName: 'selected',
      categoryColumnName: 'Category',
      isResizingGrid: false,
      resizeStartY: 0,
      resizeStartHeight: 0,
      resizeStartDetailsHeight: 0,
      isResizingColumn: false,
      resizeColumnKey: '',
      resizeColumnStartX: 0,
      resizeColumnStartWidth: 0,
      isResizingDetails: false,
      detailsResizeStartX: 0,
      detailsResizeStartWidth: 0,
    };

    const el = {
      sourceLabel: document.getElementById('sourceLabel'),
      search: document.getElementById('search'),
      labelFilter: document.getElementById('labelFilter'),
      descriptionFilter: document.getElementById('descriptionFilter'),
      skillType: document.getElementById('skillType'),
      reuseLevel: document.getElementById('reuseLevel'),
      status: document.getElementById('status'),
      selectionFilter: document.getElementById('selectionFilter'),
      escoLevel0Pillar: document.getElementById('escoPillar'),
      escoLevel1Category: document.getElementById('escoLevel1_Category'),
      thematicCategory: document.getElementById('thematicCategory'),
      customCategoryFilter: document.getElementById('customCategoryFilter'),
      pageSize: document.getElementById('pageSize'),
      csvFileInput: document.getElementById('csvFileInput'),
      reloadDefaultBtn: document.getElementById('reloadDefaultBtn'),
      clearSelectionBtn: document.getElementById('clearSelectionBtn'),
      exportAllBtn: document.getElementById('exportAllBtn'),
      exportSelectedBtn: document.getElementById('exportSelectedBtn'),
      resetBtn: document.getElementById('resetBtn'),
      tableBody: document.getElementById('tableBody'),
      tableWrap: document.getElementById('tableWrap'),
      detailsPanel: document.getElementById('detailsPanel'),
      pageInfo: document.getElementById('pageInfo'),
      stratifySelectBtn: document.getElementById('stratifySelectBtn'),
      statusText: document.getElementById('statusText'),
      details: document.getElementById('details'),
      selectAllCheckbox: document.getElementById('selectAllCheckbox'),
      workspace: document.getElementById('workspace'),
      colDivider: document.getElementById('colDivider'),
      sidePanel: document.getElementById('sidePanel'),
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
          // Ignore CR in CRLF
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
      const records = rows.slice(1)
        .filter(r => r.some(v => v !== ''))
        .map((r, idx) => {
          const obj = {};
          headers.forEach((h, colIdx) => {
            obj[h] = r[colIdx] ?? '';
          });
          const baseId = obj.conceptUri || obj.preferredLabel || 'row';
          obj.__id = `${baseId}__${idx}`;
          obj.__selected = false;
          return obj;
        });

      return { headers, records };
    }

    function parseSelectedValue(value) {
      const normalized = String(value ?? '').trim().toLowerCase();
      return ['true', '1', 'yes', 'y', 'x'].includes(normalized);
    }

    function uniqueValues(fieldName) {
      return [...new Set(state.allRows.map(r => (r[fieldName] || '').trim()).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b));
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

    function getRowById(rowId) {
      return state.allRows.find(r => r.__id === rowId) || null;
    }

    function setRowSelected(row, selected) {
      if (!row) return;
      row.__selected = Boolean(selected);

      const existingSelectionHeader = state.headers.find(
        h => h.trim().toLowerCase() === state.selectionColumnName.toLowerCase()
      );
      if (existingSelectionHeader) {
        row[existingSelectionHeader] = row.__selected ? 'true' : 'false';
      }
    }

    function selectedCount() {
      return state.allRows.reduce((count, row) => count + (row.__selected ? 1 : 0), 0);
    }

    function getCategoryHeader() {
      const match = state.headers.find(h => h.trim().toLowerCase() === state.categoryColumnName.toLowerCase());
      return match || state.categoryColumnName;
    }

    function getRowCategoryValue(row) {
      const header = getCategoryHeader();
      return (row[header] || '').trim();
    }

    function setRowCategoryValue(row, value) {
      const header = getCategoryHeader();
      row[header] = value;
    }

    function categoryFilterValues() {
      const valuesFromData = uniqueValues(getCategoryHeader());
      return [...new Set([...CATEGORY_VALUES, ...valuesFromData])];
    }

    function toCategoryShortLabel(value) {
      const text = String(value || '').trim();
      if (!text) return '';
      const idx = text.indexOf(' — ');
      return idx >= 0 ? text.slice(0, idx) : text;
    }

    function thematicToCategoryValue(thematicValue) {
      const normalized = String(thematicValue || '').trim();
      if (!normalized) return '';
      return CATEGORY_VALUES.includes(normalized) ? normalized : '';
    }

    function assignCategoryFromThematicForFilteredRows() {
      let assignedCount = 0;

      state.filteredRows.forEach(row => {
        const mappedValue = thematicToCategoryValue(row.thematicCategory || '');
        if (!mappedValue) return;

        const currentValue = getRowCategoryValue(row);
        if (currentValue === mappedValue) return;

        setRowCategoryValue(row, mappedValue);
        assignedCount += 1;
      });

      initFilters();
      render();

      return assignedCount;
    }

    function clearCategoryForFilteredRows() {
      let clearedCount = 0;

      state.filteredRows.forEach(row => {
        const currentValue = getRowCategoryValue(row);
        if (!currentValue) return;

        setRowCategoryValue(row, '');
        clearedCount += 1;
      });

      initFilters();
      render();

      return clearedCount;
    }

    function randomSample(rows, targetCount) {
      if (targetCount <= 0 || rows.length === 0) return [];
      if (rows.length <= targetCount) return [...rows];

      const copy = [...rows];
      for (let i = copy.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [copy[i], copy[j]] = [copy[j], copy[i]];
      }
      return copy.slice(0, targetCount);
    }

    function stratifySelectFilteredRows(perCategoryCount) {
      const grouped = new Map();

      state.filteredRows.forEach(row => {
        const category = getRowCategoryValue(row);
        if (!category) return;
        if (!grouped.has(category)) grouped.set(category, []);
        grouped.get(category).push(row);
      });

      let selectedCount = 0;
      let categoriesAffected = 0;

      grouped.forEach((rows, category) => {
        const sampled = randomSample(rows, perCategoryCount);
        sampled.forEach(row => {
          if (!row.__selected) {
            setRowSelected(row, true);
            selectedCount += 1;
          }
        });
        if (sampled.length > 0) categoriesAffected += 1;
      });

      render();

      return {
        selectedCount,
        categoriesAffected,
        totalCategories: grouped.size,
      };
    }

    function applyFilters() {
      const q = el.search.value.trim().toLowerCase();
      const labelQuery = el.labelFilter.value.trim().toLowerCase();
      const descriptionQuery = el.descriptionFilter.value.trim().toLowerCase();
      const skillType = el.skillType.value;
      const reuseLevel = el.reuseLevel.value;
      const status = el.status.value;
      const selectionFilter = el.selectionFilter.value;
      const escoLevel0Pillar = el.escoLevel0Pillar.value;
      const escoLevel1Category = el.escoLevel1Category.value;
      const thematicCategory = el.thematicCategory.value;
      const customCategoryFilter = el.customCategoryFilter.value;

      state.filteredRows = state.allRows.filter(row => {
        if (labelQuery && !(row.preferredLabel || '').toLowerCase().includes(labelQuery)) return false;
        if (descriptionQuery && !(row.description || '').toLowerCase().includes(descriptionQuery)) return false;
        if (skillType && row.skillType !== skillType) return false;
        if (reuseLevel && row.reuseLevel !== reuseLevel) return false;
        if (status && row.status !== status) return false;
        if (selectionFilter === 'selected' && !row.__selected) return false;
        if (escoLevel0Pillar && (row.escoLevel0Pillar || '') !== escoLevel0Pillar) return false;
        if (escoLevel1Category && (row.escoLevel1Category || '') !== escoLevel1Category) return false;
        if (thematicCategory && (row.thematicCategory || '') !== thematicCategory) return false;

        const rowCategory = getRowCategoryValue(row);
        if (customCategoryFilter === UNASSIGNED_CATEGORY_FILTER_VALUE && rowCategory) return false;
        if (customCategoryFilter && customCategoryFilter !== UNASSIGNED_CATEGORY_FILTER_VALUE && rowCategory !== customCategoryFilter) return false;

        if (!q) return true;

        const haystack = state.headers.map(h => row[h] || '').join(' ').toLowerCase();
        return haystack.includes(q);
      });

      state.visibleCount = state.pageSize;
      render();
    }

    function getVisibleRows() {
      return state.filteredRows.slice(0, state.visibleCount);
    }

    function ensureVisibleRowsForScroll() {
      const wrap = el.tableWrap;
      if (!wrap) return;

      let safety = 0;
      while (
        wrap.scrollHeight <= wrap.clientHeight + 4
        && state.visibleCount < state.filteredRows.length
        && safety < 10
      ) {
        state.visibleCount = Math.min(state.filteredRows.length, state.visibleCount + state.pageSize);
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

      if (state.visibleCount >= state.filteredRows.length) return;

      state.visibleCount = Math.min(state.filteredRows.length, state.visibleCount + state.pageSize);
      render();
    }

    function onGridResizeMove(clientY) {
      if (!state.isResizingGrid) return;

      const delta = clientY - state.resizeStartY;
      const newGridHeight = Math.max(220, state.resizeStartHeight + delta);
      const newDetailsHeight = Math.max(240, state.resizeStartDetailsHeight - delta);

      el.tableWrap.style.height = `${newGridHeight}px`;
      el.detailsPanel.style.height = `${newDetailsHeight}px`;
      ensureVisibleRowsForScroll();
    }

    function stopGridResize() {
      if (!state.isResizingGrid) return;
      state.isResizingGrid = false;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    }

    function startGridResize(clientY) {
      state.isResizingGrid = true;
      state.resizeStartY = clientY;
      state.resizeStartHeight = el.tableWrap.getBoundingClientRect().height;
      state.resizeStartDetailsHeight = el.detailsPanel.getBoundingClientRect().height;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'ns-resize';
    }

    function startColumnResize(columnKey, clientX) {
      const headerEl = document.querySelector(`th[data-col-key="${CSS.escape(columnKey)}"]`);
      if (!headerEl) return;

      state.isResizingColumn = true;
      state.resizeColumnKey = columnKey;
      state.resizeColumnStartX = clientX;
      state.resizeColumnStartWidth = headerEl.getBoundingClientRect().width;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
    }

    function onColumnResizeMove(clientX) {
      if (!state.isResizingColumn) return;

      const headerEl = document.querySelector(`th[data-col-key="${CSS.escape(state.resizeColumnKey)}"]`);
      if (!headerEl) return;

      const delta = clientX - state.resizeColumnStartX;
      const newWidth = Math.max(90, state.resizeColumnStartWidth + delta);
      headerEl.style.width = `${newWidth}px`;
    }

    function stopColumnResize() {
      if (!state.isResizingColumn) return;
      state.isResizingColumn = false;
      state.resizeColumnKey = '';
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    }

    function startDetailsResize(clientX) {
      state.isResizingDetails = true;
      state.detailsResizeStartX = clientX;
      state.detailsResizeStartWidth = el.sidePanel.getBoundingClientRect().width;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
    }

    function onDetailsResizeMove(clientX) {
      if (!state.isResizingDetails) return;
      // Side panel is on the right, so dragging left widens it.
      const delta = state.detailsResizeStartX - clientX;
      const newWidth = Math.min(520, Math.max(200, state.detailsResizeStartWidth + delta));
      el.workspace.style.setProperty('--details-width', `${newWidth}px`);
    }

    function stopDetailsResize() {
      if (!state.isResizingDetails) return;
      state.isResizingDetails = false;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }

    function renderTable() {
      const rows = getVisibleRows();

      if (rows.length === 0) {
        el.tableBody.innerHTML = '<tr><td colspan="7" class="empty">No entries match your filters.</td></tr>';
        return;
      }

      const categoryValues = categoryFilterValues();

      el.tableBody.innerHTML = rows.map(row => {
        const activeClass = row.__id === state.activeRowId ? 'active' : '';
        const rowCategory = getRowCategoryValue(row);
        const categoryOptions = categoryValues
          .map(value => {
            const shortLabel = toCategoryShortLabel(value);
            return `
              <option value="${escapeHtml(value)}" ${value === rowCategory ? 'selected' : ''} title="${escapeHtml(value)}">${escapeHtml(shortLabel)}</option>
            `;
          })
          .join('');

        return `
          <tr class="${activeClass}" data-id="${escapeHtml(row.__id)}">
            <td class="select-col">
              <input
                type="checkbox"
                data-toggle-select="true"
                data-id="${escapeHtml(row.__id)}"
                ${row.__selected ? 'checked' : ''}
                aria-label="Select ${escapeHtml(row.preferredLabel || row.__id)}"
              />
            </td>
            <td>${escapeHtml(row.preferredLabel || '')}</td>
            <td>${escapeHtml(row.skillType || '')}</td>
            <td>${escapeHtml(row.reuseLevel || '')}</td>
            <td>${escapeHtml(row.status || '')}</td>
            <td class="category-col">
              <select data-category-select="true" data-id="${escapeHtml(row.__id)}">
                <option value="" ${rowCategory ? '' : 'selected'}>Unassigned</option>
                ${categoryOptions}
              </select>
            </td>
            <td class="desc" title="${escapeHtml(row.description || '')}">${escapeHtml(row.description || '')}</td>
          </tr>
        `;
      }).join('');
    }

    function renderDetails() {
      const row = getRowById(state.activeRowId);
      if (!row) {
        el.details.className = 'empty';
        el.details.textContent = 'Click a row to inspect all fields. Use the checkbox to select/deselect it.';
        return;
      }

      el.details.className = '';
      const categoryHeader = getCategoryHeader();
      const headersWithoutSpecial = state.headers.filter(h => {
        const normalized = h.trim().toLowerCase();
        return normalized !== state.selectionColumnName.toLowerCase()
          && normalized !== categoryHeader.trim().toLowerCase();
      });
      const fields = ['selected', categoryHeader, ...headersWithoutSpecial];

      el.details.innerHTML = `
        <dl class="details-grid">
          ${fields.map(field => {
            let value = '';
            if (field === 'selected') {
              value = row.__selected ? 'true' : 'false';
            } else if (field.trim().toLowerCase() === categoryHeader.trim().toLowerCase()) {
              value = getRowCategoryValue(row);
            } else {
              value = row[field] || '';
            }

            return `
              <dt>${escapeHtml(field)}</dt>
              <dd>${escapeHtml(value)}</dd>
            `;
          }).join('')}
        </dl>
      `;
    }

    function renderPaginationAndStatus() {
      const total = state.filteredRows.length;
      const visible = Math.min(state.visibleCount, total);
      const selected = selectedCount();

      el.pageInfo.textContent = `Showing ${visible} of ${total} filtered entries`;

      el.statusText.className = 'status ok';
      el.statusText.textContent = `${state.allRows.length} total entries loaded • ${selected} selected`;

      el.exportAllBtn.disabled = state.allRows.length === 0;
      el.exportSelectedBtn.disabled = selected === 0;
      el.clearSelectionBtn.disabled = selected === 0;
      el.stratifySelectBtn.disabled = state.filteredRows.length === 0;

      // Reflect how many of the filtered rows are ticked in the header checkbox.
      const filteredSelected = state.filteredRows.reduce((n, row) => n + (row.__selected ? 1 : 0), 0);
      el.selectAllCheckbox.disabled = total === 0;
      el.selectAllCheckbox.checked = total > 0 && filteredSelected === total;
      el.selectAllCheckbox.indeterminate = filteredSelected > 0 && filteredSelected < total;
    }

    function render() {
      renderTable();
      renderDetails();
      renderPaginationAndStatus();
      ensureVisibleRowsForScroll();
    }

    function initFilters() {
      const previousValues = {
        skillType: el.skillType.value,
        reuseLevel: el.reuseLevel.value,
        status: el.status.value,
        escoLevel0Pillar: el.escoLevel0Pillar.value,
        escoLevel1Category: el.escoLevel1Category.value,
        thematicCategory: el.thematicCategory.value,
        customCategoryFilter: el.customCategoryFilter.value,
      };

      fillFilter(el.skillType, uniqueValues('skillType'));
      fillFilter(el.reuseLevel, uniqueValues('reuseLevel'));
      fillFilter(el.status, uniqueValues('status'));
      fillFilter(el.escoLevel0Pillar, uniqueValues('escoLevel0Pillar'));
      fillFilter(el.escoLevel1Category, uniqueValues('escoLevel1Category'));
      fillFilter(el.thematicCategory, uniqueValues('thematicCategory'), toCategoryShortLabel);

      fillFilter(el.customCategoryFilter, categoryFilterValues(), toCategoryShortLabel);
      const unassignedOption = document.createElement('option');
      unassignedOption.value = UNASSIGNED_CATEGORY_FILTER_VALUE;
      unassignedOption.textContent = 'Unassigned';
      el.customCategoryFilter.insertBefore(unassignedOption, el.customCategoryFilter.children[1] || null);
      el.customCategoryFilter.disabled = false;

      const restoreValue = (selectEl, value) => {
        if (!value) {
          selectEl.value = '';
          return;
        }
        const hasValue = [...selectEl.options].some(opt => opt.value === value);
        selectEl.value = hasValue ? value : '';
      };

      restoreValue(el.skillType, previousValues.skillType);
      restoreValue(el.reuseLevel, previousValues.reuseLevel);
      restoreValue(el.status, previousValues.status);
      restoreValue(el.escoLevel0Pillar, previousValues.escoLevel0Pillar);
      restoreValue(el.escoLevel1Category, previousValues.escoLevel1Category);
      restoreValue(el.thematicCategory, previousValues.thematicCategory);
      restoreValue(el.customCategoryFilter, previousValues.customCategoryFilter);
    }

    function csvEscape(value) {
      const str = String(value ?? '');
      const escaped = str.replaceAll('"', '""');
      if (/[",\n\r]/.test(str)) {
        return `"${escaped}"`;
      }
      return escaped;
    }

    function buildExportCsv(includeOnlySelected) {
      const rows = includeOnlySelected
        ? state.allRows.filter(row => row.__selected)
        : state.allRows;

      const hasSelectionHeader = state.headers.some(
        h => h.trim().toLowerCase() === state.selectionColumnName.toLowerCase()
      );
      const categoryHeader = getCategoryHeader();
      const hasCategoryHeader = state.headers.some(
        h => h.trim().toLowerCase() === categoryHeader.trim().toLowerCase()
      );

      let exportHeaders = hasSelectionHeader
        ? [...state.headers]
        : [...state.headers, state.selectionColumnName];

      if (!hasCategoryHeader) {
        exportHeaders.push(categoryHeader);
      }

      exportHeaders = [...new Set(exportHeaders)];

      const lines = [exportHeaders.map(csvEscape).join(',')];

      rows.forEach(row => {
        const values = exportHeaders.map(header => {
          const normalized = header.trim().toLowerCase();
          if (normalized === state.selectionColumnName.toLowerCase()) {
            return row.__selected ? 'true' : 'false';
          }
          if (normalized === categoryHeader.trim().toLowerCase()) {
            return getRowCategoryValue(row);
          }
          return row[header] ?? '';
        });

        lines.push(values.map(csvEscape).join(','));
      });

      return lines.join('\r\n');
    }

    function downloadCsv(filename, content) {
      const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function baseFileName(name) {
      return name.replace(/\.csv$/i, '');
    }

    function exportRows(includeOnlySelected) {
      const csvContent = buildExportCsv(includeOnlySelected);
      const suffix = includeOnlySelected ? '_selected' : '_all';
      downloadCsv(`${baseFileName(state.sourceName)}${suffix}.csv`, csvContent);
    }

    function loadParsedCsv(headers, records, sourceName) {
      if (!headers.length) {
        throw new Error('CSV has no header row.');
      }

      const selectionHeader = headers.find(h => h.trim().toLowerCase() === 'selected');
      const categoryHeader = headers.find(h => h.trim().toLowerCase() === state.categoryColumnName.toLowerCase()) || state.categoryColumnName;
      state.selectionColumnName = selectionHeader || 'selected';

      const normalizedHeaders = headers.map(h => h.trim().toLowerCase());
      if (!normalizedHeaders.includes(categoryHeader.trim().toLowerCase())) {
        headers.push(categoryHeader);
      }

      records.forEach(row => {
        const selectedValue = selectionHeader ? row[selectionHeader] : '';
        row.__selected = selectionHeader ? parseSelectedValue(selectedValue) : false;

        if (selectionHeader) {
          row[selectionHeader] = row.__selected ? 'true' : 'false';
        }

        const existingCategoryValue = (row[categoryHeader] || '').trim();
        // Prefill the editable group from the Step 2 thematic group when empty.
        row[categoryHeader] = existingCategoryValue || thematicToCategoryValue(row.thematicCategory || '');
      });

      el.selectionFilter.value = 'all';
      el.escoLevel0Pillar.value = '';
      el.escoLevel1Category.value = '';
      el.thematicCategory.value = '';
      el.customCategoryFilter.value = '';

      const shouldSortByLabel = headers.includes('preferredLabel');
      state.headers = headers;
      state.allRows = shouldSortByLabel
        ? records.sort((a, b) => (a.preferredLabel || '').localeCompare(b.preferredLabel || ''))
        : records;

      state.filteredRows = [...state.allRows];
      state.visibleCount = state.pageSize;
      state.activeRowId = null;
      state.sourceName = sourceName;
      el.sourceLabel.textContent = sourceName;

      initFilters();
      render();
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
        el.statusText.className = 'status';
        el.statusText.textContent = `Failed to load CSV: ${error.message}`;
        el.tableBody.innerHTML = `
          <tr>
            <td colspan="7" class="empty">
              Could not load <code>${DEFAULT_CSV_FILE}</code>.<br>
              Start a local server in this folder and open the page through HTTP (not file://).<br>
              Example: <code>python3 -m http.server 8000</code>
            </td>
          </tr>
        `;
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
        el.statusText.className = 'status';
        el.statusText.textContent = `Failed to load ${file.name}: ${error.message}`;
      }
    }

    function setupEvents() {
      el.search.addEventListener('input', applyFilters);
      el.labelFilter.addEventListener('input', applyFilters);
      el.descriptionFilter.addEventListener('input', applyFilters);
      el.skillType.addEventListener('change', applyFilters);
      el.reuseLevel.addEventListener('change', applyFilters);
      el.status.addEventListener('change', applyFilters);
      el.selectionFilter.addEventListener('change', applyFilters);
      el.escoLevel0Pillar.addEventListener('change', applyFilters);
      el.escoLevel1Category.addEventListener('change', applyFilters);
      el.thematicCategory.addEventListener('change', applyFilters);
      el.customCategoryFilter.addEventListener('change', applyFilters);

      el.pageSize.addEventListener('change', () => {
        state.pageSize = Number(el.pageSize.value);
        state.visibleCount = state.pageSize;
        render();
      });

      el.resetBtn.addEventListener('click', event => {
        // The button sits inside the Filters <summary>; stop it toggling the panel.
        event.preventDefault();
        event.stopPropagation();
        el.search.value = '';
        el.labelFilter.value = '';
        el.descriptionFilter.value = '';
        el.skillType.value = '';
        el.reuseLevel.value = '';
        el.status.value = '';
        el.selectionFilter.value = 'all';
        el.escoLevel0Pillar.value = '';
        el.escoLevel1Category.value = '';
        el.thematicCategory.value = '';
        el.customCategoryFilter.value = '';
        state.activeRowId = null;
        applyFilters();
      });

      el.tableWrap.addEventListener('scroll', maybeLoadMoreRows);

      el.colDivider.addEventListener('mousedown', event => {
        event.preventDefault();
        startDetailsResize(event.clientX);
      });

      el.colDivider.addEventListener('touchstart', event => {
        const touch = event.touches?.[0];
        if (!touch) return;
        startDetailsResize(touch.clientX);
      }, { passive: true });

      window.addEventListener('mousemove', event => {
        onGridResizeMove(event.clientY);
        onColumnResizeMove(event.clientX);
        onDetailsResizeMove(event.clientX);
      });

      window.addEventListener('touchmove', event => {
        const touch = event.touches?.[0];
        if (!touch) return;
        onGridResizeMove(touch.clientY);
        onColumnResizeMove(touch.clientX);
        onDetailsResizeMove(touch.clientX);
      }, { passive: true });

      window.addEventListener('mouseup', () => {
        stopGridResize();
        stopColumnResize();
        stopDetailsResize();
      });
      window.addEventListener('touchend', () => {
        stopGridResize();
        stopColumnResize();
        stopDetailsResize();
      });
      window.addEventListener('touchcancel', () => {
        stopGridResize();
        stopColumnResize();
        stopDetailsResize();
      });

      el.tableWrap.addEventListener('mousedown', event => {
        const handle = event.target.closest('[data-col-resize]');
        if (!handle) return;
        event.preventDefault();
        event.stopPropagation();
        startColumnResize(handle.dataset.colResize, event.clientX);
      });

      el.tableWrap.addEventListener('touchstart', event => {
        const handle = event.target.closest('[data-col-resize]');
        if (!handle) return;
        const touch = event.touches?.[0];
        if (!touch) return;
        event.stopPropagation();
        startColumnResize(handle.dataset.colResize, touch.clientX);
      }, { passive: true });

      el.tableBody.addEventListener('change', event => {
        const checkbox = event.target.closest('input[data-toggle-select]');
        if (checkbox) {
          const row = getRowById(checkbox.dataset.id);
          setRowSelected(row, checkbox.checked);

          if (el.selectionFilter.value === 'selected') {
            applyFilters();
          } else {
            render();
          }
          return;
        }

        const categorySelect = event.target.closest('select[data-category-select]');
        if (categorySelect) {
          const row = getRowById(categorySelect.dataset.id);
          if (!row) return;

          setRowCategoryValue(row, categorySelect.value);
          initFilters();

          if (
            el.customCategoryFilter.value
            || el.customCategoryFilter.value === UNASSIGNED_CATEGORY_FILTER_VALUE
          ) {
            applyFilters();
          } else {
            render();
          }
        }
      });

      el.tableBody.addEventListener('click', event => {
        if (event.target.closest('input[data-toggle-select]')) return;
        if (event.target.closest('select[data-category-select]')) return;
        if (event.target.closest('[data-col-resize]')) return;

        const rowEl = event.target.closest('tr[data-id]');
        if (!rowEl) return;

        state.activeRowId = rowEl.dataset.id;
        renderDetails();
        renderPaginationAndStatus();
        renderTable();
      });

      el.clearSelectionBtn.addEventListener('click', () => {
        state.allRows.forEach(row => setRowSelected(row, false));
        applyFilters();
      });

      el.selectAllCheckbox.addEventListener('change', () => {
        const shouldSelect = el.selectAllCheckbox.checked;
        state.filteredRows.forEach(row => setRowSelected(row, shouldSelect));
        if (el.selectionFilter.value === 'selected') {
          applyFilters();
        } else {
          render();
        }
      });

      el.stratifySelectBtn.addEventListener('click', () => {
        const input = window.prompt(
          `How many entries should be selected per category among the ${state.filteredRows.length} filtered rows?`,
          '5'
        );
        if (input === null) return;

        const perCategoryCount = Number.parseInt(input, 10);
        if (!Number.isFinite(perCategoryCount) || perCategoryCount < 0) {
          window.alert('Please enter a whole number 0 or greater.');
          return;
        }

        const confirmed = window.confirm(
          `Stratify-select up to ${perCategoryCount} row${perCategoryCount === 1 ? '' : 's'} per category across filtered rows?\n\nIf a category has fewer rows, all rows in that category will be selected.`
        );
        if (!confirmed) return;

        const result = stratifySelectFilteredRows(perCategoryCount);
        el.statusText.className = 'status ok';
        el.statusText.textContent = `Stratify selected ${result.selectedCount} row${result.selectedCount === 1 ? '' : 's'} across ${result.categoriesAffected}/${result.totalCategories} categor${result.totalCategories === 1 ? 'y' : 'ies'}.`;
      });

      el.exportAllBtn.addEventListener('click', () => {
        exportRows(false);
      });

      el.exportSelectedBtn.addEventListener('click', () => {
        exportRows(true);
      });

      el.reloadDefaultBtn.addEventListener('click', () => {
        loadDefaultCsv();
      });

      el.csvFileInput.addEventListener('change', async () => {
        const file = el.csvFileInput.files?.[0];
        if (!file) return;
        await loadCsvFile(file);
        el.csvFileInput.value = '';
      });
    }

    setupEvents();
    loadDefaultCsv();
