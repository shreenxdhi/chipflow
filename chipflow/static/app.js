const $ = selector => document.querySelector(selector);
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const physicalStages = ['synth', 'floorplan', 'place', 'cts', 'route', 'finish'];
const stages = {
  sim: ['RTL simulation', 'Runs your testbench. Success requires a zero exit code and your configured success message.'],
  gate_sim: ['Gate simulation', 'Synthesizes your design, then tests the gates using your testbench and compatible cell models.'],
  synth: ['Synthesis', 'Converts RTL into a gate-level netlist using the selected technology library.'],
  floorplan: ['Floorplan & power', 'Runs synthesis, defines the chip area, and creates the power grid.'],
  place: ['Cell placement', 'Runs earlier stages and places the cells inside the core.'],
  cts: ['Clock tree', 'Runs earlier stages and builds the clock distribution network.'],
  route: ['Routing', 'Runs earlier stages and connects the cells with metal wires.'],
  finish: ['Final layout', 'Runs the full physical flow through extraction, timing reports, and GDSII. Test your RTL separately first.']
};
const statusNames = {queued: 'Starting', running: 'In progress', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted'};
let selected = null, offset = 0, logText = '', tab = 'overview', lastState = '', busy = false;
const logLimit = 2 * 1024 * 1024;
let logTrimmed = false, pausedLogText = null;
let runs = [], projects = [], environment = null, historyKey = '', filesCache = new Map();
let logQuery = '', fileQuery = '', followLogs = true, editorBase = null, starting = false;
const checked = new Set(), cancelling = new Set();
let requestedRun = new URLSearchParams(location.search).get('run');
let requestedTab = new URLSearchParams(location.search).get('tab');
let liveKey = '';
let guideData = null, guideConfig = '', guideBusy = false, referenceData = null, learningKey = '';
try { guideConfig = localStorage.getItem('chipflow.guideConfig') || ''; } catch {}
function updateLocation() {
  if (!selected) return; const url = new URL(location.href); url.searchParams.set('run', selected); url.searchParams.set('tab', tab); history.replaceState(null, '', url);
}
function renderLiveRun() {
  const active = runs.filter(run => !terminal.has(run.status));
  const run = active.find(run => run.id === selected) || active[0];
  const box = $('#live-run'); box.hidden = !run;
  if (!run) { liveKey = ''; return; }
  const current = run.stages.find(stage => stage.status === 'running');
  const key = JSON.stringify([run.id, run.status, current?.name, run.stages.length]);
  if (key === liveKey) return; liveKey = key; box.replaceChildren();
  const text = node('div'); text.append(node('strong', '● Live: ' + run.name), node('p', current ? `${stages[current.name][0]} · stage ${run.stages.length} of ${stageList(run.target).length}` : 'Preparing inputs and tools…'));
  box.append(text, button('Watch live execution →', () => { select(run.id); changeTab('logs'); $('#detail').scrollIntoView({block: 'start', behavior: 'smooth'}); }));
}

function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
}
function button(text, action, cls = 'secondary') {
  const element = node('button', text, cls);
  element.type = 'button';
  element.onclick = action;
  return element;
}
function message(selector, text, issues = []) {
  const box = $(selector);
  box.replaceChildren();
  box.hidden = !text;
  if (!text) return;
  box.append(node('strong', text));
  if (selector === '#notice') { const dismiss = button('×', () => { box.hidden = true; }, 'notice-close'); dismiss.setAttribute('aria-label', 'Dismiss notification'); box.append(dismiss); }
  if (issues.length) {
    const list = node('ul');
    for (const issue of issues) list.append(node('li', issue));
    box.append(list);
  }
}
function notify(text) { message('#notice', text); }
function showError(error, selector = '#error') { message(selector, error.message || String(error), error.issues || []); }
async function api(url, options = {}) {
  let response;
  try { response = await fetch(url, options); }
  catch { throw new Error('Cannot reach Chipflow. Check that the local server is running, then try again.'); }
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? 'Check the highlighted project settings.' : data.detail;
    const error = new Error(detail || `Request failed (${response.status}). Please try again.`);
    error.issues = data.issues || (Array.isArray(data.detail) ? data.detail.map(e => `${e.loc.slice(1).join('.')}: ${e.msg}`) : []);
    throw error;
  }
  return data;
}
function post(url, data) {
  return api(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
}
function bytes(value) {
  return value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`;
}
function duration(start, end = Date.now() / 1000) {
  const seconds = Math.max(0, Math.round(end - start));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
function stageList(target) {
  return target === 'sim' ? ['sim'] : target === 'gate_sim' ? ['synth', 'gate_sim'] : physicalStages.slice(0, physicalStages.indexOf(target) + 1);
}
function prettyMetric(key) {
  const known = {'finish__timing__setup__ws': 'Setup slack (ns)', 'finish__timing__hold__ws': 'Hold slack (ns)', 'finish__design__instance__area': 'Cell area (µm²)', 'finish__design__instance__count': 'Cell count', 'finish__power__total': 'Estimated power (W)', 'finish__timing__fmax': 'Maximum frequency (Hz)'};
  return known[key] || key.replace(/^finish__/, '').replaceAll('__', ' / ').replaceAll('_', ' ');
}
function number(value) {
  if (typeof value !== 'number') return value ?? 'Not available';
  return value !== 0 && Math.abs(value) < 0.001 ? value.toExponential(3) : Number(value.toFixed(4)).toLocaleString();
}
function select(id) {
  if (selected === id) return;
  selected = id; offset = 0; logText = ''; logTrimmed = false; pausedLogText = null; lastState = ''; tab = 'overview';
  logQuery = ''; fileQuery = ''; followLogs = true;
  try { localStorage.setItem('chipflow.selected', id); } catch {}
  updateLocation();
}
function updateCompare() {
  $('#compare').disabled = checked.size !== 2;
  $('#compare').textContent = checked.size === 2 ? 'Compare selected (2)' : 'Compare selected';
  $('#compare-count').textContent = checked.size ? `${checked.size} of 2 selected` : 'Select two runs to compare';
}
function renderRuns() {
  $('#count').textContent = runs.length;
  const active = runs.filter(r => !terminal.has(r.status)).length;
  $('#run-summary').textContent = `${active} in progress · ${runs.filter(r => r.status === 'completed').length} completed`;
  const query = $('#search').value.trim().toLowerCase(), filter = $('#filter').value;
  const shown = runs.filter(r => `${r.name} ${r.id} ${stages[r.target]?.[0]}`.toLowerCase().includes(query) &&
    (filter === 'all' || (filter === 'active' ? !terminal.has(r.status) : filter === 'failed' ? ['failed', 'interrupted'].includes(r.status) : r.status === filter)));
  const key = JSON.stringify([shown.map(r => [r.id, r.name, r.target, r.status]), selected, query, filter]);
  if (key === historyKey) return;
  historyKey = key;
  const focused = document.activeElement?.dataset.run;
  const list = $('#runs'); list.replaceChildren();
  if (!shown.length) {
    const empty = node('div', undefined, 'empty');
    empty.append(node('h3', runs.length ? 'No matching runs' : 'Your first run starts here'), node('p', runs.length ? 'Try another search or choose All runs.' : 'Try the counter example, or create a project with your own RTL.'));
    if (runs.length) empty.append(button('Clear filters', () => {$('#search').value = ''; $('#filter').value = 'all'; renderRuns();}));
    list.append(empty); return;
  }
  for (const run of shown) {
    const row = node('div', undefined, 'run' + (run.id === selected ? ' selected' : ''));
    const check = document.createElement('input');
    check.type = 'checkbox'; check.checked = checked.has(run.id); check.dataset.run = `compare-${run.id}`;
    check.setAttribute('aria-label', `Compare ${run.name}, ${run.id.slice(0, 8)}`);
    check.onchange = () => {
      if (check.checked && checked.size >= 2) { check.checked = false; notify('Two runs are already selected. Deselect one to choose another.'); return; }
      check.checked ? checked.add(run.id) : checked.delete(run.id); updateCompare();
    };
    const choose = button('', () => { select(run.id); renderRuns(); refresh(); if (window.matchMedia('(max-width: 900px)').matches) $('#detail').scrollIntoView({block: 'start', behavior: 'smooth'}); }, 'run-select');
    choose.dataset.run = run.id; choose.setAttribute('aria-current', run.id === selected ? 'true' : 'false');
    const info = node('span', undefined, 'info');
    info.append(node('strong', run.name), node('small', stages[run.target]?.[0] || run.target), node('small', `${new Date(run.created * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})} · ${run.id.slice(0, 6)}`));
    choose.append(info, node('span', statusNames[run.status] || run.status, 'status ' + run.status));
    row.append(check, choose); list.append(row);
  }
  if (focused) list.querySelector(`[data-run="${CSS.escape(focused)}"]`)?.focus({preventScroll: true});
}
function artifactLink(run, file, label) {
  const link = node('a', label === undefined ? file.path : label, 'download-link');
  link.href = `/api/runs/${run.id}/artifacts/` + file.path.split('/').map(encodeURIComponent).join('/');
  link.download = ''; return link;
}
async function getFiles(run) {
  const cached = filesCache.get(run.id);
  if (cached && cached.status === run.status && (terminal.has(run.status) || Date.now() - cached.time < 3000)) return cached.files;
  const files = await api(`/api/runs/${run.id}/artifacts`);
  filesCache.set(run.id, {files, time: Date.now(), status: run.status}); return files;
}
function mainFiles(files) {
  const definitions = [
    ['Waveform', 'Open in your local waveform viewer', /\.vcd$/],
    ['Final layout', 'GDSII · open in KLayout', /\/6_final\.gds$/],
    ['Gate-level netlist', 'Synthesized Verilog', /\/1_2_yosys\.v$/],
    ['Parasitics', 'Extracted resistance & capacitance', /\/6_final\.spef$/]
  ];
  return definitions.flatMap(([name, help, pattern]) => { const file = files.find(f => pattern.test(f.path)); return file ? [{name, help, file}] : []; });
}
function checkCard(label, value, explanation) {
  const card = node('div', undefined, 'check-card');
  card.append(node('span', label), node('strong', value === 'unknown' || !value ? 'Not verified' : value === 'passed' ? 'Passed' : 'Needs attention', 'check-value ' + (value || 'unknown')), node('p', explanation));
  return card;
}
function overview(run, files) {
  const panel = node('div', undefined, 'overview');
  const summary = node('div', undefined, 'result-banner ' + run.status);
  let title, description;
  if (run.status === 'completed') {
    title = run.target === 'sim' || run.target === 'gate_sim' ? 'Your simulation passed.' : 'Your requested flow completed.';
    description = run.target === 'sim' || run.target === 'gate_sim' ? 'The testbench exited successfully and printed its success message.' : 'All requested stages finished. Review the separate design checks below.';
  } else if (run.status === 'failed') { title = 'This run needs attention.'; description = run.error || 'A tool reported an error. Open Live logs for details.'; }
  else if (run.status === 'cancelled') { title = 'Run cancelled.'; description = 'Saved logs and any generated files are still available. You can run these inputs again.'; }
  else if (run.status === 'interrupted') { title = 'The worker stopped unexpectedly.'; description = 'Your logs and files are saved. Review them before trying again.'; }
  else { title = run.status === 'queued' ? (run.queue_reason ? 'Waiting for the physical flow slot…' : 'Preparing your design…') : 'Your design is running.'; description = 'You can leave this page. The run continues on your computer, and results are saved automatically.'; }
  summary.append(node('h3', title), node('p', description)); panel.append(summary);
  if (['failed', 'interrupted'].includes(run.status)) {
    const failure = node('div', undefined, 'help-note');
    failure.append(node('strong', 'What to do next'), node('p', 'Open Live logs and look for the first error. Check your source files, testbench and tool settings. Run again repeats the saved inputs; to change inputs, edit your project and start a new run.'), button('View error log', () => changeTab('logs'))); panel.append(failure);
  }
  const downloads = mainFiles(files);
  if (downloads.length) {
    panel.append(node('h3', 'Your output files', 'subheading'));
    const grid = node('div', undefined, 'download-grid');
    for (const {name, help, file} of downloads) {
      const link = artifactLink(run, file, ''); link.className = 'download-card';
      link.append(node('strong', '↓ ' + name), node('span', help), node('small', bytes(file.bytes))); grid.append(link);
    }
    panel.append(grid);
  } else if (run.status === 'completed') panel.append(node('p', 'All generated reports and run inputs are available in Files.', 'muted'));
  if (run.target !== 'sim' && run.target !== 'gate_sim') {
    panel.append(node('h3', 'Design checks', 'subheading'));
    const cards = node('div', undefined, 'check-grid');
    cards.append(checkCard('Timing', run.checks.timing, 'Final setup and hold slack for paths covered by your constraints.'), checkCard('Router checks', run.checks.routing_drc, 'Violations reported by the detailed router.'), checkCard('Physical signoff', run.checks.physical, 'Full foundry DRC and LVS are not certified by Chipflow.')); panel.append(cards);
    const metrics = node('div', undefined, 'metric-strip');
    for (const [key, label] of [['finish__timing__setup__ws', 'Setup slack'], ['finish__timing__hold__ws', 'Hold slack']]) {
      if (typeof run.metrics?.[key] === 'number') { const item = node('div'); item.append(node('span', label), node('strong', number(run.metrics[key]) + ' ns')); metrics.append(item); }
    }
    if (metrics.childElementCount) panel.append(metrics);
  }
  return panel;
}
function changeTab(value) { tab = value; updateLocation(); lastState = ''; const run = runs.find(r => r.id === selected); if (run) { renderDetail(run); renderTab(run).catch(error => showError(error)); } refresh(); }
function renderDetail(run) {
  const focused = $('#detail').contains(document.activeElement) ? document.activeElement.id : null;
  const root = $('#detail'); root.replaceChildren();
  const title = node('div', undefined, 'detail-title');
  const heading = node('div'); heading.append(node('span', stages[run.target]?.[0] || run.target, 'eyebrow'), node('h2', run.name));
  title.append(heading, node('span', statusNames[run.status] || run.status, 'status ' + run.status)); root.append(title);
  const meta = node('div', undefined, 'detail-meta');
  const id = node('span', run.id, 'meta'); id.title = 'Run ID';
  const elapsed = node('span', '', 'elapsed'); elapsed.dataset.start = run.started || run.created; if (run.finished) elapsed.dataset.end = run.finished;
  meta.append(id, elapsed); root.append(meta);
  const actions = node('div', undefined, 'run-actions');
  if (!terminal.has(run.status)) {
    const cancel = button(cancelling.has(run.id) ? 'Cancelling…' : 'Cancel run', async () => {
      cancel.disabled = true;
      try { await post(`/api/runs/${run.id}/cancel`); cancelling.add(run.id); cancel.textContent = 'Cancelling…'; notify('Cancellation requested. Saved files will remain available.'); }
      catch (error) { cancel.disabled = false; showError(error); }
    }); cancel.id = 'cancel-run'; cancel.disabled = cancelling.has(run.id); actions.append(cancel);
  } else {
    const retry = button('Run again', async () => {
      retry.disabled = true;
      try { const next = await post(`/api/runs/${run.id}/retry`); select(next.id); notify('New run started using the saved inputs.'); await refresh(); }
      catch (error) { showError(error); } finally { retry.disabled = false; }
    }); retry.id = 'retry-run'; actions.append(retry);
  }
  const copy = button('Copy run ID', async () => { await copyText(run.id); }); copy.id = 'copy-id'; actions.append(copy); root.append(actions);
  const pipeline = node('ol', undefined, 'stages'); pipeline.setAttribute('aria-label', 'Run progress');
  for (const [index, name] of stageList(run.target).entries()) {
    const stage = run.stages.find(s => s.name === name), status = stage?.status || 'pending';
    const item = node('li', undefined, 'stage ' + status);
    item.append(node('span', status === 'completed' ? '✓' : String(index + 1), 'stage-number'), node('span', stages[name][0]));
    item.title = `${stages[name][0]}: ${status}${stage?.finished ? ' · ' + duration(stage.started, stage.finished) : ''}`;
    item.append(node('span', ' — ' + status, 'sr-only'));
    if (status === 'running') item.setAttribute('aria-current', 'step'); pipeline.append(item);
  }
  root.append(pipeline);
  const tabs = node('div', undefined, 'tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Run details');
  for (const [value, label] of [['overview', 'Overview'], ['logs', 'Live logs'], ['artifacts', 'Files'], ['metrics', 'Metrics']]) {
    const item = button(label, () => changeTab(value), tab === value ? 'chosen' : '');
    item.id = 'tab-' + value; item.setAttribute('role', 'tab'); item.setAttribute('aria-selected', String(tab === value)); item.setAttribute('aria-controls', 'tab-panel');
    item.tabIndex = tab === value ? 0 : -1;
    item.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const order = ['overview', 'logs', 'artifacts', 'metrics'], current = order.indexOf(value);
      const next = event.key === 'Home' ? order[0] : event.key === 'End' ? order[3] : order[(current + (event.key === 'ArrowRight' ? 1 : 3)) % 4];
      changeTab(next); setTimeout(() => $('#tab-' + next)?.focus(), 150);
    };
    tabs.append(item);
  }
  const panel = node('div', undefined, 'tab-content'); panel.id = 'tab-panel'; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', 'tab-' + tab);
  root.append(tabs, panel);
  if (focused) document.getElementById(focused)?.focus({preventScroll: true});
  updateElapsed();
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); notify('Copied to clipboard.'); }
  catch { notify('Clipboard access is unavailable. You can select and copy the text directly.'); }
}
function renderLogText() {
  const pre = $('#log-output'); if (!pre) return;
  const query = logQuery.toLowerCase();
  const text = !followLogs && pausedLogText !== null ? pausedLogText : logText;
  const rendered = (query ? text.split('\n').filter(line => line.toLowerCase().includes(query)).join('\n') : text) || (query ? 'No matching log lines.' : 'Preparing tools and waiting for output…');
  if (pre.textContent !== rendered) pre.textContent = rendered;
  if (followLogs) pre.scrollTop = pre.scrollHeight;
  const count = $('#log-count'); if (count) count.textContent = (query ? `${pre.textContent === 'No matching log lines.' ? 0 : pre.textContent.split('\n').length} matching lines` : 'Updates automatically') + (logTrimmed ? ' · Showing recent output; download the full log to search earlier lines.' : '');
}
function logsPanel(panel, run) {
  if (!$('#log-output')) {
    const tools = node('div', undefined, 'log-tools');
    const search = node('input'); search.type = 'search'; search.id = 'log-search'; search.placeholder = 'Find in logs…'; search.setAttribute('aria-label', 'Find in logs'); search.value = logQuery;
    search.oninput = () => { logQuery = search.value; renderLogText(); };
    const follow = node('label', undefined, 'checkbox-label'); const check = node('input'); check.type = 'checkbox'; check.checked = followLogs;
    check.onchange = () => { followLogs = check.checked; pausedLogText = followLogs ? null : logText; renderLogText(); }; follow.append(check, node('span', 'Follow output'));
    const file = {path: 'console.log'}; const download = artifactLink(run, file, '↓ Download log');
    tools.append(search, follow, download);
    const pre = node('pre'); pre.id = 'log-output'; pre.tabIndex = 0; pre.setAttribute('aria-label', 'Run log output');
    pre.onscroll = () => { if (pre.scrollHeight - pre.scrollTop - pre.clientHeight > 100 && followLogs) { followLogs = false; pausedLogText = logText; check.checked = false; } };
    panel.append(tools, node('small', '', 'muted'), pre); panel.querySelector('small').id = 'log-count';
  }
  renderLogText();
}
function renderFiles(panel, run, files) {
  if (!panel.querySelector('#file-search')) {
    const search = node('input'); search.id = 'file-search'; search.type = 'search'; search.placeholder = 'Find a file by name or type…'; search.value = fileQuery; search.setAttribute('aria-label', 'Search output files');
    search.oninput = () => { fileQuery = search.value; renderFileList(panel, run, files); };
    panel.append(search, node('div', undefined, 'artifacts'));
  }
  // Always update the handler with the latest list while a run generates files.
  panel.querySelector('#file-search').oninput = event => { fileQuery = event.target.value; renderFileList(panel, run, files); };
  renderFileList(panel, run, files);
}
function renderFileList(panel, run, files) {
  const list = panel.querySelector('.artifacts');
  const key = JSON.stringify([files, fileQuery]); if (list.dataset.key === key) return; list.dataset.key = key; list.replaceChildren();
  const query = fileQuery.toLowerCase();
  const shown = files.filter(file => file.path.toLowerCase().includes(query));
  if (!shown.length) { list.append(node('p', 'No matching files. Try another filename or extension.', 'empty')); return; }
  const priority = mainFiles(shown); const featured = new Set(priority.map(f => f.file.path));
  for (const {name, file} of priority) { const link = artifactLink(run, file, '↓ ' + name); link.append(node('small', `${file.path} · ${bytes(file.bytes)}`)); list.append(link); }
  const rest = shown.filter(file => !featured.has(file.path));
  if (rest.length) {
    const details = node('details', undefined, 'all-files'); details.open = Boolean(query);
    details.append(node('summary', `All other files (${rest.length})`));
    for (const file of rest) { const link = artifactLink(run, file); link.append(node('small', bytes(file.bytes))); details.append(link); } list.append(details);
  }
}
function metricsPanel(panel, run) {
  const metrics = Object.entries(run.metrics || {});
  panel.replaceChildren();
  if (!metrics.length) { panel.append(node('div', 'Final timing, area and power metrics appear after the finishing stage. Simulation results are shown in Overview.', 'empty')); return; }
  const table = node('table'); const caption = node('caption', 'Final reported metrics'); table.append(caption);
  for (const [key, value] of metrics) { const row = node('tr'); const label = node('th', prettyMetric(key)); label.scope = 'row'; label.title = key; row.append(label, node('td', number(value))); table.append(row); }
  panel.append(table);
  const raw = node('details'); raw.append(node('summary', 'Show raw metrics'), node('pre', JSON.stringify(run.metrics, null, 2))); panel.append(raw);
}
async function renderTab(run) {
  const currentTab = tab, panel = $('.tab-content');
  if (tab === 'logs') { logsPanel(panel, run); return; }
  if (tab === 'metrics') { if (!panel.childElementCount) metricsPanel(panel, run); return; }
  const files = await getFiles(run);
  if (selected !== run.id || tab !== currentTab || !panel.isConnected) return;
  if (tab === 'artifacts') renderFiles(panel, run, files);
  else { const key = JSON.stringify([run.status, run.checks, files]); if (panel.dataset.overview !== key) { panel.replaceChildren(overview(run, files)); panel.dataset.overview = key; } }
}
function updateElapsed() {
  for (const el of document.querySelectorAll('.elapsed')) el.textContent = `${duration(Number(el.dataset.start), el.dataset.end ? Number(el.dataset.end) : undefined)} ${el.dataset.end ? 'total' : 'elapsed'}`;
}
async function refresh() {
  if (busy) return; busy = true;
  try {
    runs = await api('/api/runs');
    $('#connection').textContent = '● Local workspace'; $('#connection').classList.remove('offline'); $('#connection').title = 'View installed tools';
    if (requestedRun) { const requested = runs.find(run => run.id === requestedRun); if (requested) { select(requested.id); if (['overview', 'logs', 'artifacts', 'metrics'].includes(requestedTab)) tab = requestedTab; requestedRun = null; updateLocation(); } }
    if (!selected || !runs.some(run => run.id === selected)) {
      let previous; try { previous = localStorage.getItem('chipflow.selected'); } catch {}
      select(runs.find(run => run.id === previous)?.id || runs[0]?.id || null);
    }
    renderRuns(); renderLiveRun(); renderLearning(); updateElapsed();
    if (selected) {
      const id = selected, run = runs.find(item => item.id === id);
      const logs = await api(`/api/runs/${id}/logs?offset=${offset}`);
      if (selected !== id) return;
      if (logs.offset < offset) { logText = ''; logTrimmed = false; }
      logText += logs.text; offset = logs.offset;
      if (logText.length > logLimit) { const tail = logText.slice(-logLimit); const newline = tail.indexOf('\n'); logText = newline >= 0 ? tail.slice(newline + 1) : tail; logTrimmed = true; }
      const key = JSON.stringify(run) + tab;
      if (key !== lastState) { renderDetail(run); lastState = key; }
      await renderTab(run);
    }
  } catch (error) {
    $('#connection').textContent = '● Reconnecting…'; $('#connection').classList.add('offline');
    $('#connection').title = error.message + ' Running jobs continue independently.';
  } finally { busy = false; }
}
function renderPreflight(result) {
  const box = $('#preflight'); box.replaceChildren(); box.hidden = false;
  box.className = result.ready ? 'preflight ready' : 'preflight not-ready';
  box.append(node('strong', result.ready ? `✓ ${result.project.name} is ready to start` : 'A few things need fixing first'));
  if (result.ready) {
    const simulation = result.project.simulation;
    box.append(node('p', `${result.project.rtl.length} RTL file${result.project.rtl.length === 1 ? '' : 's'} · ${result.project.platform} · Top: ${result.project.top}`));
    if (result.target === 'sim' || result.target === 'gate_sim') box.append(node('small', `Success message: “${simulation.pass_marker}” · Testbench: ${simulation.top}`));
    else box.append(node('small', result.project.sdc ? 'Using your SDC timing constraints.' : 'Using the configured clock. Add an SDC file for I/O delays and timing exceptions.'));
  } else { const list = node('ul'); for (const issue of result.issues) list.append(node('li', issue)); box.append(list); }
  return result.ready;
}
async function checkProject() {
  message('#error', '');
  const result = await post('/api/projects/inspect', {config: $('#config').value.trim(), target: $('#target').value});
  renderPreflight(result); return result;
}
$('#start').onsubmit = async event => {
  event.preventDefault(); if (starting) return; starting = true;
  const start = $('#start-button'); start.disabled = true; start.textContent = 'Checking project…';
  try {
    const result = await checkProject(); if (!result.ready) { $('#preflight').scrollIntoView({block: 'nearest', behavior: 'smooth'}); return; }
    start.textContent = 'Starting…';
    const run = await post('/api/runs', {config: $('#config').value.trim(), target: $('#target').value});
    select(run.id); notify(`${result.project.name} started. You can follow progress below.`);
    $('#filter').value = 'all'; $('#search').value = ''; await refresh();
    $('#detail').scrollIntoView({block: 'nearest', behavior: 'smooth'});
    try { localStorage.setItem('chipflow.config', $('#config').value); } catch {}
  } catch (error) { showError(error); } finally { starting = false; start.disabled = false; start.textContent = 'Start run ↗'; }
};
$('#validate').onclick = async () => {
  const button = $('#validate'); button.disabled = true; button.textContent = 'Checking…';
  try { await checkProject(); } catch (error) { $('#preflight').hidden = true; showError(error); } finally { button.disabled = false; button.textContent = 'Check project'; }
};
function stageHelp() { $('#stage-help').textContent = stages[$('#target').value][1]; $('#preflight').hidden = true; }
$('#target').onchange = stageHelp;
$('#config').oninput = () => { $('#preflight').hidden = true; message('#error', ''); };
$('#demo').onclick = () => {
  $('#config').value = projects.find(p => p.example && /chipflow.yaml$/.test(p.path))?.path || environment?.example || 'examples/counter/chipflow.yaml';
  $('#target').value = 'sim'; stageHelp(); $('#start-button').focus();
  notify('Counter example selected. Click Start run to test its self-checking simulation.');
};
$('#search').oninput = renderRuns; $('#filter').onchange = renderRuns;
$('#compare').onclick = async () => {
  if (checked.size !== 2) return;
  try {
    const [left, right] = [...checked]; const data = await api(`/api/compare?left=${left}&right=${right}`);
    const content = $('#comparison-content'); content.replaceChildren();
    const table = node('table'); const head = node('tr'); head.append(node('th', 'Result'), node('th', `${data.left.name} · ${left.slice(0, 6)}`), node('th', `${data.right.name} · ${right.slice(0, 6)}`), node('th', 'Change')); table.append(head);
    const row = (label, a, b, delta = '—') => { const item = node('tr'); const heading = node('th', label); heading.scope = 'row'; item.append(heading, node('td', a), node('td', b), node('td', delta)); table.append(item); };
    row('Run stage', stages[data.left.target][0], stages[data.right.target][0]);
    row('Status', statusNames[data.left.status], statusNames[data.right.status]);
    for (const [key, label] of [['timing', 'Timing'], ['routing_drc', 'Router checks'], ['physical', 'Physical signoff']]) row(label, data.left.checks[key] || 'unknown', data.right.checks[key] || 'unknown');
    for (const key of new Set([...Object.keys(data.left.metrics || {}), ...Object.keys(data.right.metrics || {})])) row(prettyMetric(key), number(data.left.metrics?.[key]), number(data.right.metrics?.[key]), key in data.deltas ? number(data.deltas[key]) : '—');
    content.append(table); if (!Object.keys(data.deltas).length) content.append(node('p', 'No shared numeric final metrics yet. Final metrics are produced by the finishing stage.', 'muted'));
    $('#comparison').hidden = false; $('#comparison').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) { showError(error); }
};
$('#close-compare').onclick = () => { $('#comparison').hidden = true; $('#compare').focus(); };
function openDialog(id) { $('#' + id).showModal(); }
for (const id of ['help-open', 'footer-help']) $('#' + id).onclick = () => openDialog('help-dialog');
for (const id of ['tools-open', 'connection']) $('#' + id).onclick = () => openDialog('tools-dialog');
for (const button of document.querySelectorAll('[data-close]')) button.onclick = () => $('#' + button.dataset.close).close();
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => { if (event.target === dialog) { const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); } });
const fieldIds = ['name', 'top', 'rtl', 'tb', 'simtop', 'marker', 'clock', 'period', 'platform', 'orfs', 'sdc', 'die', 'core', 'density', 'threads', 'includes', 'models', 'tcldir', 'hooks', 'parameters', 'variables', 'files'];
function lines(value) { return value.split('\n').map(s => s.trim()).filter(Boolean); }
function populateEditor(project = null, inputs = []) {
  editorBase = project;
  $('#project-form').reset(); message('#project-error', '');
  const defaults = {orfs: environment?.orfs || '~/vlsi-tools/OpenROAD-flow-scripts'};
  if (project) {
    const paths = kind => inputs.filter(i => i.kind === kind).map(i => i.path).join('\n');
    Object.assign(defaults, {name: project.name, top: project.top, rtl: paths('RTL source') || project.rtl.join('\n'), tb: paths('Testbench'), simtop: project.simulation?.top || '', marker: project.simulation?.pass_marker || 'PASS', clock: project.clocks[0]?.port || '', period: project.clocks[0]?.period_ns || '', platform: project.platform, orfs: project.orfs, sdc: paths('Timing constraints'), die: project.physical.DIE_AREA ?? '', core: project.physical.CORE_AREA ?? '', density: project.physical.PLACE_DENSITY ?? '', threads: project.threads, includes: paths('Include folder'), models: paths('Cell model')});
  }
  const pairs = values => Object.entries(values || {}).map(([name, value]) => `${name}=${Array.isArray(value) ? value.join(',') : value}`).join('\n');
  defaults.tcldir = inputs.find(input => input.kind === 'Tcl directory')?.path || project?.tcl?.directory || '';
  defaults.hooks = pairs(project?.tcl?.hooks); defaults.parameters = pairs(project?.tcl?.parameters);
  defaults.variables = pairs(project?.orfs_variables);
  defaults.files = pairs(Object.fromEntries(Object.entries(project?.orfs_files || {}).map(([name, files]) => [name, inputs.filter(input => input.kind === 'ORFS file: ' + name).map(input => input.path).length ? inputs.filter(input => input.kind === 'ORFS file: ' + name).map(input => input.path) : files])));
  for (const [key, value] of Object.entries(defaults)) $('#p-' + key).value = value;
  $('#project-heading').textContent = project ? 'Edit a project copy' : 'Create a project';
  const extra = project && (project.tcl || Object.keys(project.orfs_variables || {}).length || Object.keys(project.orfs_files || {}).length); $('#project-dialog .advanced').open = $('#experience').value === 'expert' || Boolean(extra);
  clockRequirements(); openDialog('project-dialog'); $('#p-name').focus();
}
function clockRequirements() {
  const sdc = Boolean($('#p-sdc').value.trim());
  $('#p-clock').required = !sdc; $('#p-period').required = !sdc;
  $('#p-simtop').required = Boolean($('#p-tb').value.trim());
  $('#p-marker').required = Boolean($('#p-tb').value.trim());
}
$('#p-sdc').oninput = clockRequirements; $('#p-tb').oninput = clockRequirements;
$('#create-project').onclick = () => populateEditor();
$('#edit-project').onclick = async () => {
  try { const info = await post('/api/projects/inspect', {config: $('#config').value.trim(), target: $('#target').value});
    // Resolve the ORFS folder relative to the source config before creating a copy.
    info.project.orfs = info.orfs || info.project.orfs; populateEditor(info.project, info.inputs);
  } catch (error) { showError(error); }
};
$('#project-close').onclick = () => $('#project-dialog').close();
$('#project-form').onsubmit = async event => {
  event.preventDefault(); const save = $('#save-project'); save.disabled = true; save.textContent = 'Saving…'; message('#project-error', '');
  try {
    const values = Object.fromEntries(fieldIds.map(id => [id, $('#p-' + id).value.trim()]));
    const physical = {...editorBase?.physical};
    for (const [key, value] of [['DIE_AREA', values.die], ['CORE_AREA', values.core], ['PLACE_DENSITY', values.density]]) { if (value) physical[key] = key === 'PLACE_DENSITY' ? Number(value) : value; else delete physical[key]; }
    // Explicit die/core dimensions supersede utilization-based sizing.
    if (values.die || values.core) delete physical.CORE_UTILIZATION;
    const project = {...editorBase, name: values.name, top: values.top, rtl: lines(values.rtl), orfs: values.orfs, platform: values.platform,
      clocks: values.clock && values.period ? [{port: values.clock, period_ns: Number(values.period)}, ...(editorBase?.clocks.slice(1) || [])] : [],
      sdc: values.sdc || null, include_dirs: lines(values.includes), threads: Number(values.threads), physical,
      simulation: values.tb ? {...editorBase?.simulation, top: values.simtop, testbench: lines(values.tb), pass_marker: values.marker || 'PASS', cell_models: lines(values.models)} : null};
    const hooks = parseAssignments(values.hooks, 'Tcl hooks'), parameters = parseAssignments(values.parameters, 'Tcl parameters');
    if (!values.tcldir && (Object.keys(hooks).length || Object.keys(parameters).length)) throw new Error('Choose a Tcl bundle directory for your scripts and parameters.');
    project.tcl = values.tcldir ? {directory: values.tcldir, hooks, parameters} : null;
    project.orfs_variables = Object.fromEntries(Object.entries(parseAssignments(values.variables, 'ORFS settings')).map(([key, value]) => [key, value === 'true' ? true : value === 'false' ? false : /^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(value) ? Number(value) : value]));
    project.orfs_files = Object.fromEntries(Object.entries(parseAssignments(values.files, 'ORFS files')).map(([key, value]) => [key, value.split(',').map(path => path.trim()).filter(Boolean)]));
    const saved = await post('/api/projects', project);
    await loadProjects(); $('#config').value = saved.path; $('#project-dialog').close();
    notify(`${saved.name} saved. Check the project, then start a run.`); await checkProject();
  } catch (error) { showError(error, $('#project-dialog').open ? '#project-error' : '#error'); }
  finally { save.disabled = false; save.textContent = 'Save project'; }
};
async function loadProjects() {
  projects = await api('/api/projects'); $('#project-list').replaceChildren();
  for (const project of projects) { const option = node('option'); option.value = project.path; option.label = `${project.name}${project.example ? ' · example' : ''} · ${project.platform}`; $('#project-list').append(option); }
}
async function setup(preserveConfig = false) {
  const initialConfig = $('#config').value;
  try {
    environment = await api('/api/workspace'); $('#workspace-path').textContent = environment.directory;
    $('#platform-list').replaceChildren(); for (const platform of environment.platforms) { const option = node('option'); option.value = platform; $('#platform-list').append(option); }
    const content = $('#tools-content'); content.replaceChildren();
    for (const [name, path] of Object.entries(environment.tools)) { const item = node('div', undefined, 'tool-row'); item.append(node('strong', name), node('span', path || 'Not found on PATH', path ? 'tool-path' : 'missing-tool')); content.append(item); }
    const setup = environment.setup;
    if (setup && !setup.ready) {
      content.append(node('h3', 'Next steps', 'subheading'));
      for (const check of setup.checks.filter(c => c.status !== 'available')) {
        const issue = node('div', undefined, 'help-note');
        issue.append(node('strong', check.name), node('p', check.detail), node('p', check.action)); content.append(issue);
      }
      content.append(node('h3', 'Choose tools to install', 'subheading'));
      content.append(node('p', 'Choose a package or build variant below. Copy its command and run it in a terminal on this machine; Chipflow will show the installation commands and ask before running them.'));
      const select = node('select'); select.id = 'install-option'; select.setAttribute('aria-label', 'Tool installation variant');
      for (const item of setup.installation_options || []) {
        const option = node('option', item.label + (item.available ? '' : ' (guide only / unavailable)'));
        option.value = item.id; select.append(option);
      }
      const description = node('p'); description.id = 'install-description';
      const command = node('code'); command.id = 'install-command';
      const source = node('a', 'Installation guide'); source.target = '_blank'; source.rel = 'noopener noreferrer';
      const copy = button('Copy installation command', async () => { await copyText(command.textContent); });
      const update = () => {
        const item = (setup.installation_options || []).find(item => item.id === select.value);
        if (!item) return;
        description.textContent = item.description + (item.available ? '' : ' ' + item.reason);
        command.textContent = item.command; command.hidden = !item.available; copy.hidden = !item.available;
        source.href = item.source;
      };
      select.onchange = update; update();
      const choice = node('section', undefined, 'installation-choice');
      choice.append(select, description, command, copy, source); content.append(choice);
    }
    content.append(node('p', 'Run chipflow setup --install in a terminal to choose and install missing tools. Package installation itself does not verify flow compatibility.', 'muted'));
    content.append(button('Recheck installed tools', setupTools));
    content.append(node('p', `Workspace: ${environment.directory}`, 'muted'));
    await loadProjects();
    if (!preserveConfig && $('#config').value === initialConfig) {
      try { const previous = localStorage.getItem('chipflow.config'); $('#config').value = previous || environment.example; } catch {}
    }
  } catch (error) { $('#tools-content').textContent = error.message; }
}

async function setupTools() { await setup(true); }

function parseAssignments(text, label) {
  const result = {};
  for (const line of lines(text).filter(line => !line.startsWith('#'))) {
    const at = line.indexOf('=');
    if (at < 1) throw new Error(`${label}: use one NAME=value assignment per line.`);
    const name = line.slice(0, at).trim(), value = line.slice(at + 1).trim();
    if (Object.hasOwn(result, name)) throw new Error(`${label}: ${name} is listed twice.`);
    result[name] = value;
  }
  return result;
}
function setExperience() {
  const expert = $('#experience').value === 'expert';
  $('#learning').hidden = expert; $('.welcome').hidden = expert;
  $('#edit-yaml').hidden = !expert;
  try { localStorage.setItem('chipflow.experience', $('#experience').value); } catch {}
}
try { $('#experience').value = localStorage.getItem('chipflow.experience') || 'learn'; } catch {}
$('#experience').onchange = setExperience;
setExperience();
function learningState() {
  const byId = new Map(runs.map(run => [run.id, run]));
  const related = runs.filter(run => {
    const seen = new Set();
    while (run && !seen.has(run.id)) {
      if (run.source_config === guideConfig) return true;
      seen.add(run.id);
      const parts = run.source_config?.split('/') || [];
      run = parts.at(-1) === 'project.json' ? byId.get(parts.at(-2)) : null;
    }
    return false;
  });
  const targets = new Set(guideData?.lessons.map(lesson => lesson.target) || []);
  const done = new Set(related.flatMap(run => run.stages.filter(stage => stage.status === 'completed' && targets.has(stage.name)).map(stage => stage.name)));
  const active = related.find(run => !terminal.has(run.status));
  const next = guideData?.lessons.find(lesson => !done.has(lesson.target));
  return {done, active, next};
}
function renderLearning() {
  if (!guideData) return;
  const {done, active, next} = learningState();
  const key = JSON.stringify([guideConfig, [...done], active?.id, next?.target, guideBusy]);
  if (key === learningKey) return; learningKey = key;
  $('#learning-progress').textContent = guideConfig ? `${done.size} of ${guideData.lessons.length} steps completed for ${guideConfig.split('/').slice(-2).join('/')}` : 'Start with a counter that includes a testbench. Simulation only needs Icarus Verilog.';
  $('#learn-start').disabled = guideBusy || Boolean(active);
  $('#learn-start').textContent = guideConfig ? 'Start another counter' : 'Start learning with a counter';
  $('#learn-next').hidden = !guideConfig || !next;
  $('#learn-next').disabled = guideBusy || Boolean(active);
  $('#learn-next').textContent = active ? 'Step running…' : next ? `Run step ${guideData.lessons.indexOf(next) + 1}: ${next.title}` : 'Learning path complete';
  $('#learning-status').textContent = active ? `Running ${stages[active.target][0]}. Watch its logs below.` : guideConfig && !next ? 'All learning steps completed. Review timing and router checks before interpreting the layout.' : '';
  const list = $('#lessons'); list.replaceChildren();
  for (const [index, lesson] of guideData.lessons.entries()) {
    const item = node('li', undefined, 'lesson' + (done.has(lesson.target) ? ' done' : next?.target === lesson.target && guideConfig ? ' current' : ''));
    item.append(node('small', `${String(index + 1).padStart(2, '0')} · ${done.has(lesson.target) ? 'Completed' : active?.target === lesson.target ? 'Running' : lesson.target}`), node('h3', lesson.title), node('p', lesson.explanation));
    const details = node('details'); details.append(node('summary', 'What to look for'), node('p', 'Output: ' + lesson.output), node('p', lesson.review), node('small', 'Tools: ' + lesson.tools)); item.append(details); list.append(item);
  }
}
async function learningRun(create) {
  if (guideBusy) return; guideBusy = true; renderLearning(); message('#error', '');
  try {
    if (create) { const starter = await post('/api/projects/starter'); guideConfig = starter.path; await loadProjects(); }
    if (!guideConfig) throw new Error('Choose a project or start with the counter example.');
    try { localStorage.setItem('chipflow.guideConfig', guideConfig); } catch {}
    const next = learningState().next;
    if (!next) return;
    $('#config').value = guideConfig; $('#target').value = next.target; stageHelp();
    const check = await checkProject();
    if (!check.ready) { notify('Resolve the project checks below. Installed tools lists setup suggestions.'); return; }
    const run = await post('/api/runs', {config: guideConfig, target: next.target});
    select(run.id); changeTab('logs'); await refresh();
    notify(`Learning step started: ${next.title}. Read “What to look for” while the tools run.`);
    $('#detail').scrollIntoView({block: 'start', behavior: 'smooth'});
  } catch (error) { showError(error); }
  finally { guideBusy = false; renderLearning(); }
}
$('#learn-start').onclick = () => learningRun(true);
$('#learn-next').onclick = () => learningRun(false);
$('#learn-current').onclick = async () => {
  try {
    const info = await post('/api/projects/inspect', {config: $('#config').value.trim(), target: 'sim'});
    guideConfig = info.path; try { localStorage.setItem('chipflow.guideConfig', guideConfig); } catch {}
    renderLearning(); notify('Learning path now follows the selected project. Each physical target includes its prerequisites.');
  } catch (error) { showError(error); }
};
async function loadGuide() {
  try {
    guideData = await api('/api/guide'); const glossary = $('#glossary'); glossary.replaceChildren();
    for (const [term, meaning] of Object.entries(guideData.glossary)) glossary.append(node('dt', term), node('dd', meaning));
    renderLearning();
  } catch (error) { $('#learning-progress').textContent = error.message; }
}
function renderReference() {
  if (!referenceData) return;
  const query = $('#reference-search').value.toLowerCase(), root = $('#reference-results'); root.replaceChildren();
  const items = [...Object.entries(referenceData.hooks).map(([name, info]) => ({name, ...info, hook: true})), ...Object.entries(referenceData.variables).filter(([, info]) => !info.managed && !info.script).map(([name, info]) => ({name, ...info, hook: false}))].filter(item => (item.name + ' ' + item.description).toLowerCase().includes(query));
  root.append(node('p', `${items.length} matching settings. Showing up to 40; search to narrow the list.`, 'muted'));
  for (const item of items.slice(0, 40)) {
    const row = node('div', undefined, 'reference-item'); row.append(node('strong', item.name), node('p', item.description));
    if (item.hook && !item.available) row.append(node('p', 'Not executed by this installed ORFS revision.', 'missing-tool'));
    else {
      const add = button(item.hook ? 'Add script entry' : 'Add override', () => {
        const field = $(item.hook ? '#p-hooks' : '#p-variables');
        try { if (Object.hasOwn(parseAssignments(field.value, 'Settings'), item.name)) { notify(`${item.name} is already in the form.`); return; } }
        catch (error) { showError(error, '#project-error'); return; }
        const value = item.hook ? 'custom.tcl' : item.default == null || /[$#\n]/.test(String(item.default)) ? '' : String(item.default);
        field.value += (field.value.trim() ? '\n' : '') + item.name + '=' + value; field.focus();
      }); row.append(add);
    }
    root.append(row);
  }
}
$('#reference-load').onclick = async () => {
  try { referenceData = await api('/api/reference?orfs=' + encodeURIComponent($('#p-orfs').value)); $('#reference').hidden = false; renderReference(); }
  catch (error) { showError(error, '#project-error'); }
};
$('#reference-search').oninput = renderReference;
$('#edit-yaml').onclick = async () => {
  try {
    const data = await api('/api/projects/yaml?config=' + encodeURIComponent($('#config').value.trim()));
    $('#yaml-editor').value = data.yaml; message('#yaml-error', ''); openDialog('yaml-dialog'); $('#yaml-editor').focus();
  } catch (error) { showError(error); }
};
$('#yaml-form').onsubmit = async event => {
  event.preventDefault(); const save = $('#yaml-save'); save.disabled = true; message('#yaml-error', '');
  try {
    const data = await post('/api/projects/yaml', {yaml: $('#yaml-editor').value});
    await loadProjects(); $('#config').value = data.path; $('#yaml-dialog').close();
    notify('Saved a new project copy with the complete configuration.'); await checkProject();
  } catch (error) { showError(error, $('#yaml-dialog').open ? '#yaml-error' : '#error'); }
  finally { save.disabled = false; }
};
stageHelp(); setup(); loadGuide(); refresh(); setInterval(refresh, 1000);
