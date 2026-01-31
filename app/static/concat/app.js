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

let outputNameAuto = true;
let lastAutoOutputName = '';

const fixedUi = {
  intro: { infoEl: fixedIntroInfo, pickBtn: fixedIntroPick, fileEl: fixedIntroFile, playerEl: fixedIntroPlayer },
  outro: { infoEl: fixedOutroInfo, pickBtn: fixedOutroPick, fileEl: fixedOutroFile, playerEl: fixedOutroPlayer },
  tail: { infoEl: fixedTailInfo, pickBtn: fixedTailPick, fileEl: fixedTailFile, playerEl: fixedTailPlayer },
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

function fileSort(a, b){
  return a.name.localeCompare(b.name, 'zh-CN', { numeric: true, sensitivity: 'base' });
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
  return { mode: 'none', files: [] };
}

function updateActiveUI(){
  const active = getActive();
  buildBtn.disabled = active.files.length === 0;

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
  } else {
    pickedActiveEl.textContent = '未选择';
    pickedActiveEl.classList.add('muted');
    setStatusMuted('等待');
  }
}

function setSingleFile(file){
  if (!file) return;
  singleFile = file;
  updateSingleUI();
  resetResult();
  updateActiveUI();
}

function addMultiFiles(fileList){
  const incoming = Array.from(fileList || []).filter(f => f && f.size > 0);
  if (incoming.length === 0) return;

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

buildBtn.addEventListener('click', async () => {
  const active = getActive();
  if (!active.files || active.files.length === 0) return;
  buildBtn.disabled = true;
  resetResult();
  setStatus('上传中…');
  setProgress(0, '上传中 0%');

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

    lastOutputFile = data.outputFile;
    setProgress(15, '上传完成，等待处理…');
    setStatus('上传完成，开始处理…');

    const sec = (ms) => (ms/1000).toFixed(2) + 's';
    const stageMap = {
      analyzing: '分析音频',
      preparing_fixed: '准备固定片头/片尾',
      transcoding_main: '转码主音频',
      combining_main: '合并主音频',
      concatenating: '拼接输出',
      finalizing: '读取结果信息',
      done: '完成',
      error: '失败',
    };

    const eventsUrl = data.eventsUrl;
    const es = new EventSource(eventsUrl);
    currentEventSource = es;

    let transcodePct = 0;

    const setStageProgress = (stage, message) => {
      const label = stageMap[stage] || stage;
      setStatus(message || label);

      if (stage === 'analyzing') setProgress(16, message || '分析音频…');
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
        transcodePct = clamp(Number(payload.pct || 0), 0, 1);
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
        lastOutputFile = payload.outputFile;
        openBtn.disabled = false;
        setProgress(100, '完成 100%');
        setStatus(`完成：总耗时 ${sec(payload.elapsedMs)}`);
        const durationSeconds = Number(payload.durationSeconds);
        const durationInt = Number.isFinite(durationSeconds) ? Math.round(durationSeconds) : null;
        const durationText = durationInt === null ? '未知' : String(durationInt);
        const latestTxt = payload.latestTxtPath ? String(payload.latestTxtPath) : '';
        resultEl.innerHTML =
          `<div>输出文件：<a href="${payload.downloadUrl}" download>${payload.outputFile}</a></div>` +
          `<div>时长（秒）：<span id="durationValue">${durationText}</span> ` +
            `<button id="copyDuration" class="secondary mini" type="button" ${durationInt === null ? 'disabled' : ''}>复制时长</button>` +
          `</div>` +
          `<div class="muted">保存位置：${payload.outputPath}</div>` +
          (latestTxt ? `<div class="muted">写入 TXT：${latestTxt}</div>` : '');

        const copyBtn = document.getElementById('copyDuration');
        if (copyBtn && durationInt !== null) {
          copyBtn.addEventListener('click', async () => {
            const ok = await copyTextToClipboard(String(durationInt));
            if (ok) {
              const old = copyBtn.textContent;
              copyBtn.textContent = '已复制';
              setTimeout(() => { copyBtn.textContent = old || '复制时长'; }, 1200);
            }
          });
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

    es.onerror = () => {
      // The server will close the stream when done; ignore auto-reconnect noise here.
    };
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
      const url = item.url ? String(item.url) : `/fixed/${encodeURIComponent(kind)}`;

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
    const r = await fetch(`/api/fixed/${encodeURIComponent(kind)}`, { method: 'POST', body: fd });
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

openBtn.addEventListener('click', async () => {
  if (!lastOutputFile) return;
  try{
    await fetch(`/api/open-output?file=${encodeURIComponent(lastOutputFile)}`, { method: 'POST' });
  }catch(_){}
});

updateSingleUI();
updateMultiUI();
updateActiveUI();

wireFixedPickers();
loadFixedInfo().catch(() => {});

