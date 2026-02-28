const dropSingle = document.getElementById('dropSingle');
const dropMulti = document.getElementById('dropMulti');
const fileSingleInput = document.getElementById('fileSingle');
const fileMultiInput = document.getElementById('fileMulti');
const multiClearBtn = document.getElementById('multiClear');
const multiHintEl = document.getElementById('multiHint');
const multiListEl = document.getElementById('multiList');

const fixedIntroInfo = document.getElementById('fixedIntroInfo');
const fixedOutroInfo = document.getElementById('fixedOutroInfo');
const fixedTailInfo = document.getElementById('fixedTailInfo');
const fixedIntroPick = document.getElementById('fixedIntroPick');
const fixedOutroPick = document.getElementById('fixedOutroPick');
const fixedTailPick = document.getElementById('fixedTailPick');
const fixedIntroFile = document.getElementById('fixedIntroFile');
const fixedOutroFile = document.getElementById('fixedOutroFile');
const fixedTailFile = document.getElementById('fixedTailFile');
const fixedIntroPlayer = document.getElementById('fixedIntroPlayer');
const fixedOutroPlayer = document.getElementById('fixedOutroPlayer');
const fixedTailPlayer = document.getElementById('fixedTailPlayer');

const jobSelect = document.getElementById('jobSelect');
const jobRefreshBtn = document.getElementById('jobRefresh');
const jobLoadBtn = document.getElementById('jobLoad');
const candidateListEl = document.getElementById('candidateList');
const candidateSummaryEl = document.getElementById('candidateSelectionSummary');
const stitchOutputNameInput = document.getElementById('stitchOutputName');
const stitchOutputFormatSelect = document.getElementById('stitchOutputFormat');
const stitchPartsBtn = document.getElementById('stitchPartsBtn');
const stitchAutoPickBtn = document.getElementById('stitchAutoPick');
const stitchPartsResultEl = document.getElementById('stitchPartsResult');

const STORAGE_THEME = "notebooklm.uiTheme";
const THEME_DARK = "dark";
const THEME_LIGHT = "light";

const transitionEnabledEl = document.getElementById('transitionEnabled');
const transitionFadeEl = document.getElementById('transitionFade');
const transitionListEl = document.getElementById('transitionList');

function fmtTs(ts){
  const raw = String(ts || "").trim();
  if (!raw) return "-";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())){
    return raw.replace("T"," ").replace("Z","").replace(/\.\d+\+00:00$/,"");
  }
  const fmt = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  try{
    const parts = fmt.formatToParts(d);
    const out = {};
    for (const p of parts) out[p.type] = p.value;
    return `${out.year}-${out.month}-${out.day} ${out.hour}:${out.minute}:${out.second}`;
  }catch{
    return fmt.format(d);
  }
}

const importedBox = document.getElementById('importedBox');
const importedName = document.getElementById('importedName');
const importedClear = document.getElementById('importedClear');

const buildBtn = document.getElementById('build');
const openBtn = document.getElementById('openOutput');
const repeatInput = document.getElementById('repeat');
const qualitySelect = document.getElementById('quality');
const outputNameInput = document.getElementById('outputName');
const pickedSingleEl = document.getElementById('pickedSingle');
const pickedMultiEl = document.getElementById('pickedMulti');
const pickedActiveEl = document.getElementById('pickedActive');
const statusEl = document.getElementById('status');
const resultEl = document.getElementById('result');
const bar = document.getElementById('bar');
const progressText = document.getElementById('progressText');

let singleFile = null;
let multiFiles = [];
let lastOutputFile = null;
let currentEventSource = null;
let importSource = null;
let candidateSegments = 0;
let candidateSelection = {};
const waveformCache = new Map();
const waveformInflight = new Map();

async function uploadManualPartCandidate(jobId, part, file){
  if (!jobId) throw new Error("缺少任务 ID");
  if (!file) throw new Error("未选择文件");
  const fd = new FormData();
  fd.append("job_id", String(jobId));
  fd.append("part", String(part));
  fd.append("file", file);
  const res = await fetch("/concat/api/stitch-parts/upload", {
    method: "POST",
    body: fd,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok){
    throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
  }
  return data;
}

function renderCandidateSummary(){
  if (!candidateSummaryEl) return;
  const segs = Number(candidateSegments || 0);
  if (!segs){
    candidateSummaryEl.innerHTML = "";
    return;
  }
  const rows = [];
  for (let i=1;i<=segs;i++){
    const file = candidateSelection[i];
    const label = `第${i}段`;
    const text = file ? file : "未选择";
    rows.push(`<div class="row"><span>${label}</span><span class="file">${text}</span></div>`);
  }
  candidateSummaryEl.innerHTML = rows.join("");
}

let outputNameAuto = true;
let lastAutoOutputName = '';

const fixedUi = {
  intro: { infoEl: fixedIntroInfo, pickBtn: fixedIntroPick, fileEl: fixedIntroFile, playerEl: fixedIntroPlayer },
  outro: { infoEl: fixedOutroInfo, pickBtn: fixedOutroPick, fileEl: fixedOutroFile, playerEl: fixedOutroPlayer },
  tail: { infoEl: fixedTailInfo, pickBtn: fixedTailPick, fileEl: fixedTailFile, playerEl: fixedTailPlayer },
};

const STORAGE_STITCH_TRANSITIONS = "notebooklm.stitchTransitions";
const STORAGE_STITCH_TRANSITION_REPEATS = "notebooklm.stitchTransitionRepeats";
const STORAGE_STITCH_TRANSITION_DURATIONS = "notebooklm.stitchTransitionDurations";
const DEFAULT_TRANSITIONS_BY_SEGMENTS = {
  3: [
    "assets/transitions/第一二段之间的链接-轻快活泼自由自在尤克里里.wav",
    "assets/transitions/第二三段之间的连接-欢快轻快节奏活力阳光.wav",
  ],
};
const DEFAULT_TRANSITION_DURATIONS_BY_SEGMENTS = {
  3: [30, 25],
};

function humanSize(bytes){
  const units = ['B','KB','MB','GB'];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function clamp(n, min, max){
  return Math.max(min, Math.min(max, n));
}

function _currentTheme(){
  return document.documentElement?.dataset?.theme || "dark";
}

function drawWaveform(canvas, peaks, segments, durationSeconds){
  if (!canvas || !peaks || !peaks.length) return;
  const theme = _currentTheme();
  const width = canvas.clientWidth || 320;
  const height = canvas.clientHeight || 64;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(width * dpr));
  canvas.height = Math.max(1, Math.floor(height * dpr));
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  const baseFill = theme === "light" ? "rgba(30,26,36,0.08)" : "rgba(255,255,255,0.06)";
  ctx.fillStyle = baseFill;
  ctx.fillRect(0, 0, width, height);

  if (durationSeconds && segments && segments.length){
    ctx.fillStyle = "rgba(255, 80, 80, 0.28)";
    for (const seg of segments){
      const start = Math.max(0, Number(seg.start_s || 0));
      const end = Math.max(start, Number(seg.end_s || 0));
      if (durationSeconds <= 0) continue;
      const x = (start / durationSeconds) * width;
      const w = Math.max(1, ((end - start) / durationSeconds) * width);
      ctx.fillRect(x, 0, w, height);
    }
  }

  const mid = height / 2;
  const stroke = theme === "light" ? "rgba(30,26,36,0.65)" : "rgba(248,244,235,0.82)";
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 1;

  const step = width / peaks.length;
  ctx.beginPath();
  for (let i=0;i<peaks.length;i++){
    const v = Math.min(1, Math.max(0, Number(peaks[i]) || 0));
    const h = v * (height * 0.44);
    const x = i * step;
    ctx.moveTo(x, mid - h);
    ctx.lineTo(x, mid + h);
  }
  ctx.stroke();
  ctx.restore();
}

async function loadWaveform(jobId, file, canvas, hintEl){
  if (!jobId || !file || !canvas) return;
  if (canvas.dataset.loaded === "1") return;
  const key = `${jobId}::${file}`;
  if (waveformCache.has(key)){
    const data = waveformCache.get(key);
    drawWaveform(canvas, data.peaks || [], data.silence_segments || [], data.duration_seconds || 0);
    canvas.dataset.loaded = "1";
    if (hintEl){
      const count = (data.silence_segments || []).length;
      hintEl.textContent = count ? `静音区 ${count} 处` : "未检测到静音";
    }
    return;
  }
  if (hintEl) hintEl.textContent = "波形生成中…";
  let promise = waveformInflight.get(key);
  if (!promise){
    const url = `/api/jobs/${encodeURIComponent(jobId)}/waveform?file=${encodeURIComponent(file)}`;
    promise = fetch(url).then(async (r) => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok){
        throw new Error(data?.detail || data?.error || `HTTP ${r.status}`);
      }
      return data;
    });
    waveformInflight.set(key, promise);
  }
  try{
    const data = await promise;
    waveformCache.set(key, data);
    drawWaveform(canvas, data.peaks || [], data.silence_segments || [], data.duration_seconds || 0);
    canvas.dataset.loaded = "1";
    if (hintEl){
      const count = (data.silence_segments || []).length;
      hintEl.textContent = count ? `静音区 ${count} 处` : "未检测到静音";
    }
  }catch(e){
    if (hintEl) hintEl.textContent = `波形失败：${String(e)}`;
  }finally{
    waveformInflight.delete(key);
  }
}

function applyTheme(theme){
  const root = document.documentElement;
  const body = document.body;
  const normalized = theme === THEME_LIGHT ? THEME_LIGHT : THEME_DARK;
  root.dataset.theme = normalized;
  if (body) body.dataset.theme = normalized;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta){
    meta.setAttribute("content", normalized === THEME_LIGHT ? "#f6f1e8" : "#0b0b12");
  }
  const btn = document.getElementById("themeToggle");
  if (btn){
    btn.textContent = normalized === THEME_LIGHT ? "切换到暗色" : "切换到亮色";
  }
  try{
    localStorage.setItem(STORAGE_THEME, normalized);
  }catch{}
}

function initThemeToggle(){
  const saved = (() => {
    try{
      return localStorage.getItem(STORAGE_THEME);
    }catch{
      return null;
    }
  })();
  applyTheme(saved || THEME_DARK);
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme || THEME_DARK;
    applyTheme(current === THEME_LIGHT ? THEME_DARK : THEME_LIGHT);
  });
}

function _readJSON(key, fallback){
  try{
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  }catch{
    return fallback;
  }
}

function _writeJSON(key, value){
  try{
    localStorage.setItem(key, JSON.stringify(value));
  }catch{}
}

function _readTransitions(){
  return _readJSON(STORAGE_STITCH_TRANSITIONS, []);
}
function _writeTransitions(list){
  _writeJSON(STORAGE_STITCH_TRANSITIONS, list || []);
}
function _readTransitionRepeats(){
  return _readJSON(STORAGE_STITCH_TRANSITION_REPEATS, []);
}
function _writeTransitionRepeats(list){
  _writeJSON(STORAGE_STITCH_TRANSITION_REPEATS, list || []);
}
function _readTransitionDurations(){
  return _readJSON(STORAGE_STITCH_TRANSITION_DURATIONS, []);
}
function _writeTransitionDurations(list){
  _writeJSON(STORAGE_STITCH_TRANSITION_DURATIONS, list || []);
}

function _normalizeTransitions(list, segments){
  const gaps = Math.max(0, (segments || 0) - 1);
  const out = Array.isArray(list) ? list.slice(0, gaps) : [];
  while (out.length < gaps) out.push("");
  return out;
}
function _normalizeTransitionRepeats(list, segments){
  const gaps = Math.max(0, (segments || 0) - 1);
  const out = Array.isArray(list) ? list.slice(0, gaps) : [];
  for (let i=0;i<out.length;i++){
    const n = parseInt(String(out[i] ?? "1"), 10);
    out[i] = Number.isFinite(n) ? Math.max(0, Math.min(n, 5)) : 1;
  }
  while (out.length < gaps) out.push(1);
  return out;
}
function _normalizeTransitionDurations(list, segments){
  const gaps = Math.max(0, (segments || 0) - 1);
  const out = Array.isArray(list) ? list.slice(0, gaps) : [];
  for (let i=0;i<out.length;i++){
    const n = parseFloat(String(out[i] ?? "0"));
    out[i] = Number.isFinite(n) ? Math.max(0, n) : 0;
  }
  while (out.length < gaps) out.push(30);
  return out;
}
function _defaultTransitions(segments){
  return (DEFAULT_TRANSITIONS_BY_SEGMENTS[segments] || []).slice();
}
function _defaultTransitionDurations(segments){
  return (DEFAULT_TRANSITION_DURATIONS_BY_SEGMENTS[segments] || []).slice();
}

function defaultOutputNameFromFileName(fileName){
  const idx = fileName.lastIndexOf('.');
  const base = idx > 0 ? fileName.slice(0, idx) : fileName;
  return `${base}.mp3`;
}

function tomorrowOutputStem(){
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `刘润早间新闻-${y}-${m}-${day}`;
}

function setStatus(text){
  statusEl.textContent = text;
  statusEl.classList.remove('muted');
}

function setStatusMuted(text){
  statusEl.textContent = text;
  statusEl.classList.add('muted');
}

function setProgress(pct, text){
  const p = clamp(pct, 0, 100);
  bar.style.width = `${p.toFixed(2)}%`;
  progressText.textContent = text || `${p.toFixed(0)}%`;
  progressText.classList.remove('muted');
}

function resetProgress(){
  bar.style.width = '0%';
  progressText.textContent = '0%';
  progressText.classList.add('muted');
}

function resetResult(){
  resultEl.innerHTML = '';
  lastOutputFile = null;
  openBtn.disabled = true;
  resetProgress();
}

function setImportSource(source){
  if (!source || !source.file) {
    importSource = null;
  } else {
    importSource = {
      kind: source.kind || 'job',
      jobId: source.jobId ? String(source.jobId) : '',
      file: String(source.file),
    };
  }
  if (importedBox){
    if (importSource){
      importedBox.style.display = 'flex';
      if (importedName) importedName.textContent = importSource.file;
    } else {
      importedBox.style.display = 'none';
      if (importedName) importedName.textContent = '';
    }
  }
  updateActiveUI();
}

function clearImportSource(){
  setImportSource(null);
}

const stageMap = {
  analyzing: '分析主音频',
  preparing_fixed: '准备固定片头/片尾',
  transcoding_main: '转码主音频',
  combining_main: '合并主音频',
  concatenating: '拼接输出',
  finalizing: '读取结果信息',
  done: '完成',
  error: '失败',
};

function startWatchingJob(data){
  if (!data) return;
  if (currentEventSource) {
    try { currentEventSource.close(); } catch(_){}
    currentEventSource = null;
  }

  lastOutputFile = data.outputFile || lastOutputFile;
  buildBtn.disabled = true;
  setProgress(15, '上传完成，等待处理…');
  setStatus('上传完成，开始处理…');

  const eventsUrl = data.eventsUrl || `/concat/api/jobs/${encodeURIComponent(data.jobId || '')}/events`;
  if (!eventsUrl) {
    setStatus('无法订阅任务事件');
    buildBtn.disabled = false;
    return;
  }

  const es = new EventSource(eventsUrl);
  currentEventSource = es;

  let transcodePct = 0;
  const sec = (ms) => (ms/1000).toFixed(2) + 's';

  const setStageProgress = (stage, message) => {
    const label = stageMap[stage] || stage;
    setStatus(message || label);

    if (stage === 'analyzing') setProgress(16, message || '分析主音频…');
    else if (stage === 'preparing_fixed') setProgress(18, message || '准备固定音频…');
    else if (stage === 'transcoding_main') setProgress(20, message || '转码主音频…');
    else if (stage === 'combining_main') setProgress(95, message || '合并主音频…');
    else if (stage === 'concatenating') setProgress(96, message || '拼接输出…');
    else if (stage === 'finalizing') setProgress(98, message || '读取结果信息…');
  };

  es.addEventListener('stage', (e) => {
    try {
      const payload = JSON.parse(e.data);
      setStageProgress(payload.stage, payload.message);
    } catch(_) {}
  });

  es.addEventListener('progress', (e) => {
    try {
      const payload = JSON.parse(e.data);
      if (payload.stage !== 'transcoding_main') return;
      transcodePct = Math.max(0, Math.min(Number(payload.pct || 0), 1));
      const overall = 20 + transcodePct * 75;
      const pctText = (transcodePct * 100).toFixed(1) + '%';
      const extra = [];
      if (payload.part && payload.parts) extra.push(`${payload.part}/${payload.parts}`);
      if (payload.speed) extra.push(payload.speed);
      const extraText = extra.length ? `（${extra.join('，')}）` : '';
      setProgress(overall, `主音频 ${pctText}${extraText}`);
    } catch(_) {}
  });

  es.addEventListener('done', (e) => {
    try {
      const payload = JSON.parse(e.data);
      lastOutputFile = payload.outputFile || lastOutputFile;
      openBtn.disabled = false;
      setProgress(100, '完成 100%');
      setStatus(`完成，总耗时 ${sec(payload.elapsedMs)}`);
      const durationSeconds = Number(payload.durationSeconds);
      const durationInt = Number.isFinite(durationSeconds) ? Math.round(durationSeconds) : null;
      const durationText = durationInt === null ? '未知' : `${durationInt}s`;
      resultEl.innerHTML = '';
      if (lastOutputFile) {
        const nameLine = document.createElement('div');
        nameLine.className = 'result-line';
        const meta = durationText ? ` · ${durationText}` : '';
        nameLine.textContent = `输出：${lastOutputFile}${meta}`;
        const dl = document.createElement('a');
        dl.className = 'btn';
        dl.textContent = '下载';
        dl.target = '_blank';
        dl.href = payload.downloadUrl || `/concat/download/${encodeURIComponent(lastOutputFile)}`;
        resultEl.append(nameLine, dl);
      }
    } catch (err) {
      setStatus('完成');
      setProgress(100, '完成 100%');
    } finally {
      try { es.close(); } catch(_){}
      if (currentEventSource === es) currentEventSource = null;
      buildBtn.disabled = false;
    }
  });

  es.addEventListener('job_error', (e) => {
    try {
      const payload = JSON.parse(e.data);
      setStatus('失败：' + (payload.message || '处理失败'));
    } catch(_) {
      setStatus('失败：处理失败');
    }
    setProgress(0, '处理失败');
    try { es.close(); } catch(_){}
    if (currentEventSource === es) currentEventSource = null;
    buildBtn.disabled = false;
  });
}

async function loadJobFromUrl(){
  const params = new URLSearchParams(location.search);
  const jobId = params.get('job');
  if (!jobId) return false;
  resetResult();
  setStatus('已导入任务，加载中…');
  try{
    const r = await fetch(`/concat/api/jobs/${encodeURIComponent(jobId)}`);
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok){
      throw new Error(data?.detail || data?.error || `HTTP ${r.status}`);
    }
    startWatchingJob({
      ...data,
      eventsUrl: `/concat/api/jobs/${encodeURIComponent(jobId)}/events`,
    });
    return true;
  }catch(e){
    setStatus(`导入失败：${String(e)}`);
    buildBtn.disabled = false;
    return false;
  }
}

async function importFromUrl(){
  const params = new URLSearchParams(location.search);
  const jobId = params.get('import_job') || params.get('importJob') || params.get('import');
  const file = params.get('file');
  if (!jobId || !file) return false;

  resetResult();
  setStatus('已导入文件，等待拼接…');

  singleFile = null;
  multiFiles = [];
  updateSingleUI();
  updateMultiUI();
  setImportSource({ kind: 'job', jobId, file });
  if (outputNameInput){
    const name = defaultOutputNameFromFileName(file);
    suggestOutputName(name);
  }
  return true;
}

function fileSort(a, b){
  return a.name.localeCompare(b.name, 'zh-CN', { numeric: true, sensitivity: 'base' });
}

async function loadJobsList(){
  if (!jobSelect) return;
  try{
    const res = await fetch('/api/jobs');
    const jobs = await res.json();
    jobSelect.innerHTML = '';
    if (!Array.isArray(jobs) || jobs.length === 0){
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '暂无任务';
      jobSelect.append(opt);
      return;
    }
    for (const j of jobs){
      const opt = document.createElement('option');
      opt.value = j.id;
      const created = j.created_at ? fmtTs(j.created_at) : '';
      const title = j.config?.split_enabled ? `分段×${j.config?.split_segments || '?'}` : '整段';
      opt.textContent = `${created} · ${title} · ${j.id}`;
      jobSelect.append(opt);
    }
  }catch(e){
    if (jobSelect){
      jobSelect.innerHTML = '';
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '加载失败';
      jobSelect.append(opt);
    }
  }
}

async function loadCandidatesForJob(jobId, opts = {}){
  if (!candidateListEl) return;
  const keepSelection = !!opts?.keepSelection;
  const prevSelection = keepSelection ? { ...candidateSelection } : {};
  candidateListEl.innerHTML = '<div class="muted">加载候选中…</div>';
  candidateSelection = {};
  candidateSegments = 0;
  renderCandidateSummary();
  try{
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (!res.ok) throw new Error(await res.text());
    const job = await res.json();

    const evRes = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/event_log?limit=20000`);
    const evData = evRes.ok ? await evRes.json() : {};
    const events = Array.isArray(evData?.events) ? evData.events : [];

    const byPart = {};
    for (const ev of events){
      if (String(ev?.type || '') !== 'part_accepted') continue;
      const part = Number(ev.part || 0);
      const file = String(ev.file || "").trim();
      if (!part || !file) continue;
      if (!byPart[part]) byPart[part] = [];
      byPart[part].push({
        file,
        duration: Number(ev.duration_minutes || 0),
        account: ev.account_name || ev.account_id || '',
        manual: false,
      });
    }

    const manualRes = await fetch(`/concat/api/stitch-parts/manual?job_id=${encodeURIComponent(jobId)}`);
    const manualData = manualRes.ok ? await manualRes.json().catch(() => ({})) : {};
    const manualItems = Array.isArray(manualData?.items) ? manualData.items : [];
    for (const item of manualItems){
      const part = Number(item?.part || 0);
      const file = String(item?.file || "").trim();
      if (!part || !file) continue;
      if (!byPart[part]) byPart[part] = [];
      byPart[part].push({
        file,
        duration: Number(item?.durationSeconds || 0) / 60,
        account: "手动上传",
        manual: true,
      });
    }

    for (const key of Object.keys(byPart)){
      const rows = Array.isArray(byPart[key]) ? byPart[key] : [];
      const dedup = new Map();
      for (const row of rows){
        const file = String(row?.file || "");
        if (!file || dedup.has(file)) continue;
        dedup.set(file, row);
      }
      byPart[key] = Array.from(dedup.values());
    }

    const maxPartFromData = Object.keys(byPart)
      .map((k) => Number(k))
      .filter((v) => Number.isFinite(v) && v > 0)
      .reduce((m, v) => Math.max(m, v), 0);
    candidateSegments = Math.max(Number(job?.config?.split_segments || 0), maxPartFromData);

    for (let i = 1; i <= candidateSegments; i++){
      const picked = String(prevSelection[i] || "");
      if (!picked) continue;
      const exists = (byPart[i] || []).some((item) => String(item?.file || "") === picked);
      if (exists) candidateSelection[i] = picked;
    }

    const preferPart = Number(opts?.preferPart || 0);
    const preferFile = String(opts?.preferFile || "");
    if (preferPart > 0 && preferFile){
      const exists = (byPart[preferPart] || []).some((item) => String(item?.file || "") === preferFile);
      if (exists) candidateSelection[preferPart] = preferFile;
    }

    renderCandidates(jobId, byPart, candidateSegments);
    renderTransitionListForSegments(candidateSegments || 0);
    renderCandidateSummary();
  }catch(e){
    candidateListEl.innerHTML = `<div class="muted">加载失败：${String(e)}</div>`;
  }
}

function renderCandidates(jobId, byPart, segments){
  if (!candidateListEl) return;
  candidateListEl.innerHTML = '';

  const segs = Number(segments || 0);
  if (!segs){
    candidateListEl.innerHTML = '<div class="muted">未检测到分段信息。</div>';
    return;
  }

  for (let i=1;i<=segs;i++){
    const items = Array.isArray(byPart?.[i]) ? byPart[i].slice() : [];
    items.sort((a,b) => (b.duration || 0) - (a.duration || 0));

    const group = document.createElement('div');
    group.className = 'candidateGroup';

    const head = document.createElement('div');
    head.className = 'candidateHead';
    const left = document.createElement('div');
    left.className = 'candidateHeadLeft';
    const title = document.createElement('strong');
    title.textContent = `第 ${i} 段`;
    const count = document.createElement('span');
    const manualCount = items.filter((it) => !!it?.manual).length;
    count.className = 'muted';
    count.textContent = manualCount > 0 ? `${items.length} 条候选（手动 ${manualCount}）` : `${items.length} 条候选`;
    left.append(title, count);

    const right = document.createElement('div');
    right.className = 'candidateHeadRight';
    const uploadBtn = document.createElement('button');
    uploadBtn.type = 'button';
    uploadBtn.className = 'btn secondary mini';
    uploadBtn.textContent = '上传补位音频';
    const uploadHint = document.createElement('span');
    uploadHint.className = 'candidateUploadHint muted';
    uploadHint.textContent = '';
    const uploadInput = document.createElement('input');
    uploadInput.type = 'file';
    uploadInput.accept = 'audio/*';
    uploadInput.className = 'candidateUploadInput';

    uploadBtn.addEventListener('click', () => uploadInput.click());
    uploadInput.addEventListener('change', async () => {
      const file = uploadInput.files?.[0];
      uploadInput.value = '';
      if (!file) return;
      const oldText = uploadBtn.textContent;
      uploadBtn.disabled = true;
      uploadBtn.textContent = '上传中…';
      uploadHint.textContent = file.name;
      try{
        const data = await uploadManualPartCandidate(jobId, i, file);
        const uploaded = String(data?.file || "").trim();
        await loadCandidatesForJob(jobId, {
          keepSelection: true,
          preferPart: i,
          preferFile: uploaded,
        });
        if (uploaded){
          setStatus(`第 ${i} 段补位音频已上传：${uploaded}`);
        } else {
          setStatus(`第 ${i} 段补位音频上传成功`);
        }
      }catch(e){
        alert(`第 ${i} 段补位音频上传失败：${String(e)}`);
      }finally{
        uploadBtn.disabled = false;
        uploadBtn.textContent = oldText;
        uploadHint.textContent = '';
      }
    });

    right.append(uploadBtn, uploadHint, uploadInput);
    head.append(left, right);

    const list = document.createElement('div');
    list.className = 'candidateItems';

    if (!items.length){
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = '暂无候选，可直接上传补位音频。';
      list.append(empty);
    } else {
      for (const item of items){
        const row = document.createElement('label');
        row.className = 'candidateItem';
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = `part-${i}`;
        radio.value = item.file;
        const isSelected = candidateSelection[i] === item.file;
        if (isSelected) {
          radio.checked = true;
          row.classList.add('selected');
        }
        radio.addEventListener('change', () => {
          if (radio.checked) {
            candidateSelection[i] = item.file;
            const group = row.closest('.candidateGroup');
            if (group){
              group.querySelectorAll('.candidateItem').forEach(el => el.classList.remove('selected'));
            }
            row.classList.add('selected');
            renderCandidateSummary();
            if (waveCanvas) loadWaveform(jobId, item.file, waveCanvas, waveHint);
          }
        });
        const meta = document.createElement('div');
        meta.className = 'candidateMeta';
        const dur = Number.isFinite(item.duration) ? `${item.duration.toFixed(2).replace(/\\.00$/,'')} min` : '-';
        const source = item.manual ? '手动上传' : (item.account || '');
        meta.textContent = `${dur} · ${source} · ${item.file}`;
        const audio = document.createElement('audio');
        audio.className = 'candidateAudio';
        audio.controls = true;
        audio.preload = 'none';
        audio.src = `/download/${encodeURIComponent(jobId)}/${encodeURIComponent(item.file)}`;

        const waveWrap = document.createElement('div');
        waveWrap.className = 'waveWrap';
        const waveHead = document.createElement('div');
        waveHead.className = 'waveHead';
        const waveBtn = document.createElement('button');
        waveBtn.type = 'button';
        waveBtn.className = 'btn secondary mini';
        waveBtn.textContent = '显示波形';
        waveBtn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          loadWaveform(jobId, item.file, waveCanvas, waveHint);
        });
        const waveHint = document.createElement('span');
        waveHint.className = 'waveHint muted';
        waveHint.textContent = '未加载';
        waveHead.append(waveBtn, waveHint);
        const waveCanvas = document.createElement('canvas');
        waveCanvas.className = 'waveCanvas';
        waveCanvas.height = 64;
        waveWrap.append(waveHead, waveCanvas);

        row.append(radio, meta, audio, waveWrap);
        list.append(row);
      }
    }

    group.append(head, list);
    candidateListEl.append(group);
  }
  renderCandidateSummary();
}

function autoPickLongest(){
  if (!candidateListEl) return;
  const groups = candidateListEl.querySelectorAll('.candidateGroup');
  groups.forEach(group => {
    const radios = group.querySelectorAll('input[type=radio]');
    if (radios.length > 0) {
      const first = radios[0];
      first.checked = true;
      const name = first.name;
      const part = Number(String(name || '').replace('part-',''));
      candidateSelection[part] = first.value;
      group.querySelectorAll('.candidateItem').forEach(el => el.classList.remove('selected'));
      const label = first.closest('.candidateItem');
      if (label) {
        label.classList.add('selected');
        const canvas = label.querySelector('.waveCanvas');
        const hint = label.querySelector('.waveHint');
        if (canvas) loadWaveform(jobSelect?.value, first.value, canvas, hint);
      }
    }
  });
  renderCandidateSummary();
}

function suggestOutputName(name){
  const current = (outputNameInput.value || '').trim();
  if (!current) {
    outputNameInput.value = name;
    outputNameAuto = true;
    lastAutoOutputName = name;
    return;
  }

  if (outputNameAuto || current === lastAutoOutputName) {
    outputNameInput.value = name;
    outputNameAuto = true;
    lastAutoOutputName = name;
  }
}

outputNameInput.addEventListener('input', () => {
  const v = (outputNameInput.value || '').trim();
  if (!v) {
    outputNameAuto = true;
    lastAutoOutputName = '';
    return;
  }
  if (v !== lastAutoOutputName) {
    outputNameAuto = false;
  }
});

function updateSingleUI(){
  if (!singleFile) {
    pickedSingleEl.textContent = '未选择';
    pickedSingleEl.classList.add('muted');
    return;
  }
  pickedSingleEl.textContent = `${singleFile.name} (${humanSize(singleFile.size)})`;
  pickedSingleEl.classList.remove('muted');
}

function updateMultiUI(){
  if (!multiFiles || multiFiles.length === 0) {
    pickedMultiEl.textContent = '未添加';
    pickedMultiEl.classList.add('muted');
    multiClearBtn.disabled = true;
    multiHintEl.textContent = '未添加';
    multiListEl.innerHTML = '';
    return;
  }

  pickedMultiEl.textContent = `${multiFiles.length} 段（按文件名排序）`;
  pickedMultiEl.classList.remove('muted');
  multiClearBtn.disabled = false;
  multiHintEl.textContent = `已添加 ${multiFiles.length} 段`;

  multiListEl.innerHTML = '';
  for (let i = 0; i < multiFiles.length; i++){
    const f = multiFiles[i];
    const li = document.createElement('li');
    li.className = 'file-item';

    const left = document.createElement('div');
    left.className = 'file-left';

    const idx = document.createElement('span');
    idx.className = 'file-idx';
    idx.textContent = String(i + 1);

    const meta = document.createElement('div');
    meta.className = 'file-meta';

    const name = document.createElement('div');
    name.className = 'file-name';
    name.textContent = f.name;
    name.title = f.name;

    const size = document.createElement('div');
    size.className = 'file-size';
    size.textContent = humanSize(f.size);

    meta.appendChild(name);
    meta.appendChild(size);

    left.appendChild(idx);
    left.appendChild(meta);

    li.appendChild(left);

    const rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'remove';
    rm.textContent = '移除';
    rm.addEventListener('click', () => removeMultiAt(i));
    li.appendChild(rm);

    multiListEl.appendChild(li);
  }
}

function getActive(){
  if (multiFiles && multiFiles.length > 0) return { mode: 'multi', files: multiFiles };
  if (singleFile) return { mode: 'single', files: [singleFile] };
  if (importSource) return { mode: 'import', files: [] };
  return { mode: 'none', files: [] };
}

function updateActiveUI(){
  const active = getActive();
  buildBtn.disabled = (active.mode === 'none');

  if (active.mode === 'multi') {
    pickedActiveEl.textContent = `多段主音频（${active.files.length} 段）`;
    pickedActiveEl.classList.remove('muted');
    suggestOutputName(tomorrowOutputStem());
    setStatus(`已添加多段主音频（${active.files.length} 段），等待开始`);
  } else if (active.mode === 'single') {
    pickedActiveEl.textContent = '单段主音频';
    pickedActiveEl.classList.remove('muted');
    suggestOutputName(defaultOutputNameFromFileName(singleFile.name));
    setStatus('已选择单段主音频，等待开始');
  } else if (active.mode === 'import') {
    pickedActiveEl.textContent = '导入主音频';
    pickedActiveEl.classList.remove('muted');
    if (importSource) {
      suggestOutputName(defaultOutputNameFromFileName(importSource.file));
    }
    setStatus('已导入主音频，等待开始');
  } else {
    pickedActiveEl.textContent = '未选择';
    pickedActiveEl.classList.add('muted');
    setStatusMuted('等待');
  }
}

function renderTransitionListForSegments(segments){
  if (!transitionListEl) return;
  const gaps = Math.max(0, (segments || 0) - 1);
  const list = _normalizeTransitions(_readTransitions(), segments);
  const repeats = _normalizeTransitionRepeats(_readTransitionRepeats(), segments);
  const durations = _normalizeTransitionDurations(_readTransitionDurations(), segments);

  if (gaps <= 0){
    transitionListEl.innerHTML = `<div class="muted">当前分段不足 2 段，无需过渡音频。</div>`;
    return;
  }

  const defaults = _defaultTransitions(segments);
  if (defaults.length && list.every(v => !v)){
    _writeTransitions(_normalizeTransitions(defaults, segments));
  }
  const defDur = _defaultTransitionDurations(segments);
  if (defDur.length){
    _writeTransitionDurations(_normalizeTransitionDurations(defDur, segments));
  }

  const uploadTransitionFile = async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/transitions/upload", { method: "POST", body: form });
    if (!res.ok){
      throw new Error(await res.text());
    }
    const data = await res.json();
    return String(data?.path || "");
  };

  transitionListEl.innerHTML = "";
  const transitions = _normalizeTransitions(_readTransitions(), segments);
  const rep = _normalizeTransitionRepeats(_readTransitionRepeats(), segments);
  const dur = _normalizeTransitionDurations(_readTransitionDurations(), segments);

  for (let i=1;i<=gaps;i++){
    const row = document.createElement("div");
    row.className = "transitionRow";
    const label = document.createElement("div");
    label.className = "muted";
    label.textContent = `第 ${i}-${i+1} 段过渡音频（重复/时长秒）`;

    const cell = document.createElement("div");
    cell.className = "transitionCell";

    const input = document.createElement("input");
    input.type = "text";
    input.className = "transitionInput";
    input.placeholder = "可选：填写本地路径，或拖拽音频文件到右侧";
    input.value = transitions[i - 1] || "";
    input.addEventListener("input", () => {
      const next = _normalizeTransitions(_readTransitions(), segments);
      next[i - 1] = String(input.value || "");
      _writeTransitions(next);
    });

    const repeat = document.createElement("input");
    repeat.type = "number";
    repeat.min = "0";
    repeat.max = "5";
    repeat.step = "1";
    repeat.className = "transitionRepeat";
    repeat.value = String(rep[i - 1] ?? 1);
    repeat.title = "过渡音频重复次数（0 表示不插入）";
    repeat.addEventListener("input", () => {
      const next = _normalizeTransitionRepeats(_readTransitionRepeats(), segments);
      next[i - 1] = parseInt(repeat.value || "1", 10);
      _writeTransitionRepeats(next);
    });

    const duration = document.createElement("input");
    duration.type = "number";
    duration.min = "0";
    duration.max = "600";
    duration.step = "1";
    duration.className = "transitionDuration";
    duration.value = String(dur[i - 1] ?? 30);
    duration.title = "过渡音频时长（秒，0 表示使用原始时长，超过则循环）";
    duration.addEventListener("input", () => {
      const next = _normalizeTransitionDurations(_readTransitionDurations(), segments);
      next[i - 1] = parseFloat(duration.value || "0");
      _writeTransitionDurations(next);
    });

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "audio/*";
    fileInput.className = "transitionFileInput";

    const drop = document.createElement("div");
    drop.className = "transitionDrop";
    drop.textContent = "拖拽或点击选择";

    const applyUploadedPath = (path) => {
      if (!path) return;
      input.value = path;
      const next = _normalizeTransitions(_readTransitions(), segments);
      next[i - 1] = String(path);
      _writeTransitions(next);
    };

    const handleFile = async (file) => {
      if (!file) return;
      drop.classList.add("loading");
      drop.textContent = "上传中…";
      try{
        const path = await uploadTransitionFile(file);
        applyUploadedPath(path);
      }catch(e){
        alert(`过渡音频上传失败：${String(e)}`);
      }finally{
        drop.classList.remove("loading");
        drop.textContent = "拖拽或点击选择";
      }
    };

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files?.[0];
      await handleFile(file);
      fileInput.value = "";
    });

    drop.addEventListener("click", () => fileInput.click());
    drop.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      drop.classList.add("drag");
    });
    drop.addEventListener("dragleave", () => {
      drop.classList.remove("drag");
    });
    drop.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      drop.classList.remove("drag");
      const file = ev.dataTransfer?.files?.[0];
      await handleFile(file);
    });

    cell.append(input, repeat, duration, drop, fileInput);
    row.append(label, cell);
    transitionListEl.append(row);
  }
}

function setSingleFile(file){
  if (!file) return;
  clearImportSource();
  singleFile = file;
  updateSingleUI();
  resetResult();
  updateActiveUI();
}

function addMultiFiles(fileList){
  const incoming = Array.from(fileList || []).filter(f => f && f.size > 0);
  if (incoming.length === 0) return;
  clearImportSource();

  const keyOf = (f) => `${f.name}::${f.size}::${f.lastModified}`;
  const map = new Map((multiFiles || []).map(f => [keyOf(f), f]));
  for (const f of incoming) map.set(keyOf(f), f);

  multiFiles = Array.from(map.values());
  multiFiles.sort(fileSort);

  updateMultiUI();
  resetResult();
  updateActiveUI();
}

function removeMultiAt(index){
  if (!multiFiles || index < 0 || index >= multiFiles.length) return;
  multiFiles.splice(index, 1);
  updateMultiUI();
  resetResult();
  updateActiveUI();
}

function clearMulti(){
  multiFiles = [];
  updateMultiUI();
  resetResult();
  updateActiveUI();
}

multiClearBtn.addEventListener('click', () => clearMulti());

function wireDrop(dropEl, onFiles){
  dropEl.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropEl.classList.add('dragover');
  });
  dropEl.addEventListener('dragleave', () => dropEl.classList.remove('dragover'));
  dropEl.addEventListener('drop', (e) => {
    e.preventDefault();
    dropEl.classList.remove('dragover');
    const fl = e.dataTransfer.files;
    if (fl && fl.length > 0) onFiles(fl);
  });
}

wireDrop(dropSingle, (fl) => setSingleFile(fl[0]));
wireDrop(dropMulti, (fl) => addMultiFiles(fl));

fileSingleInput.addEventListener('change', () => {
  const fl = fileSingleInput.files;
  if (fl && fl.length > 0) setSingleFile(fl[0]);
  fileSingleInput.value = '';
});

fileMultiInput.addEventListener('change', () => {
  const fl = fileMultiInput.files;
  if (fl && fl.length > 0) addMultiFiles(fl);
  fileMultiInput.value = '';
});

importedClear?.addEventListener('click', () => {
  clearImportSource();
  updateActiveUI();
});

buildBtn.addEventListener('click', async () => {
  const active = getActive();
  if (active.mode === 'none') return;
  buildBtn.disabled = true;
  resetResult();
  setStatus(active.mode === 'import' ? '开始拼接…' : '上传中…');
  setProgress(0, active.mode === 'import' ? '准备中 0%' : '上传中 0%');

  if (active.mode === 'import' && importSource){
    try{
      const repeat = parseInt(repeatInput.value || '3', 10);
      const quality = parseInt(qualitySelect.value || '5', 10);
      const endpoint = importSource.kind === 'concat' ? '/concat/api/import-output' : '/concat/api/import';
      const payload = (importSource.kind === 'concat')
        ? {
            file: importSource.file,
            repeat: Number.isFinite(repeat) ? repeat : 3,
            quality: Number.isFinite(quality) ? quality : 5,
            output_name: outputNameInput.value || '',
          }
        : {
            job_id: importSource.jobId,
            file: importSource.file,
            repeat: Number.isFinite(repeat) ? repeat : 3,
            quality: Number.isFinite(quality) ? quality : 5,
            output_name: outputNameInput.value || '',
          };
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok){
        throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      }
      startWatchingJob(data);
      return;
    }catch(e){
      setStatus('失败：' + String(e));
      setProgress(0, '处理失败');
      buildBtn.disabled = false;
      return;
    }
  }

  const fd = new FormData();
  for (const f of active.files) fd.append('mainAudio', f);
  fd.append('repeat', repeatInput.value || '3');
  fd.append('quality', qualitySelect.value || '5');
  fd.append('outputName', outputNameInput.value || '');

  if (currentEventSource) {
    try { currentEventSource.close(); } catch(_){}
    currentEventSource = null;
  }

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/concat/api/jobs');
  xhr.responseType = 'json';

  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const uploadPct = e.total > 0 ? e.loaded / e.total : 0;
    setProgress(uploadPct * 15, `上传中 ${(uploadPct * 100).toFixed(0)}%`);
  };

  xhr.onerror = () => {
    setStatus('失败：网络错误');
    setProgress(0, '上传失败');
    buildBtn.disabled = false;
  };

  xhr.onload = () => {
    const data = xhr.response || {};
    if (xhr.status < 200 || xhr.status >= 300 || !data.ok) {
      const msg = data?.detail || data?.error || `请求失败（HTTP ${xhr.status}）`;
      setStatus('失败：' + msg);
      setProgress(0, '处理失败');
      buildBtn.disabled = false;
      return;
    }

    startWatchingJob(data);
  };

  xhr.send(fd);
});

async function copyTextToClipboard(text){
  try {
    if (navigator.clipboard && (window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) {
    // fallback below
  }

  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

function fixedLabel(kind){
  if (kind === 'intro') return '片头';
  if (kind === 'outro') return '片尾';
  if (kind === 'tail') return '片尾音乐';
  return kind;
}

async function loadFixedInfo(){
  try {
    const r = await fetch('/concat/api/fixed', { method: 'GET' });
    const data = await r.json();
    if (!r.ok || !data || !data.ok) {
      throw new Error(data?.error || `HTTP ${r.status}`);
    }

    const items = Array.isArray(data.items) ? data.items : [];
    for (const item of items){
      const kind = String(item.kind || '');
      const ui = fixedUi[kind];
      if (!ui) continue;

      const exists = Boolean(item.exists);
      const fileName = item.fileName ? String(item.fileName) : '';
      const sizeBytes = typeof item.sizeBytes === 'number' ? item.sizeBytes : 0;
      const durationSeconds = typeof item.durationSeconds === 'number' ? item.durationSeconds : 0;
      const lastWriteUnixMs = typeof item.lastWriteUnixMs === 'number' ? item.lastWriteUnixMs : Date.now();
      const url = item.url ? String(item.url) : `/concat/fixed/${encodeURIComponent(kind)}`;

      const sizeText = sizeBytes > 0 ? humanSize(sizeBytes) : '';
      const durText = durationSeconds > 0 ? `${durationSeconds}s` : '';

      if (!exists) {
        ui.infoEl.textContent = `缺失：${fileName || fixedLabel(kind)}`;
        ui.infoEl.classList.remove('muted');
        ui.playerEl.removeAttribute('src');
      } else {
        const parts = [fileName];
        if (sizeText) parts.push(sizeText);
        if (durText) parts.push(durText);
        ui.infoEl.textContent = parts.join(' · ');
        ui.infoEl.classList.add('muted');
        ui.playerEl.src = `${url}?v=${encodeURIComponent(String(lastWriteUnixMs))}`;
      }
    }
  } catch (err) {
    for (const kind of Object.keys(fixedUi)) {
      const ui = fixedUi[kind];
      ui.infoEl.textContent = '固定音频信息加载失败';
      ui.infoEl.classList.remove('muted');
    }
  }
}

async function uploadFixed(kind, file){
  const ui = fixedUi[kind];
  const label = fixedLabel(kind);
  if (!ui) return;
  if (!file) return;

  if (!/\.mp3$/i.test(file.name)) {
    setStatus(`失败：${label} 仅支持 .mp3`);
    return;
  }

  try {
    ui.pickBtn.disabled = true;
    setStatus(`上传${label}…`);
    setProgress(0, `上传${label} 0%`);

    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`/concat/api/concat/fixed/${encodeURIComponent(kind)}`, { method: 'POST', body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      throw new Error(data?.error || data?.detail || `HTTP ${r.status}`);
    }

    setStatus(`${label}已更新`);
    resetProgress();
    await loadFixedInfo();
  } catch (e) {
    setStatus(`失败：${e?.message || '更新固定音频失败'}`);
    setProgress(0, '失败');
  } finally {
    ui.pickBtn.disabled = false;
  }
}

function wireFixedPickers(){
  for (const kind of Object.keys(fixedUi)) {
    const ui = fixedUi[kind];
    if (!ui?.pickBtn || !ui?.fileEl) continue;

    ui.pickBtn.addEventListener('click', () => {
      ui.fileEl.click();
    });

    ui.fileEl.addEventListener('change', async () => {
      const file = ui.fileEl.files?.[0];
      ui.fileEl.value = '';
      if (!file) return;
      await uploadFixed(kind, file);
    });
  }
}

async function stitchParts(){
  const jobId = jobSelect?.value;
  if (!jobId){
    alert("请选择一个任务");
    return;
  }
  const segs = Number(candidateSegments || 0);
  if (!segs){
    alert("未加载候选音频");
    return;
  }
  const parts = [];
  for (let i=1;i<=segs;i++){
    const picked = candidateSelection[i];
    if (!picked){
      alert(`第 ${i} 段还没有选择候选`);
      return;
    }
    parts.push(picked);
  }

  stitchPartsBtn.disabled = true;
  stitchPartsResultEl.innerHTML = '<div class="muted">拼接处理中…</div>';

  try{
    const payload = {
      job_id: jobId,
      parts,
      output_name: stitchOutputNameInput?.value || "",
      output_format: stitchOutputFormatSelect?.value || "m4a",
      transition_enabled: !!transitionEnabledEl?.checked,
      transition_fade_seconds: parseFloat(transitionFadeEl?.value || "3"),
      transition_files: _normalizeTransitions(_readTransitions(), segs),
      transition_repeats: _normalizeTransitionRepeats(_readTransitionRepeats(), segs),
      transition_durations: _normalizeTransitionDurations(_readTransitionDurations(), segs),
    };
    const res = await fetch("/concat/api/stitch-parts", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok){
      throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
    }
    const duration = Number(data.durationSeconds || 0);
    const durText = duration > 0 ? `${duration}s` : "未知时长";
    const dl = data.downloadUrl || `/concat/download/${encodeURIComponent(data.outputFile)}`;
    stitchPartsResultEl.innerHTML = "";
    const line = document.createElement("div");
    line.className = "result-line";
    line.textContent = `主体拼接完成：${data.outputFile} · ${durText}`;
    const actions = document.createElement("div");
    actions.className = "actions compact";
    const a = document.createElement("a");
    a.href = dl;
    a.target = "_blank";
    a.textContent = "下载主体";
    a.className = "btn";
    const useBtn = document.createElement("button");
    useBtn.type = "button";
    useBtn.className = "btn secondary";
    useBtn.textContent = "作为主音频";
    useBtn.addEventListener("click", () => {
      setImportSource({ kind: "concat", file: data.outputFile });
      updateActiveUI();
    });
    actions.append(a, useBtn);
    stitchPartsResultEl.append(line, actions);
  }catch(e){
    stitchPartsResultEl.innerHTML = `<div class="muted">拼接失败：${String(e)}</div>`;
  }finally{
    stitchPartsBtn.disabled = false;
  }
}

openBtn.addEventListener('click', async () => {
  if (!lastOutputFile) return;
  try{
    await fetch(`/concat/api/open-output?file=${encodeURIComponent(lastOutputFile)}`, { method: 'POST' });
  }catch(_){}
});

initThemeToggle();
updateSingleUI();
updateMultiUI();
updateActiveUI();

wireFixedPickers();
loadFixedInfo().catch(() => {});
loadJobsList();
jobRefreshBtn?.addEventListener('click', loadJobsList);
jobLoadBtn?.addEventListener('click', () => {
  const jobId = jobSelect?.value;
  if (jobId) loadCandidatesForJob(jobId);
});
stitchAutoPickBtn?.addEventListener('click', autoPickLongest);
stitchPartsBtn?.addEventListener('click', stitchParts);
renderTransitionListForSegments(3);
loadJobFromUrl().then((loaded) => {
  if (!loaded) {
    importFromUrl();
  }
});
