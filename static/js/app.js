/* DataMedic front end — vanilla JS. All data is rendered with textContent / DOM
   nodes (never innerHTML), because uploaded cell values are untrusted. */
(() => {
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const fmt = n => (n === null || n === undefined) ? '—' : Number(n).toLocaleString();
const DEFAULT_MAX = 524288000;                       // 500 MB — overwritten by /api/config

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, '');
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return e;
}
const clear = n => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const live = msg => { $('#live').textContent = ''; setTimeout(() => { $('#live').textContent = msg; }, 30); };

function humanSize(n) {
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB']; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 2 : 1)} ${u[i]}`;
}
function clock(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  const p = x => String(x).padStart(2, '0');
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}

class ApiError extends Error {
  constructor(message, code, status) { super(message); this.code = code; this.status = status; }
}
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, opts); }
  catch { throw new ApiError('Could not reach the DataMedic server. Check that it is running and try again.', 'network', 0); }
  let data = null;
  try { data = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) throw new ApiError(data?.error?.message || `The server returned an error (${r.status}).`, data?.error?.code, r.status);
  return data;
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

/* ------------------------------------------------------------------ state */
const S = {
  config: { max_file_bytes: DEFAULT_MAX, allowed_extensions: ['.csv', '.xlsx', '.xls'] },
  file: null, job: null, analysis: null, preview: null, previewMode: 'inline',
  view: 'upload', pollTimer: null, tickTimer: null, timerBase: 0, timerAt: 0, xhr: null, lastPlan: null,
};
const KEY = 'datamedic.job';

/* ------------------------------------------------------------------ views */
function show(view) {
  S.view = view;
  for (const v of ['upload', 'station', 'export']) $(`#view-${v}`).hidden = v !== view;
  updateStepper();
  const h = { upload: '#h-upload', station: '#h-station', export: '#h-export' }[view];
  $(h)?.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}
function updateStepper() {
  const j = S.job, st = j?.state;
  const analysisReady = j && j.analysis_ready;
  const enabled = {
    upload: !j || ['uploaded', 'analyzing'].includes(st) || (st === 'failed' && j.failed_stage === 'analysis'),
    station: !!analysisReady && st !== 'processing',
    export: !!j && (['processing', 'completed'].includes(st) || (st === 'failed' && j.failed_stage === 'processing')),
  };
  const done = { upload: !!analysisReady, station: st === 'completed', export: false };
  for (const b of $$('.step')) {
    const k = b.dataset.step;
    b.disabled = !enabled[k] && S.view !== k;
    b.classList.toggle('done', done[k] && S.view !== k);
    if (S.view === k) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
  }
}
$('#stepper').addEventListener('click', e => {
  const b = e.target.closest('.step'); if (!b || b.disabled) return;
  const k = b.dataset.step;
  if (k === 'station' && S.analysis) { show('station'); renderCompletedBanner(); }
  else if (k === 'export') { show('export'); renderExport(); }
  else if (k === 'upload') show('upload');
});

/* ------------------------------------------------------------------ stage 1 */
const dz = $('#dropzone'), input = $('#file-input');
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('drag'); }));
dz.addEventListener('drop', e => { if (e.dataTransfer.files.length) chooseFile(e.dataTransfer.files[0]); });
dz.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
input.addEventListener('change', () => { if (input.files.length) chooseFile(input.files[0]); input.value = ''; });
$('#btn-clear-file').addEventListener('click', () => { S.file = null; hideUploadError(); renderFile(); });

function ext(name) { const i = name.lastIndexOf('.'); return i < 0 ? '' : name.slice(i).toLowerCase(); }

function chooseFile(f) {
  hideUploadError();
  const max = S.config.max_file_bytes;
  const e = ext(f.name);
  if (!S.config.allowed_extensions.includes(e)) return showUploadError('Unsupported file type', `“${e || 'no extension'}” files are not supported. Choose a .csv, .xlsx or .xls file.`, false);
  if (f.size === 0) return showUploadError('Empty file', 'This file is empty (0 bytes).', false);
  if (f.size > max) return showUploadError('File too large', `This file is ${humanSize(f.size)}. The maximum is ${humanSize(max)} (${fmt(max)} bytes).`, false);
  S.file = f;
  renderFile();
}
function renderFile() {
  const f = S.file;
  $('#file-card').hidden = !f;
  $('#btn-analyze').disabled = !f;
  if (!f) return;
  const e = ext(f.name).slice(1);
  $('#file-name').textContent = f.name;
  $('#file-name').title = f.name;
  $('#file-size').textContent = `${humanSize(f.size)} (${fmt(f.size)} bytes)`;
  $('#file-type').textContent = { csv: 'CSV (comma-separated text)', xlsx: 'Excel workbook (.xlsx)', xls: 'Legacy Excel workbook (.xls)' }[e];
  $('#file-icon').textContent = e.toUpperCase();
  $('#file-icon').dataset.kind = e;
}
let retryAction = null;
function showUploadError(title, text, retry) {
  $('#upload-error-title').textContent = title;
  $('#upload-error-text').textContent = text;
  $('#upload-error').hidden = false;
  $('#btn-retry').hidden = !retry;
  retryAction = retry || null;
  live(`${title}. ${text}`);
}
function hideUploadError() { $('#upload-error').hidden = true; retryAction = null; }
$('#btn-retry').addEventListener('click', () => { if (retryAction) retryAction(); });

function setBusy(busy) {
  $('#btn-analyze').disabled = busy || !S.file;
  $('#dropzone').setAttribute('aria-disabled', busy);
  input.disabled = busy;
  $('#btn-clear-file').disabled = busy;
}

$('#btn-analyze').addEventListener('click', startUpload);

function startUpload() {
  if (!S.file) return;
  hideUploadError();
  setBusy(true);
  $('#upload-progress').hidden = false;
  $('#btn-cancel-analysis').hidden = false;
  $('#btn-cancel-analysis').onclick = () => { if (S.xhr) { S.xhr.abort(); } };
  setProgress('Uploading…', 0, 'Sending the file to the server.');
  const fd = new FormData();
  fd.append('file', S.file, S.file.name);
  const xhr = new XMLHttpRequest();
  S.xhr = xhr;
  xhr.open('POST', '/api/jobs');
  const t0 = performance.now();
  xhr.upload.onprogress = e => {
    if (!e.lengthComputable) return;
    const pct = Math.floor(100 * e.loaded / e.total);
    const sec = (performance.now() - t0) / 1000;
    const speed = sec > 0.5 ? ` · ${humanSize(e.loaded / sec)}/s` : '';
    setProgress('Uploading…', pct, `${humanSize(e.loaded)} of ${humanSize(e.total)}${speed}`);
    if (e.loaded >= e.total) setProgress('Upload complete — validating on the server…', 100, 'Checking the file format and size.', true);
  };
  xhr.onload = () => {
    S.xhr = null;
    let data = null; try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    if (xhr.status === 201 && data) {
      S.job = data; localStorage.setItem(KEY, data.id);
      $('#upload-bar').removeAttribute('value');
      pollAnalysis();
    } else {
      endUploadUI();
      showUploadError('Upload failed', data?.error?.message || `The server rejected the upload (${xhr.status}).`, startUpload);
    }
  };
  xhr.onerror = () => { S.xhr = null; endUploadUI(); showUploadError('Upload failed', `The connection was interrupted. This also happens when a file is larger than the ${humanSize(S.config.max_file_bytes)} limit. Check your network and try again.`, startUpload); };
  xhr.onabort = () => { S.xhr = null; endUploadUI(); showUploadError('Upload cancelled', 'You cancelled the upload. Nothing was kept.', startUpload); };
  xhr.send(fd);
}
function setProgress(phase, pct, detail, indeterminate) {
  $('#upload-phase').textContent = phase;
  const bar = $('#upload-bar');
  if (indeterminate) { bar.removeAttribute('value'); $('#upload-pct').textContent = ''; }
  else { bar.value = pct; $('#upload-pct').textContent = `${pct}%`; }
  $('#upload-detail').textContent = detail || '';
}
function endUploadUI() {
  setBusy(false);
  $('#upload-progress').hidden = true;
  $('#btn-cancel-analysis').hidden = true;
}

function stopPolling() { clearTimeout(S.pollTimer); S.pollTimer = null; }

async function pollAnalysis() {
  stopPolling();
  setBusy(true);
  $('#upload-progress').hidden = false;
  $('#btn-cancel-analysis').hidden = false;
  $('#btn-cancel-analysis').onclick = async () => { try { await post(`/api/jobs/${S.job.id}/cancel`); } catch { /* ignore */ } };
  try {
    const j = await api(`/api/jobs/${S.job.id}`);
    S.job = j;
    if (j.state === 'analyzing' || j.state === 'uploaded') {
      const p = j.progress || {};
      const rows = p.done ? `${fmt(p.done)} rows scanned · ` : '';
      setProgress(p.label || 'Analyzing…', 0, `${rows}elapsed ${clock(j.elapsed_seconds || 0)}`, true);
      S.pollTimer = setTimeout(pollAnalysis, 700);
    } else if (j.state === 'failed') {
      endUploadUI();
      showUploadError('Analysis failed', j.error?.message || 'The file could not be analysed.', async () => {
        hideUploadError();
        try { S.job = await post(`/api/jobs/${S.job.id}/analyze`); pollAnalysis(); }
        catch (e) { showUploadError('Could not restart', e.message, null); }
      });
      updateStepper();
    } else { // analyzed / processing / completed
      endUploadUI();
      await enterStation();
    }
  } catch (e) {
    if (e.status === 404) { endUploadUI(); resetAll(); showUploadError('Job expired', 'That job no longer exists. Please upload the file again.', null); }
    else { setProgress('Connection problem — retrying…', 0, e.message, true); S.pollTimer = setTimeout(pollAnalysis, 2000); }
  }
}

/* ------------------------------------------------------------------ stage 2 */
async function enterStation() {
  S.analysis = await api(`/api/jobs/${S.job.id}/analysis`);
  S.preview = null;
  renderStationHeader();
  renderMetrics();
  renderIssues();
  renderColumns();
  buildForm();
  if (S.job.raw_plan) { applyPlanToForm(S.job.raw_plan); S.lastPlan = S.job.raw_plan; }
  show('station');
  renderCompletedBanner();
  switchTab('diagnosis');
  if (S.job.state !== 'processing') schedulePreview(0);
  live('Analysis complete.');
}

function renderStationHeader() {
  const f = S.analysis.file, s = S.analysis.summary;
  const bits = [f.name, humanSize(f.size), f.kind.toUpperCase()];
  if (f.sheet) bits.push(`sheet “${f.sheet}”`);
  if (f.kind === 'csv') bits.push(`${f.encoding}, delimiter ${f.delimiter === '\t' ? 'TAB' : '“' + f.delimiter + '”'}`);
  $('#st-file').textContent = bits.join(' · ') + ` · scanned in ${s.scan_seconds}s`;
}
function renderCompletedBanner() {
  $('#completed-banner').hidden = !(S.job && S.job.state === 'completed');
}
$('#btn-view-result').addEventListener('click', () => { show('export'); renderExport(); });

function metric(label, value, sub, tone) {
  return el('div', { class: 'metric', 'data-tone': tone || null }, el('div', { class: 'l', text: label }), el('div', { class: 'v', text: value }), sub ? el('div', { class: 's', text: sub }) : null);
}
function renderMetrics() {
  const a = S.analysis, s = a.summary, cols = a.columns;
  const sum = f => cols.reduce((t, c) => t + f(c), 0);
  const ws = sum(c => c.whitespace_edge);
  const numCols = cols.filter(c => c.numeric.candidate && c.numeric.needs_cleaning).length;
  const dateCols = cols.filter(c => c.date.candidate && c.date.needs_normalization).length;
  const badEmail = sum(c => c.email.invalid);
  const blankNames = cols.filter(c => c.name_blank || c.name_duplicate).length;
  const ic = a.issue_counts;
  const box = clear($('#metrics'));
  box.append(
    metric('Rows', fmt(s.rows)), metric('Columns', fmt(s.columns)),
    metric('Missing cells', fmt(s.missing_cells), `${s.missing_pct}% of all cells`, s.missing_cells ? 'bad' : 'good'),
    metric('Duplicate rows', fmt(s.duplicate_rows), s.duplicate_rows_after_trim > s.duplicate_rows ? `+${fmt(s.duplicate_rows_after_trim - s.duplicate_rows)} after trimming` : 'exact matches', s.duplicate_rows ? 'bad' : 'good'),
    metric('Whitespace cells', fmt(ws), 'leading/trailing', ws ? 'warn' : 'good'),
    metric('Numbers as text', fmt(numCols), 'column(s)', numCols ? 'warn' : 'good'),
    metric('Date formats to fix', fmt(dateCols), 'column(s)', dateCols ? 'warn' : 'good'),
    metric('Invalid emails', fmt(badEmail), '', badEmail ? 'bad' : 'good'),
    metric('Bad column names', fmt(blankNames), 'blank or duplicate', blankNames ? 'bad' : 'good'),
    metric('Issues found', `${ic.confirmed || 0} / ${ic.likely || 0} / ${ic.suggestion || 0}`, 'confirmed / likely / suggestions'),
  );
  $('#count-issues').textContent = String((a.issues || []).length);
}

const SEV = {
  confirmed: ['Confirmed issues', 'Verified directly in your data.'],
  likely: ['Likely issues', 'Probably problems, but they need your judgement. Nothing is changed automatically.'],
  suggestion: ['Suggestions', 'Optional improvements and informational notes.'],
};
function renderIssues() {
  const box = clear($('#panel-diagnosis'));
  const issues = S.analysis.issues;
  if (!issues.length) {
    box.append(el('div', { class: 'empty-state' }, el('strong', { text: 'No issues detected' }), 'Your data looks clean. You can still download a normalized copy.'));
    return;
  }
  for (const sev of ['confirmed', 'likely', 'suggestion']) {
    const list = issues.filter(i => i.severity === sev);
    if (!list.length) continue;
    const g = el('section', { class: 'issue-group' },
      el('h3', {}, el('span', { class: `badge badge-${sev}`, text: sev }), `${SEV[sev][0]} (${list.length})`),
      el('p', { class: 'hint', text: SEV[sev][1] }));
    for (const i of list) {
      const card = el('article', { class: 'issue', 'data-sev': sev },
        el('div', { class: 'issue-title', text: i.title }),
        el('dl', {},
          el('dt', { text: 'Found' }), el('dd', { text: i.detail }),
          el('dt', { text: 'Why it matters' }), el('dd', { text: i.why }),
          el('dt', { text: 'Treatment' }), el('dd', { text: i.treatment })));
      if (i.treatment_key && controlFor(i.treatment_key)) {
        card.append(el('button', { type: 'button', class: 'btn btn-small jump', onclick: () => focusControl(i.treatment_key) }, 'Go to treatment'));
      }
      g.append(card);
    }
    box.append(g);
  }
}
function controlFor(key) { return $(`#opt-${key}`) || (key === 'missing' ? $('#opt-missing') : null); }
function focusControl(key) {
  const c = controlFor(key); if (!c) return;
  c.scrollIntoView({ block: 'center' });
  c.focus();
}

function renderColumns() {
  const cols = S.analysis.columns;
  const rows = cols.map(c => {
    const flags = [];
    if (c.name_blank) flags.push('blank name'); if (c.name_duplicate) flags.push('duplicate name');
    if (c.whitespace_edge) flags.push(`${fmt(c.whitespace_edge)} edge spaces`);
    if (c.placeholders) flags.push(`${fmt(c.placeholders)} placeholders`);
    if (c.numeric.protected_reason) flags.push('kept as text: ' + c.numeric.protected_reason);
    if (c.date.candidate) flags.push('dates' + (c.date.order === 'ambiguous' ? ' (ambiguous order)' : c.date.order === 'conflict' ? ' (conflicting order)' : ''));
    if (c.email.is_email && c.email.invalid) flags.push(`${fmt(c.email.invalid)} invalid emails`);
    if (c.constant) flags.push('constant'); if (c.empty) flags.push('empty');
    return el('tr', {},
      el('td', { class: 'n', text: c.index + 1 }),
      el('td', { text: c.name || '(blank)' }),
      el('td', { text: c.inferred_type }),
      el('td', { class: 'n', text: `${fmt(c.missing)} (${c.missing_pct}%)` }),
      el('td', { class: 'mono', text: c.examples.join(' · ') }),
      el('td', { text: flags.join('; ') }));
  });
  clear($('#panel-columns')).append(el('div', { class: 'table-wrap' }, el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, ...['#', 'Column', 'Detected type', 'Missing', 'Examples', 'Notes'].map(t => el('th', { text: t })))),
    el('tbody', {}, rows))));
}

/* tabs */
function switchTab(name) {
  for (const t of ['diagnosis', 'preview', 'columns']) {
    const on = t === name;
    $(`#tab-${t}`).setAttribute('aria-selected', on); $(`#tab-${t}`).tabIndex = on ? 0 : -1;
    $(`#panel-${t}`).hidden = !on;
  }
}
$$('.tab').forEach(b => b.addEventListener('click', () => switchTab(b.id.replace('tab-', ''))));
$('.tabs').addEventListener('keydown', e => {
  const tabs = $$('.tab'); const i = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
  let n = null;
  if (e.key === 'ArrowRight') n = (i + 1) % tabs.length; else if (e.key === 'ArrowLeft') n = (i - 1 + tabs.length) % tabs.length;
  if (n !== null) { e.preventDefault(); tabs[n].focus(); switchTab(tabs[n].id.replace('tab-', '')); }
});

/* ------------------------------------------------------------------ treatments form */
const STRATEGY_INFO = {
  preserve: ['Keep missing values as they are', 'Safest choice. Nothing is removed or invented.'],
  drop_rows: ['Drop rows containing missing values', 'Removes every row that has a missing value in the selected columns. This can discard a lot of data. Your original file is not changed.'],
  fill_text: ['Fill with text I provide', 'Every missing cell in the selected columns gets the same text. Use a clear label such as “Unknown”.'],
  fill_zero: ['Fill numeric columns with 0', 'Only purely numeric columns are filled. Zero is not the same as “unknown”; it can distort averages.'],
  mean: ['Fill numeric columns with the mean', 'Replaces missing numbers with the column average (6 decimals). Invents values and is sensitive to outliers; imputed cells are not flagged in the output.'],
  median: ['Fill numeric columns with the median', 'Replaces missing numbers with the middle value. More robust to outliers than the mean, but still invents values.'],
  mode: ['Fill with the most frequent value', 'Works for any column. Ties are broken alphabetically. Invents values.'],
};

function optCheckbox(key, label, hint, checked, disabled) {
  const id = `opt-${key}`;
  return el('div', { class: 'opt' },
    el('input', { type: 'checkbox', id, checked: checked, disabled: disabled }),
    el('label', { for: id }, label, hint ? el('span', { class: 'hint', text: hint }) : null));
}

function buildForm() {
  const a = S.analysis, cols = a.columns, s = a.summary, f = $('#treat-form');
  const sum = k => cols.reduce((t, c) => t + c[k], 0);
  const numCand = cols.filter(c => c.numeric.candidate);
  const dateCand = cols.filter(c => c.date.candidate);
  const ambiguous = dateCand.filter(c => c.date.order === 'ambiguous');
  const emailCols = cols.filter(c => c.email.is_email);
  clear(f);

  f.append(el('fieldset', {}, el('legend', { text: 'Cleanup' }),
    optCheckbox('trim', 'Trim whitespace', `${fmt(sum('whitespace_edge'))} cell(s) have leading/trailing spaces`, true),
    optCheckbox('collapse_spaces', 'Collapse repeated whitespace', 'Turns runs of spaces/tabs/newlines inside text into one space', false),
    optCheckbox('normalize_columns', 'Normalize column names', 'lowercase snake_case, unique, never blank', true),
    optCheckbox('remove_duplicates', 'Remove duplicate rows', `${fmt(s.duplicate_rows)} exact duplicate(s); the first occurrence is kept`, true),
    optCheckbox('placeholders_as_missing', 'Treat “N/A”, “null”, “-” … as missing', `${fmt(sum('placeholders'))} placeholder cell(s). Off by default: “NA” can be a real value.`, false),
    optCheckbox('drop_empty_columns', 'Remove completely empty columns', `${s.empty_columns.length} empty column(s). Removes columns from the cleaned copy.`, false, !s.empty_columns.length)));

  // missing values
  const sel = el('select', { id: 'opt-missing', 'aria-describedby': 'missing-info' },
    Object.entries(STRATEGY_INFO).map(([k, v]) => el('option', { value: k, text: v[0] })));
  const colList = el('details', { class: 'cols', id: 'missing-cols', hidden: true },
    el('summary', { text: 'Apply to columns (all selected)' }),
    el('div', {}, cols.map(c => el('div', { class: 'opt' },
      el('input', { type: 'checkbox', id: `mc-${c.index}`, value: c.index, checked: true, 'data-col': c.index }),
      el('label', { for: `mc-${c.index}`, text: `${c.index + 1}. ${c.name || '(blank)'}` })))));
  f.append(el('fieldset', {}, el('legend', { text: 'Missing values' }),
    el('label', { class: 'field', for: 'opt-missing' }, el('span', { text: `Strategy (${fmt(s.missing_cells)} missing cells)` }), sel),
    el('label', { class: 'field', id: 'fill-text-field', hidden: true }, el('span', { text: 'Fill text' }), el('input', { type: 'text', id: 'opt-fill-text', maxlength: 500, placeholder: 'e.g. Unknown' })),
    el('div', { class: 'warn-note', id: 'missing-info', 'aria-live': 'polite' }),
    colList));

  // types & dates
  f.append(el('fieldset', {}, el('legend', { text: 'Types & dates' }),
    optCheckbox('numeric', 'Convert numeric text to numbers',
      numCand.length ? `${numCand.length} safe column(s): ${numCand.slice(0, 4).map(c => c.name || '#' + (c.index + 1)).join(', ')}${numCand.length > 4 ? '…' : ''}. IDs, phones, ZIPs and leading-zero values are never converted.` : 'No column qualifies safely.', numCand.length > 0, !numCand.length),
    optCheckbox('dates', 'Normalize dates to YYYY-MM-DD',
      dateCand.length ? `${dateCand.length} date column(s). Unparseable or invalid dates are left unchanged.` : 'No date column detected.', dateCand.length > 0, !dateCand.length),
    ambiguous.length ? el('label', { class: 'field' }, el('span', { text: 'Ambiguous dates like 03/04/2023 mean…' }),
      el('select', { id: 'opt-date-order' },
        el('option', { value: 'auto', text: 'Don’t guess — skip those columns' }),
        el('option', { value: 'dmy', text: 'Day first (3 April)' }),
        el('option', { value: 'mdy', text: 'Month first (March 4)' }))) : null,
    emailCols.length ? optCheckbox('email_lowercase', 'Lowercase valid email addresses', `${emailCols.length} email column(s). Invalid addresses are always reported, never deleted.`, false) : el('p', { class: 'hint', text: 'No email column detected.' })));

  // output
  const tooBig = s.rows > (S.config.xlsx_max_rows || 1048575);
  f.append(el('fieldset', {}, el('legend', { text: 'Output format' }),
    el('div', { class: 'opt' }, el('input', { type: 'radio', name: 'fmt', id: 'fmt-csv', value: 'csv', checked: true }), el('label', { for: 'fmt-csv' }, 'CSV (UTF-8)', el('span', { class: 'hint', text: 'Recommended; fastest; preserves text exactly.' }))),
    el('div', { class: 'opt' }, el('input', { type: 'radio', name: 'fmt', id: 'fmt-xlsx', value: 'xlsx', disabled: tooBig }), el('label', { for: 'fmt-xlsx' }, 'Excel (.xlsx)', el('span', { class: 'hint', text: tooBig ? 'Not available: more rows than Excel supports.' : 'Typed numbers and dates; slower for large files.' })))));

  f.addEventListener('input', onFormChange);
  f.addEventListener('change', onFormChange);
  updateMissingUI();
}
function onFormChange(e) {
  if (e.target.id === 'opt-missing') updateMissingUI();
  if (e.target.matches('#missing-cols input')) updateMissingSummary();
  hidePlanError();
  schedulePreview(500);
}
function updateMissingUI() {
  const v = $('#opt-missing').value;
  $('#fill-text-field').hidden = v !== 'fill_text';
  $('#missing-cols').hidden = v === 'preserve';
  const info = $('#missing-info');
  info.textContent = STRATEGY_INFO[v][1];
  info.hidden = false;
  info.style.display = '';
  if (v === 'preserve') info.className = 'hint'; else info.className = 'warn-note';
  updateMissingSummary();
}
function updateMissingSummary() {
  const boxes = $$('#missing-cols input'); const n = boxes.filter(b => b.checked).length;
  $('#missing-cols summary').textContent = n === boxes.length ? 'Apply to columns (all selected)' : `Apply to columns (${n} of ${boxes.length} selected)`;
}
function readPlan() {
  const v = id => $(`#opt-${id}`);
  const boxes = $$('#missing-cols input');
  const picked = boxes.filter(b => b.checked).map(b => Number(b.value));
  return {
    trim: v('trim').checked, collapse_spaces: v('collapse_spaces').checked,
    normalize_columns: v('normalize_columns').checked, remove_duplicates: v('remove_duplicates').checked,
    placeholders_as_missing: v('placeholders_as_missing').checked, drop_empty_columns: v('drop_empty_columns').checked,
    missing: { strategy: $('#opt-missing').value, text: $('#opt-fill-text').value, columns: picked.length === boxes.length ? null : picked },
    numeric: { convert: v('numeric').checked },
    dates: { normalize: v('dates').checked, order: $('#opt-date-order') ? $('#opt-date-order').value : 'auto' },
    emails: { lowercase: v('email_lowercase') ? v('email_lowercase').checked : false },
    output_format: $('input[name=fmt]:checked').value,
  };
}
function applyPlanToForm(p) { /* restore previous plan after a reload */
  try {
    const set = (id, val) => { const e = $(`#opt-${id}`); if (e && !e.disabled) e.checked = !!val; };
    ['trim', 'collapse_spaces', 'normalize_columns', 'remove_duplicates', 'placeholders_as_missing', 'drop_empty_columns'].forEach(k => set(k, p[k]));
    set('numeric', p.numeric?.convert); set('dates', p.dates?.normalize); set('email_lowercase', p.emails?.lowercase);
    if (p.missing) { $('#opt-missing').value = p.missing.strategy; $('#opt-fill-text').value = p.missing.text || ''; }
    if (p.output_format === 'xlsx' && !$('#fmt-xlsx').disabled) $('#fmt-xlsx').checked = true;
    if ($('#opt-date-order') && p.dates?.order) $('#opt-date-order').value = p.dates.order;
    updateMissingUI();
  } catch { /* ignore */ }
}
function showPlanError(msg) { const b = $('#plan-error'); b.textContent = msg; b.hidden = false; }
function hidePlanError() { $('#plan-error').hidden = true; }

/* ------------------------------------------------------------------ preview */
let previewTimer = null, previewSeq = 0;
function schedulePreview(delay) {
  clearTimeout(previewTimer);
  const panel = $('#panel-preview');
  if (S.preview) panel.classList.add('stale');
  previewTimer = setTimeout(runPreview, delay);
}
async function runPreview() {
  const seq = ++previewSeq;
  const panel = $('#panel-preview');
  if (!S.preview) clear(panel).append(el('div', { class: 'loading' }, el('span', { class: 'spinner' }), 'Computing preview from your data…'));
  else panel.append(el('div', { class: 'loading', id: 'pv-loading' }, el('span', { class: 'spinner' }), 'Updating preview…'));
  try {
    const data = await post(`/api/jobs/${S.job.id}/preview`, { plan: readPlan() });
    if (seq !== previewSeq) return;
    S.preview = data; panel.classList.remove('stale'); renderPreview(data);
  } catch (e) {
    if (seq !== previewSeq) return;
    S.preview = null; panel.classList.remove('stale');
    clear(panel).append(el('div', { class: 'alert alert-error', role: 'alert' }, el('div', { class: 'alert-body' }, el('strong', { text: 'Preview unavailable' }), el('span', { text: e.message }))));
    if (e.code === 'bad_plan') showPlanError(e.message);
  }
}
function cellContent(before, after) {
  const show = v => v === '' ? el('span', { class: 'nil', text: '∅ empty' }) : v;
  if (after === before) return [before === '' ? el('span', { class: 'nil', text: '' }) : before, false];
  return [[el('span', { class: 'old' }, show(before)), el('span', { class: 'new' }, show(after))], true];
}
const STATUS_TXT = { duplicate: 'Duplicate — removed', dropped_missing: 'Has missing value — removed', kept: 'Kept' };

function renderPreview(d) {
  const panel = clear($('#panel-preview'));
  const m = d.metrics, pv = d.preview;
  const scope = d.exact ? '' : ` (first ${fmt(d.sample_rows)} rows only)`;
  panel.append(el('div', { class: `alert ${d.exact ? 'alert-info' : 'alert-warn'}` }, el('div', { class: 'alert-body' },
    el('strong', { text: 'Preview only — nothing has been applied to your data yet.' }),
    el('span', { text: d.exact
      ? `Totals below are exact for all ${fmt(d.total_rows)} rows: they come from a full dry run using the same engine as the final export. The table shows the first ${pv.rows.length} rows${pv.total_columns_shown < pv.total_columns ? ` and first ${pv.total_columns_shown} of ${pv.total_columns} columns` : ''}.`
      : `This file is large, so the preview was computed on its first ${fmt(d.sample_rows)} of ${fmt(d.total_rows)} rows only. Duplicates, mean/median/mode values and totals reflect that sample and are approximate for the full file. The real export processes every row.` }))));
  const s = el('div', { class: 'pv-summary' },
    metric(`Rows${scope}`, `${fmt(m.rows_in)} → ${fmt(m.rows_out)}`, d.exact ? 'exact for full file' : 'sample only'),
    metric('Columns', `${fmt(m.cols_in)} → ${fmt(m.cols_out)}`),
    metric('Missing cells', `${fmt(m.missing_before)} → ${fmt(m.missing_after)}`),
    metric('Duplicates removed', fmt(m.duplicates_removed)),
    metric('Rows dropped (missing)', fmt(m.rows_dropped_missing)),
    metric('Values filled', fmt(m.missing_filled)),
    metric('Dates rewritten', fmt(m.dates_normalized)),
    metric('Numbers reformatted', fmt(m.numeric_converted)));
  panel.append(s);
  for (const w of d.warnings) panel.append(el('div', { class: 'alert alert-warn' }, el('div', { class: 'alert-body' }, el('span', { text: w }))));

  const tb = el('div', { class: 'diff-toolbar' },
    el('div', { class: 'hint', text: `Showing the first ${pv.rows.length} rows. Green = new value, red = original value.` }),
    el('div', { class: 'seg', role: 'group', 'aria-label': 'Comparison layout' },
      ...[['inline', 'Inline changes'], ['side', 'Side by side']].map(([k, t]) => el('button', { type: 'button', 'aria-pressed': S.previewMode === k, onclick: () => { S.previewMode = k; renderPreview(d); } }, t))));
  panel.append(tb);
  panel.append(S.previewMode === 'inline' ? inlineTable(pv) : sideBySide(pv));
}
function headCells(pv, which) {
  return pv.columns_before.map((b, j) => {
    const a = pv.columns_after[j];
    return el('th', {}, which === 'before' ? b || '(blank)' : a, which === 'inline' && a !== b ? el('span', { class: 'col-rename', text: `→ ${a}` }) : null);
  });
}
function inlineTable(pv) {
  const body = pv.rows.map(r => {
    const removed = r.status !== 'kept';
    const tds = r.before.map((b, j) => {
      if (removed) return el('td', { text: b });
      const [c, changed] = cellContent(b, r.after[j]);
      return el('td', { class: changed ? 'cell-changed' : null }, c);
    });
    return el('tr', { class: removed ? 'removed' : null },
      el('td', { class: 'rownum', text: r.row }),
      el('td', { class: 'status' }, el('span', { class: `badge ${removed ? 'badge-removed' : 'badge-kept'}`, text: STATUS_TXT[r.status] })), tds);
  });
  return el('div', { class: 'table-wrap' }, el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, el('th', { class: 'rownum', text: 'Row' }), el('th', { text: 'Result' }), headCells(pv, 'inline'))), el('tbody', {}, body)));
}
function sideBySide(pv) {
  const left = pv.rows.map(r => el('tr', { class: r.status !== 'kept' ? 'removed' : null },
    el('td', { class: 'rownum', text: r.row }), r.before.map(b => el('td', { text: b }))));
  const right = pv.rows.map(r => {
    if (r.status !== 'kept') return el('tr', { class: 'removed' }, el('td', { class: 'rownum', text: r.row }),
      el('td', { colspan: Math.max(1, r.before.length) }, el('span', { class: 'badge badge-removed', text: STATUS_TXT[r.status] })));
    return el('tr', {}, el('td', { class: 'rownum', text: r.row }), r.after.map((a, j) => el('td', { class: a !== r.before[j] ? 'cell-changed' : null, text: a })));
  });
  const tbl = (title, head, rows) => el('div', {}, el('h3', { text: title }), el('div', { class: 'table-wrap' }, el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, el('th', { class: 'rownum', text: 'Row' }), head)), el('tbody', {}, rows))));
  return el('div', { class: 'side-by-side' }, tbl('Original', headCells(pv, 'before'), left), tbl('Cleaned (proposed)', headCells(pv, 'after'), right));
}

/* ------------------------------------------------------------------ stage 3 */
$('#btn-done').addEventListener('click', async () => {
  hidePlanError();
  if (S.job.state === 'completed' && !confirm('This will replace your previous cleaned result with a new one. Continue?')) return;
  const plan = readPlan();
  const btn = $('#btn-done'); btn.disabled = true;
  try {
    S.lastPlan = plan;
    S.job = await post(`/api/jobs/${S.job.id}/process`, { plan });
    clearTimeout(previewTimer); previewSeq++;
    show('export'); renderExport(); pollProcessing();
  } catch (e) {
    showPlanError(e.message);
    if (e.status === 409) { try { S.job = await api(`/api/jobs/${S.job.id}`); } catch { /* ignore */ } }
  } finally { btn.disabled = false; }
});

async function pollProcessing() {
  stopPolling();
  try {
    const j = await api(`/api/jobs/${S.job.id}`);
    S.job = j;
    syncTimer(j);
    renderExport();
    if (j.state === 'processing') S.pollTimer = setTimeout(pollProcessing, 600);
    else if (j.state === 'analyzed') { show('station'); renderCompletedBanner(); schedulePreview(0); live('Processing was cancelled.'); }
    else live(j.state === 'completed' ? 'Your cleaned file is ready.' : 'Processing failed.');
  } catch (e) {
    if (e.status === 404) { resetAll(); show('upload'); showUploadError('Job expired', 'That job no longer exists. Please upload the file again.', null); }
    else S.pollTimer = setTimeout(pollProcessing, 2000);
  }
}
function syncTimer(j) {
  S.timerBase = j.elapsed_seconds || 0; S.timerAt = performance.now();
  clearInterval(S.tickTimer);
  if (j.state === 'processing') S.tickTimer = setInterval(() => { $('#export-timer').textContent = clock(S.timerBase + (performance.now() - S.timerAt) / 1000); }, 250);
  $('#export-timer').textContent = clock(S.timerBase);
}

function renderExport() {
  const j = S.job; if (!j) return;
  const st = j.state;
  const pill = $('#export-pill');
  const label = { processing: 'Processing', completed: 'Completed', failed: 'Failed', analyzed: 'Pending' }[st] || st;
  pill.textContent = label; pill.dataset.state = st === 'analyzed' ? 'pending' : st;
  $('#export-processing').hidden = st !== 'processing';
  $('#export-failed').hidden = !(st === 'failed');
  $('#export-done').hidden = st !== 'completed';
  if (st === 'processing') renderProgress(j);
  if (st === 'failed') $('#export-error-text').textContent = j.error?.message || 'An unknown error occurred.';
  if (st === 'completed') renderResult(j);
  updateStepper();
}
function renderProgress(j) {
  const p = j.progress || { stages: [], stage: '' };
  const idx = p.stages.findIndex(s => s.key === p.stage);
  const ol = clear($('#stage-list'));
  p.stages.forEach((s, i) => ol.append(el('li', { 'data-s': i < idx ? 'done' : i === idx ? 'active' : 'pending' },
    el('span', { class: 'dot', 'aria-hidden': 'true' }, i < idx ? '✓' : ''), s.label, i === idx ? el('span', { class: 'sr-only', text: ' (in progress)' }) : null)));
  const box = $('#stage-measure');
  if (p.total && p.done !== null && p.done !== undefined) {
    const pct = Math.min(100, Math.floor(100 * p.done / p.total));
    box.hidden = false;
    $('#stage-measure-label').textContent = `${p.label}: ${fmt(p.done)} of ${fmt(p.total)} rows`;
    $('#stage-measure-pct').textContent = `${pct}%`;
    $('#stage-bar').value = pct;
  } else box.hidden = true;
}
$('#btn-cancel-processing').addEventListener('click', async () => { try { await post(`/api/jobs/${S.job.id}/cancel`); } catch (e) { alert(e.message); } });
$('#btn-retry-processing').addEventListener('click', async () => {
  try { S.job = await post(`/api/jobs/${S.job.id}/process`, { plan: S.lastPlan || readPlan() }); renderExport(); pollProcessing(); }
  catch (e) { alert(e.message); }
});
async function backToReview() {
  stopPolling(); clearInterval(S.tickTimer);
  if (!S.analysis) await enterStation(); else { show('station'); renderCompletedBanner(); }
}
$('#btn-back-failed').addEventListener('click', backToReview);
$('#btn-back-review').addEventListener('click', backToReview);
$('#btn-clean-another').addEventListener('click', () => startOver(true));
$('#btn-new-file').addEventListener('click', () => startOver(true));

function renderResult(j) {
  const r = j.result, m = r.metrics, o = j.output;
  $('#done-sub').textContent = `${fmt(m.rows_out)} rows × ${fmt(m.cols_out)} columns written to ${o.name} (${humanSize(o.size)}) in ${clock(j.elapsed_seconds || 0)}.`;
  const dl = clear($('#downloads'));
  const items = [['cleaned', 'Download cleaned data', `${o.name} · ${o.format.toUpperCase()}`, 'btn-primary'], ['report', 'Download report (HTML)', o.report_name, ''], ['report_json', 'Download report (JSON)', o.report_json_name, '']];
  const urlKey = { cleaned: 'cleaned', report: 'report', report_json: 'report-json' };
  for (const [k, t, sub, cls] of items) {
    if (j.downloads[k]) dl.append(el('a', { class: `btn ${cls}`, href: `/api/jobs/${j.id}/download/${urlKey[k]}`, download: '' }, t, el('small', { text: sub })));
    else dl.append(el('button', { class: 'btn', disabled: true }, t, el('small', { text: 'Not available' })));
  }
  const rm = clear($('#result-metrics'));
  rm.append(
    metric('Rows', `${fmt(m.rows_in)} → ${fmt(m.rows_out)}`), metric('Columns', `${fmt(m.cols_in)} → ${fmt(m.cols_out)}`),
    metric('Missing cells', `${fmt(m.missing_before)} → ${fmt(m.missing_after)}`),
    metric('Duplicates removed', fmt(m.duplicates_removed), 'counted when removed'),
    metric('Rows removed (missing)', fmt(m.rows_dropped_missing)), metric('Values filled', fmt(m.missing_filled)),
    metric('Cells trimmed', fmt(m.cells_trimmed)), metric('Dates normalized', fmt(m.dates_normalized), `${fmt(m.dates_unparsed)} left as-is`),
    metric('Numbers reformatted', fmt(m.numeric_converted)), metric('Invalid emails', fmt(m.emails_invalid), 'reported only'));
  const w = clear($('#result-warnings'));
  for (const t of r.warnings) w.append(el('div', { class: 'alert alert-warn' }, el('div', { class: 'alert-body' }, el('span', { text: t }))));
  const tbl = clear($('#ops-table'));
  tbl.append(el('thead', {}, el('tr', {}, ['Operation', 'Status', 'Affected', 'Details'].map(t => el('th', { text: t })))));
  const lbl = { applied: 'Applied', skipped: 'Skipped', not_requested: 'Not requested' };
  tbl.append(el('tbody', {}, r.operations.map(op => el('tr', {}, el('td', { text: op.label }),
    el('td', {}, el('span', { class: `badge ${op.status === 'applied' ? 'badge-kept' : op.status === 'skipped' ? 'badge-likely' : 'badge-suggestion'}`, text: lbl[op.status] })),
    el('td', { class: 'n', text: fmt(op.affected) }), el('td', { text: op.detail })))));
}

/* ------------------------------------------------------------------ lifecycle */
function resetAll() {
  stopPolling(); clearInterval(S.tickTimer); clearTimeout(previewTimer); previewSeq++;
  S.file = null; S.job = null; S.analysis = null; S.preview = null; S.lastPlan = null;
  localStorage.removeItem(KEY);
  renderFile(); hideUploadError(); endUploadUI(); updateStepper();
}
async function startOver(askConfirm) {
  const j = S.job;
  if (j && askConfirm && !confirm(j.state === 'completed'
    ? 'Start over with another file? Your current result and downloads will be deleted from the server.'
    : 'Start over with another file? The current job will be discarded.')) return;
  if (j) { try { await api(`/api/jobs/${j.id}`, { method: 'DELETE' }); } catch { /* may already be gone */ } }
  resetAll(); show('upload');
}

async function init() {
  try { S.config = await api('/api/config'); } catch { /* defaults */ }
  $('#max-size').textContent = humanSize(S.config.max_file_bytes);
  localStorage.removeItem(KEY); // always start fresh on load
  renderFile(); updateStepper(); show('upload');
  return;

}
$('#brand-link').addEventListener('click', () => { if (confirm('Are you sure?')) window.location.href = '/'; });
init();
})();
