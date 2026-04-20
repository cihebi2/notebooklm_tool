const $ = (selector) => document.querySelector(selector);

const STORAGE_LAST_JOB = "reportExplain.lastJobId";
const STORAGE_THEME = "reportExplain.theme";
const JOB_TERMINAL = new Set(["succeeded", "failed"]);
const STAGES = ["queued", "running", "rendering", "succeeded"];
const STAGE_LABELS = {
  queued: "任务创建",
  running: "Codex 写作",
  rendering: "PDF 排版",
  succeeded: "结果就绪",
  failed: "执行失败",
};

const state = {
  files: [],
  parsedText: "",
  previewFileName: "",
  dragIndex: -1,
  jobId: "",
  currentJobData: null,
  cachedSourceJobId: "",
  pollTimer: null,
  isSubmitting: false,
  outputNameAuto: true,
  lastAutoName: "",
  recentJobs: [],
  batchJobs: [],
  batchErrors: [],
};

const dropzone = $("#dropzone");
const filePicker = $("#filePicker");
const fileStats = $("#fileStats");
const clearFileBtn = $("#clearFileBtn");
const outputNameInput = $("#outputName");
const outputModePdf = $("#outputModePdf");
const outputModeText = $("#outputModeText");
const selectedFiles = $("#selectedFiles");
const previewCaption = $("#previewCaption");
const reportPreview = $("#reportPreview");
const charCount = $("#charCount");
const promptText = $("#promptText");
const reloadPromptBtn = $("#reloadPromptBtn");
const startBtn = $("#startBtn");
const copyMarkdownBtn = $("#copyMarkdownBtn");
const copyPlainTextBtn = $("#copyPlainTextBtn");
const rerunJobBtn = $("#rerunJobBtn");
const openOutputBtn = $("#openOutputBtn");
const openResultPageBtn = $("#openResultPageBtn");
const refreshCacheBtn = $("#refreshCacheBtn");
const jobLookupInput = $("#jobLookupInput");
const loadJobBtn = $("#loadJobBtn");
const refreshLogsBtn = $("#refreshLogsBtn");
const jobStatusPill = $("#jobStatusPill");
const jobIdEl = $("#jobId");
const jobStageEl = $("#jobStage");
const sourceCharsEl = $("#sourceChars");
const promptCharsEl = $("#promptChars");
const cacheSourceNote = $("#cacheSourceNote");
const statusTextEl = $("#statusText");
const progressPercentEl = $("#progressPercent");
const progressFill = $("#progressFill");
const stageRail = $("#stageRail");
const resultBox = $("#resultBox");
const logMeta = $("#logMeta");
const eventFeed = $("#eventFeed");
const logBox = $("#logBox");
const recentJobs = $("#recentJobs");
const themeToggle = $("#themeToggle");

function readStorage(key){
  try { return localStorage.getItem(key) || ""; }
  catch (_) { return ""; }
}

function writeStorage(key, value){
  try {
    if (value) localStorage.setItem(key, value);
  } catch (_) {}
}

function clearStorage(key){
  try { localStorage.removeItem(key); }
  catch (_) {}
}

function saveLastJobId(jobId){ writeStorage(STORAGE_LAST_JOB, jobId); }
function readLastJobId(){ return readStorage(STORAGE_LAST_JOB); }
function clearLastJobId(){ clearStorage(STORAGE_LAST_JOB); }

function escapeHtml(value){
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function humanSize(bytes){
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes || 0);
  let index = 0;
  while (value >= 1024 && index < units.length - 1){
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function extOf(name){
  const index = String(name || "").lastIndexOf(".");
  return index >= 0 ? String(name).slice(index).toLowerCase() : "";
}

function fileStem(name){
  return String(name || "").replace(/\.pdf$/i, "").trim();
}

function fileKey(file){
  return [file.name, file.size, file.lastModified].join("::");
}

function formatDateTime(value){
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatDuration(seconds){
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "-";
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60);
  const remain = value % 60;
  return `${minutes}m ${remain}s`;
}

function getExportPdf(){
  return !!outputModePdf?.checked;
}

function outputModeLabel(exportPdf){
  return exportPdf ? "Markdown + PDF" : "仅文字排版";
}

async function copyText(text){
  const value = String(text || "").trim();
  if (!value){
    throw new Error("没有可复制的 Markdown 内容");
  }
  if (navigator.clipboard?.writeText){
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function markdownToPlainText(markdown){
  const text = String(markdown || "");
  return text
    .replace(/\r/g, "")
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^>\s?/gm, "")
    .replace(/^---$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function isQueueBusy(){
  return state.isSubmitting;
}

function setOutputMode(exportPdf){
  if (outputModePdf) outputModePdf.checked = !!exportPdf;
  if (outputModeText) outputModeText.checked = !exportPdf;
}

function setStatusPill(text, kind = "idle"){
  jobStatusPill.textContent = text;
  jobStatusPill.classList.remove("ok", "error", "busy");
  if (kind === "ok") jobStatusPill.classList.add("ok");
  else if (kind === "error") jobStatusPill.classList.add("error");
  else if (kind === "busy") jobStatusPill.classList.add("busy");
}

function updateActionButtons(){
  const fileCount = state.files.length;
  const hasCachedSource = !!state.cachedSourceJobId;
  const queueBusy = isQueueBusy();
  if (rerunJobBtn) rerunJobBtn.disabled = queueBusy || !hasCachedSource || fileCount > 0;
  if (copyMarkdownBtn){
    copyMarkdownBtn.disabled = !String(state.currentJobData?.markdownText || "").trim();
  }
  if (copyPlainTextBtn){
    copyPlainTextBtn.disabled = !String(state.currentJobData?.markdownText || "").trim();
  }
  if (!startBtn) return;
  startBtn.disabled = queueBusy || (!fileCount && !hasCachedSource);
  if (fileCount > 0){
    const modeText = getExportPdf() ? "解说稿" : "文字排版";
    startBtn.textContent = fileCount > 1 ? `并行生成 ${fileCount} 份${modeText}` : `开始生成${modeText}`;
    return;
  }
  startBtn.textContent = hasCachedSource ? "基于缓存重新生成" : "开始生成";
}

function setProgressState(data){
  const percent = Number(data?.progressPercent || 0);
  progressFill.style.width = `${percent}%`;
  progressPercentEl.textContent = `${Math.round(percent)}%`;

  const status = String(data?.status || "");
  if (!status){
    stageRail?.querySelectorAll(".stageNode").forEach((node) => {
      node.classList.remove("done", "active", "error");
    });
    return;
  }

  let activeStage = status;
  if (status === "failed") activeStage = data?.markdownReady ? "rendering" : "running";
  const activeIndex = Math.max(0, STAGES.indexOf(activeStage));

  stageRail?.querySelectorAll(".stageNode").forEach((node) => {
    const stage = node.dataset.stage || "";
    const index = STAGES.indexOf(stage);
    node.classList.remove("done", "active", "error");
    if (status === "failed" && stage === activeStage){
      node.classList.add("error");
      return;
    }
    if (index < activeIndex || (status === "succeeded" && index <= activeIndex)){
      node.classList.add("done");
      return;
    }
    if (index === activeIndex && !JOB_TERMINAL.has(status)){
      node.classList.add("active");
    }
  });
}

function renderSelectedFiles(){
  if (!selectedFiles) return;
  if (!state.files.length){
    selectedFiles.innerHTML = '<div class="eventEmpty">暂无待处理文件</div>';
    return;
  }

  selectedFiles.innerHTML = state.files.map((file, index) => `
    <article class="selectedFileItem${index === 0 ? " selectedFileActive" : ""}" draggable="true" data-index="${index}">
      <div>
        <strong>${escapeHtml(file.name)}</strong>
        <div class="selectedFileMeta">${humanSize(file.size)} · ${index === 0 ? "当前预览" : "待并行处理"}</div>
      </div>
      <button class="secondary selectedFileRemove" type="button" data-index="${index}">移除</button>
    </article>
  `).join("");

  selectedFiles.querySelectorAll(".selectedFileRemove").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.index || -1);
      if (index >= 0) removeFileAt(index);
    });
  });

  selectedFiles.querySelectorAll(".selectedFileItem").forEach((item) => {
    item.addEventListener("dragstart", (event) => {
      state.dragIndex = Number(item.dataset.index || -1);
      item.classList.add("selectedFileDragging");
      if (event.dataTransfer){
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(state.dragIndex));
      }
    });

    item.addEventListener("dragend", () => {
      state.dragIndex = -1;
      item.classList.remove("selectedFileDragging");
      selectedFiles.querySelectorAll(".selectedFileDropTarget").forEach((node) => {
        node.classList.remove("selectedFileDropTarget");
      });
    });

    item.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (state.dragIndex < 0) return;
      item.classList.add("selectedFileDropTarget");
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    });

    item.addEventListener("dragleave", () => {
      item.classList.remove("selectedFileDropTarget");
    });

    item.addEventListener("drop", (event) => {
      event.preventDefault();
      item.classList.remove("selectedFileDropTarget");
      const targetIndex = Number(item.dataset.index || -1);
      if (targetIndex >= 0) reorderFile(state.dragIndex, targetIndex);
    });
  });
}

function reorderFile(fromIndex, toIndex){
  if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return;
  if (fromIndex >= state.files.length || toIndex >= state.files.length) return;
  const [moved] = state.files.splice(fromIndex, 1);
  state.files.splice(toIndex, 0, moved);
  syncOutputNameFromQueue();
  renderSelectedFiles();
  updateActionButtons();
  parsePreviewForFirstFile(state.files[0]);
}

function mergeBatchJobsWithRecent(){
  if (!state.batchJobs.length || !state.recentJobs.length) return;
  const recentMap = new Map(state.recentJobs.map((item) => [item.jobId, item]));
  state.batchJobs = state.batchJobs.map((item) => recentMap.get(item.jobId) ? { ...item, ...recentMap.get(item.jobId) } : item);
}

function renderBatchSummary(){
  if (state.batchJobs.length <= 1 && !state.batchErrors.length) return "";
  const cards = state.batchJobs.map((item) => {
    const statusClass = item.status === "succeeded" ? "ok" : item.status === "failed" ? "error" : "busy";
    const resultButton = item.resultPageUrl
      ? `<a class="actionLink secondaryLink" href="${item.resultPageUrl}" target="_blank" rel="noreferrer">打开结果页</a>`
      : "";
    return `
      <article class="batchCard">
        <div class="batchCardHead">
          <strong>${escapeHtml(item.outputBaseName || item.sourceFilename || item.jobId)}</strong>
          <span class="cacheStatus ${statusClass}">${escapeHtml(item.stageLabel || item.status || "-")}</span>
        </div>
        <div class="selectedFileMeta">${escapeHtml(item.sourceFilename || "")}</div>
        <div class="selectedFileMeta">模式：${escapeHtml(outputModeLabel(item.exportPdf !== false))}</div>
        <div class="resultLinks compactLinks">${resultButton}</div>
      </article>
    `;
  }).join("");

  const errors = state.batchErrors.length
    ? `<div class="resultLine error">失败 ${state.batchErrors.length} 个：${escapeHtml(state.batchErrors.join("；"))}</div>`
    : "";

  return `
    <section class="batchSection">
      <div class="resultLine"><strong>本次批量任务</strong> · 成功 ${state.batchJobs.length} 个${state.batchErrors.length ? `，失败 ${state.batchErrors.length} 个` : ""}</div>
      ${errors}
      <div class="batchList">${cards || '<div class="eventEmpty">暂无成功任务</div>'}</div>
    </section>
  `;
}

function renderResultBox(data){
  const batchSection = renderBatchSummary();
  if (!data?.jobId){
    resultBox.innerHTML = batchSection || '<div class="resultLine muted">暂无任务结果。</div>';
    return;
  }

  let summary = "";
  if (data.status === "succeeded"){
    const primaryButton = data.downloadPdfUrl
      ? `<a class="actionLink primaryLink" href="${data.downloadPdfUrl}" target="_blank" rel="noreferrer">下载 PDF</a>`
      : data.downloadMarkdownUrl
        ? `<a class="actionLink primaryLink" href="${data.downloadMarkdownUrl}" target="_blank" rel="noreferrer">下载 Markdown</a>`
        : "";
    const markdownButton = data.downloadMarkdownUrl && data.downloadPdfUrl
      ? `<a class="actionLink secondaryLink" href="${data.downloadMarkdownUrl}" target="_blank" rel="noreferrer">下载 Markdown</a>`
      : "";
    const resultButton = data.resultPageUrl
      ? `<a class="actionLink secondaryLink" href="${data.resultPageUrl}" target="_blank" rel="noreferrer">打开结果页</a>`
      : "";
    const preview = data.markdownPreview
      ? `<div class="resultLine resultPreviewText">预览：${escapeHtml(String(data.markdownPreview)).replace(/\n/g, "<br/>")}</div>`
      : "";
    summary = `
      <div class="resultLine"><strong>当前任务已完成</strong> · ${escapeHtml(outputModeLabel(data.exportPdf !== false))}</div>
      <div class="resultLine muted">完成时间：${escapeHtml(formatDateTime(data.completedAt))} · 总耗时：${escapeHtml(formatDuration(data.durationSeconds))}</div>
      <div class="resultLinks">${primaryButton}${markdownButton}${resultButton}</div>
      ${preview}
    `;
  } else if (data.status === "failed"){
    summary = `
      <div class="resultLine error">任务失败：${escapeHtml(data.error || data.message || "未知错误")}</div>
      <div class="resultLine muted">可以查看日志，也可以从缓存任务重新执行。</div>
    `;
  } else {
    summary = `
      <div class="resultLine"><strong>当前任务运行中</strong></div>
      <div class="resultLine muted">${escapeHtml(data?.message || "正在处理任务…")}</div>
    `;
  }

  resultBox.innerHTML = summary + batchSection;
}

function renderEventFeed(events){
  const rows = Array.isArray(events) ? events : [];
  if (!rows.length){
    eventFeed.innerHTML = '<div class="eventEmpty">暂无事件</div>';
    return;
  }

  eventFeed.innerHTML = rows.slice(-10).map((item) => {
    const level = escapeHtml(item.level || "info");
    const title = escapeHtml(item.title || "事件");
    const detail = escapeHtml(item.detail || "");
    const timestamp = escapeHtml(formatDateTime(item.timestamp));
    return `
      <article class="eventCard level-${level}">
        <div class="eventHead">
          <strong>${title}</strong>
          <span>${timestamp}</span>
        </div>
        <div class="eventStatus">${escapeHtml(STAGE_LABELS[item.status] || item.status || "")}</div>
        ${detail ? `<div class="eventDetail">${detail.replace(/\n/g, "<br/>")}</div>` : ""}
      </article>
    `;
  }).join("");
}

function renderLogs(data){
  renderEventFeed(data?.events || []);
  logMeta.textContent = state.jobId
    ? `任务 ${state.jobId} · 日志大小 ${humanSize(data?.logSize || 0)} · 更新时间 ${formatDateTime(data?.updatedAt)}`
    : "未选择任务";
  logBox.textContent = String(data?.logText || "暂无日志");
}

function renderRecentJobs(items){
  state.recentJobs = Array.isArray(items) ? items : [];
  mergeBatchJobsWithRecent();
  if (!state.recentJobs.length){
    recentJobs.innerHTML = '<div class="eventEmpty">暂无缓存任务</div>';
    renderResultBox(state.currentJobData || {});
    return;
  }

  recentJobs.innerHTML = state.recentJobs.map((item) => {
    const active = item.jobId === state.jobId ? " active" : "";
    const statusClass = item.status === "succeeded" ? "ok" : item.status === "failed" ? "error" : "busy";
    return `
      <button class="cacheItem${active}" type="button" data-job-id="${escapeHtml(item.jobId)}">
        <div class="cacheTitle">${escapeHtml(item.outputBaseName || item.sourceFilename || item.jobId)}</div>
        <div class="cacheMeta">
          <span class="cacheStatus ${statusClass}">${escapeHtml(item.stageLabel || item.status || "unknown")}</span>
          <span>${escapeHtml(formatDateTime(item.updatedAt || item.createdAt))}</span>
        </div>
        <div class="cacheSub">${escapeHtml(item.sourceFilename || "")}</div>
        <div class="cacheSub">${escapeHtml(outputModeLabel(item.exportPdf !== false))}</div>
      </button>
    `;
  }).join("");

  recentJobs.querySelectorAll(".cacheItem").forEach((button) => {
    button.addEventListener("click", async () => {
      const jobId = button.dataset.jobId || "";
      if (jobId) await loadJob(jobId, { pushQuery: true, adoptPrompt: true });
    });
  });

  renderResultBox(state.currentJobData || {});
}

function applyJobToUI(data, options = {}){
  const { keepBatch = false, adoptPrompt = false } = options;
  if (!keepBatch){
    state.batchJobs = [];
    state.batchErrors = [];
  }
  state.currentJobData = data || null;
  state.jobId = String(data?.jobId || "");
  state.cachedSourceJobId = data?.rerunReady ? state.jobId : "";
  if (state.jobId) saveLastJobId(state.jobId);

  jobIdEl.textContent = state.jobId || "-";
  jobStageEl.textContent = data?.stageLabel || STAGE_LABELS[data?.status] || "-";
  sourceCharsEl.textContent = data?.sourceChars ?? "-";
  promptCharsEl.textContent = data?.promptChars ?? String(promptText.value.length || 0);
  statusTextEl.textContent = data?.error ? `${data?.message || "任务失败"}：${data.error}` : (data?.message || "等待中");

  if (typeof data?.exportPdf === "boolean") setOutputMode(data.exportPdf);

  if (data?.status === "succeeded") setStatusPill("完成", "ok");
  else if (data?.status === "failed") setStatusPill("失败", "error");
  else if (state.jobId) setStatusPill(data?.stageLabel || "运行中", "busy");
  else setStatusPill("idle");

  setProgressState(data || {});
  renderResultBox(data || {});
  if (copyMarkdownBtn){
    copyMarkdownBtn.textContent = "一键复制 Markdown";
  }
  if (copyPlainTextBtn){
    copyPlainTextBtn.textContent = "一键复制纯文本";
  }

  if (adoptPrompt && typeof data?.promptText === "string" && data.promptText){
    promptText.value = data.promptText;
    promptCharsEl.textContent = String(promptText.value.length);
  }
  if (state.files.length === 0 && typeof data?.sourcePreview === "string"){
    state.parsedText = data.sourcePreview;
    state.previewFileName = data.sourceFilename || "";
    reportPreview.value = data.sourcePreview;
    charCount.textContent = String(data.sourceChars ?? data.sourcePreview.length);
    previewCaption.textContent = data.sourceFilename ? `${data.sourceFilename} · 缓存任务预览` : "缓存任务预览";
    fileStats.textContent = data.sourceFilename ? `${data.sourceFilename} · 缓存任务` : "缓存任务";
    if (data.outputBaseName) outputNameInput.value = data.outputBaseName;
  }

  if (cacheSourceNote){
    if (!state.jobId){
      cacheSourceNote.textContent = "加载缓存任务后，可以直接重跑，无需重新上传 PDF。";
    } else if (data?.rerunFromJobId) {
      cacheSourceNote.textContent = `当前任务由缓存任务 ${data.rerunFromJobId} 派生生成，可继续直接重跑。`;
    } else if (data?.rerunReady) {
      cacheSourceNote.textContent = `当前任务 ${state.jobId} 已具备缓存重跑条件，无需重新上传 PDF。`;
    } else {
      cacheSourceNote.textContent = "当前任务尚不具备缓存重跑条件。";
    }
  }

  openResultPageBtn.disabled = !data?.resultPageUrl;
  openResultPageBtn.dataset.href = data?.resultPageUrl || "";
  updateActionButtons();
}

async function fetchJson(url, options = {}){
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false){
    throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
  }
  return data;
}

async function parsePreviewForFirstFile(file){
  state.parsedText = "";
  state.previewFileName = "";
  reportPreview.value = "";
  charCount.textContent = "0";
  if (!file){
    previewCaption.textContent = "未选择文件";
    return;
  }

  previewCaption.textContent = state.files.length > 1
    ? `批量模式：预览第 1 个文件 ${file.name}`
    : `当前预览：${file.name}`;

  try{
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/parse-file", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data?.ok){
      throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
    }
    state.parsedText = String(data.text || "");
    state.previewFileName = file.name;
    reportPreview.value = state.parsedText.slice(0, 12000);
    charCount.textContent = String(state.parsedText.length);
  }catch(error){
    reportPreview.value = "";
    charCount.textContent = "0";
    previewCaption.textContent = `正文预览加载失败：${error?.message || error}`;
  }
}

async function setFiles(files, options = {}){
  const { append = true } = options;
  const incoming = Array.from(files || []).filter((file) => extOf(file.name) === ".pdf");
  if (!incoming.length){
    state.files = [];
    state.parsedText = "";
    state.previewFileName = "";
    reportPreview.value = "";
    charCount.textContent = "0";
    fileStats.textContent = "未选择";
    renderSelectedFiles();
    updateActionButtons();
    previewCaption.textContent = "未选择文件";
    return;
  }

  const baseList = append ? state.files.slice() : [];
  const seen = new Set(baseList.map(fileKey));
  for (const file of incoming){
    const key = fileKey(file);
    if (seen.has(key)) continue;
    seen.add(key);
    baseList.push(file);
  }

  state.files = baseList;
  const totalSize = state.files.reduce((sum, item) => sum + Number(item.size || 0), 0);
  fileStats.textContent = state.files.length === 1
    ? `${state.files[0].name} · ${humanSize(totalSize)}`
    : `${state.files.length} 个文件 · ${humanSize(totalSize)}`;
  syncOutputNameFromQueue();
  renderSelectedFiles();
  updateActionButtons();
  await parsePreviewForFirstFile(state.files[0]);
}

function removeFileAt(index){
  if (index < 0 || index >= state.files.length) return;
  state.files.splice(index, 1);
  if (!state.files.length){
    setFiles([]);
    return;
  }
  const totalSize = state.files.reduce((sum, item) => sum + Number(item.size || 0), 0);
  fileStats.textContent = state.files.length === 1
    ? `${state.files[0].name} · ${humanSize(totalSize)}`
    : `${state.files.length} 个文件 · ${humanSize(totalSize)}`;
  syncOutputNameFromQueue();
  renderSelectedFiles();
  parsePreviewForFirstFile(state.files[0]);
  updateActionButtons();
}

function suggestOutputName(fileName){
  const base = fileStem(fileName);
  const suggested = base ? `${base}（报告解说）` : "";
  if (!suggested) return;
  const current = String(outputNameInput?.value || "").trim();
  if (!current || state.outputNameAuto || current === state.lastAutoName){
    outputNameInput.value = suggested;
    state.lastAutoName = suggested;
    state.outputNameAuto = true;
  }
}

function syncOutputNameFromQueue(){
  if (!state.outputNameAuto) return;
  if (!state.files.length){
    outputNameInput.value = "";
    state.lastAutoName = "";
    return;
  }
  suggestOutputName(state.files[0].name);
}


function resolveOutputNameForFile(file, index, total){
  const prefix = String(outputNameInput.value || "").trim();
  const safeStem = fileStem(file.name) || `file-${index + 1}`;
  if (!prefix){
    return total === 1 ? "" : `${safeStem}（报告解说）`;
  }
  if (total === 1) return prefix;
  return `${prefix}-${index + 1}`;
}

async function loadDefaultPrompt(){
  const data = await fetchJson("/report-explain/api/default-prompt");
  promptText.value = String(data.promptText || "");
  promptCharsEl.textContent = String(promptText.value.length);
}

async function loadRecentJobs(){
  try{
    const data = await fetchJson("/report-explain/api/jobs?limit=20");
    renderRecentJobs(data.items || []);
  }catch(error){
    recentJobs.innerHTML = `<div class="eventEmpty">缓存任务加载失败：${escapeHtml(error?.message || error)}</div>`;
  }
}

async function loadJobLogs(jobId){
  if (!jobId){
    renderLogs({ events: [], logText: "" });
    return;
  }
  try{
    const data = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(jobId)}/logs?max_chars=32000&event_limit=16`);
    renderLogs(data);
  }catch(error){
    logMeta.textContent = `日志加载失败：${error?.message || error}`;
    logBox.textContent = "";
    eventFeed.innerHTML = '<div class="eventEmpty">日志加载失败</div>';
  }
}

async function loadJob(jobId, options = {}){
  const { pushQuery = false, keepBatch = false, adoptPrompt = false } = options;
  const data = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(jobId)}/detail`);
  applyJobToUI(data, { keepBatch, adoptPrompt });
  await loadJobLogs(jobId);
  await loadRecentJobs();

  if (pushQuery){
    const url = new URL(window.location.href);
    url.searchParams.set("job", jobId);
    history.replaceState({}, "", url.toString());
  }

  if (JOB_TERMINAL.has(String(data.status || ""))){
    stopPolling();
  } else {
    startPolling(jobId);
  }
  updateActionButtons();
}

function stopPolling(){
  if (state.pollTimer){
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startPolling(jobId){
  stopPolling();
  state.pollTimer = window.setInterval(async () => {
    try{
      const data = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(jobId)}`);
      state.currentJobData = { ...(state.currentJobData || {}), ...data };
      applyJobToUI(state.currentJobData, { keepBatch: true });
      await loadJobLogs(jobId);
      await loadRecentJobs();
      if (JOB_TERMINAL.has(String(data.status || ""))){
        stopPolling();
      }
      updateActionButtons();
    }catch(error){
      logMeta.textContent = `状态刷新失败：${error?.message || error}`;
    }
  }, 2500);
}

async function createSingleJobRequest(file, total, index){
  const fd = new FormData();
  fd.append("prompt_text", String(promptText.value || "").trim());
  fd.append("output_name", resolveOutputNameForFile(file, index, total));
  fd.append("export_pdf", getExportPdf() ? "true" : "false");
  fd.append("report_file", file, file.name);
  return fetchJson("/report-explain/api/jobs", { method: "POST", body: fd });
}

async function createJobs(options = {}){
  const forceRerun = !!options.forceRerun;
  if (isQueueBusy()){
    window.alert("当前队列提交中，请稍后再试。");
    return;
  }
  const promptValue = String(promptText.value || "").trim();
  if (promptValue.length < 50){
    window.alert("提示词太短，请补充后再执行。");
    return;
  }

  const rerunJobId = state.files.length === 0 ? String(state.cachedSourceJobId || state.jobId || "").trim() : "";
  if (!state.files.length && !rerunJobId){
    window.alert("请先上传至少一个 PDF，或先加载一个缓存任务。");
    return;
  }
  if (forceRerun && !rerunJobId){
    window.alert("当前没有可用于重跑的缓存任务。");
    return;
  }

  state.isSubmitting = true;
  startBtn.disabled = true;
  if (rerunJobBtn) rerunJobBtn.disabled = true;
  setStatusPill(forceRerun ? "重跑中" : "创建中", "busy");
  progressFill.style.width = "8%";
  progressPercentEl.textContent = "8%";

  if (forceRerun || (!state.files.length && rerunJobId)){
    try{
      const fd = new FormData();
      fd.append("prompt_text", promptValue);
      fd.append("output_name", String(outputNameInput.value || "").trim());
      fd.append("export_pdf", getExportPdf() ? "true" : "false");
      const data = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(rerunJobId)}/rerun`, {
        method: "POST",
        body: fd,
      });
      state.batchJobs = [data];
      state.batchErrors = [];
      applyJobToUI(data, { keepBatch: true });
      state.isSubmitting = false;
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.set("job", String(data.jobId || ""));
      history.replaceState({}, "", nextUrl.toString());
      await loadJobLogs(String(data.jobId || ""));
      await loadRecentJobs();
      startPolling(String(data.jobId || ""));
    }catch(error){
      state.isSubmitting = false;
      startBtn.disabled = false;
      updateActionButtons();
      setStatusPill("失败", "error");
      statusTextEl.textContent = `重跑失败：${error?.message || error}`;
      progressFill.style.width = "0%";
      progressPercentEl.textContent = "0%";
      resultBox.innerHTML = `<div class="resultLine error">重跑失败：${escapeHtml(error?.message || error)}</div>`;
    }
    return;
  }

  const total = state.files.length;
  state.batchJobs = [];
  state.batchErrors = [];
  statusTextEl.textContent = total > 1 ? `正在并行创建 ${total} 个任务…` : "正在创建任务…";
  resultBox.innerHTML = `<div class="resultLine muted">${total > 1 ? `正在并行创建 ${total} 个任务…` : "正在创建任务…"}</div>`;

  const results = await Promise.allSettled(state.files.map((file, index) => createSingleJobRequest(file, total, index)));
  state.batchJobs = results.filter((item) => item.status === "fulfilled").map((item) => item.value);
  state.batchErrors = results.filter((item) => item.status === "rejected").map((item) => item.reason?.message || String(item.reason || "未知错误"));

  if (!state.batchJobs.length){
    state.isSubmitting = false;
    startBtn.disabled = false;
    updateActionButtons();
    setStatusPill("失败", "error");
    statusTextEl.textContent = "批量任务创建失败。";
    progressFill.style.width = "0%";
    progressPercentEl.textContent = "0%";
    renderResultBox({});
    return;
  }

  const active = state.batchJobs[0];
  applyJobToUI(active, { keepBatch: true });
  state.isSubmitting = false;
  const nextUrl = new URL(window.location.href);
  nextUrl.searchParams.set("job", String(active.jobId || ""));
  history.replaceState({}, "", nextUrl.toString());
  await loadJobLogs(String(active.jobId || ""));
  await loadRecentJobs();
  startPolling(String(active.jobId || ""));
}

function wireTheme(){
  const apply = (theme) => {
    const normalized = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = normalized;
    document.body.dataset.theme = normalized;
    themeToggle.textContent = normalized === "dark" ? "切换浅色" : "切换深色";
    writeStorage(STORAGE_THEME, normalized);
  };
  apply(readStorage(STORAGE_THEME) || "dark");
  themeToggle.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    apply(next);
  });
}

function wireDropzone(){
  dropzone.addEventListener("click", () => {
    filePicker.click();
  });
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length) setFiles(files, { append: true });
  });
  filePicker.addEventListener("change", () => {
    const files = Array.from(filePicker.files || []);
    if (files.length) setFiles(files, { append: true });
    filePicker.value = "";
  });
}

async function hydrateInitialJob(){
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("job");
  if (!jobId) return;
  try{
    await loadJob(jobId, { pushQuery: !!params.get("job"), adoptPrompt: false });
  }catch(_){
    if (params.get("job")) clearLastJobId();
  }
}

outputNameInput?.addEventListener("input", () => {
  const value = String(outputNameInput.value || "").trim();
  if (!value){
    state.outputNameAuto = true;
    state.lastAutoName = "";
    return;
  }
  if (value !== state.lastAutoName) state.outputNameAuto = false;
});

clearFileBtn?.addEventListener("click", () => {
  state.files = [];
  state.parsedText = "";
  state.previewFileName = "";
  reportPreview.value = "";
  charCount.textContent = "0";
  previewCaption.textContent = "未选择文件";
  fileStats.textContent = "未选择";
  renderSelectedFiles();
  updateActionButtons();
});

reloadPromptBtn?.addEventListener("click", async () => {
  try { await loadDefaultPrompt(); }
  catch (error) { window.alert(`默认提示词加载失败：${error?.message || error}`); }
});

promptText?.addEventListener("input", () => {
  promptCharsEl.textContent = String(promptText.value.length);
});

outputModePdf?.addEventListener("change", updateActionButtons);
outputModeText?.addEventListener("change", updateActionButtons);
startBtn?.addEventListener("click", createJobs);
rerunJobBtn?.addEventListener("click", () => createJobs({ forceRerun: true }));

openOutputBtn?.addEventListener("click", async () => {
  try{
    const job = state.jobId ? await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(state.jobId)}`) : null;
    const file = job?.pdfFile || job?.markdownFile || "";
    await fetch(`/report-explain/api/open-output?file=${encodeURIComponent(file)}`, { method: "POST" });
  }catch(_){}
});

openResultPageBtn?.addEventListener("click", () => {
  const href = openResultPageBtn.dataset.href || "";
  if (href) window.open(href, "_blank", "noopener");
});

copyMarkdownBtn?.addEventListener("click", async () => {
  try{
    copyMarkdownBtn.disabled = true;
    await copyText(state.currentJobData?.markdownText || "");
    copyMarkdownBtn.textContent = "已复制";
    window.setTimeout(() => {
      if (copyMarkdownBtn){
        copyMarkdownBtn.textContent = "一键复制 Markdown";
        copyMarkdownBtn.disabled = !String(state.currentJobData?.markdownText || "").trim();
      }
    }, 1500);
  }catch(error){
    copyMarkdownBtn.textContent = "一键复制 Markdown";
    copyMarkdownBtn.disabled = !String(state.currentJobData?.markdownText || "").trim();
    window.alert(`复制失败：${error?.message || error}`);
  }
});

copyPlainTextBtn?.addEventListener("click", async () => {
  try{
    copyPlainTextBtn.disabled = true;
    await copyText(markdownToPlainText(state.currentJobData?.markdownText || ""));
    copyPlainTextBtn.textContent = "已复制";
    window.setTimeout(() => {
      if (copyPlainTextBtn){
        copyPlainTextBtn.textContent = "一键复制纯文本";
        copyPlainTextBtn.disabled = !String(state.currentJobData?.markdownText || "").trim();
      }
    }, 1500);
  }catch(error){
    copyPlainTextBtn.textContent = "一键复制纯文本";
    copyPlainTextBtn.disabled = !String(state.currentJobData?.markdownText || "").trim();
    window.alert(`复制失败：${error?.message || error}`);
  }
});

refreshCacheBtn?.addEventListener("click", loadRecentJobs);
loadJobBtn?.addEventListener("click", async () => {
  const jobId = String(jobLookupInput.value || "").trim();
  if (!jobId) return;
  try { await loadJob(jobId, { pushQuery: true, adoptPrompt: true }); }
  catch (error) { window.alert(`加载缓存失败：${error?.message || error}`); }
});

refreshLogsBtn?.addEventListener("click", async () => {
  await loadJobLogs(state.jobId);
});

wireTheme();
wireDropzone();
renderSelectedFiles();
updateActionButtons();

loadDefaultPrompt().catch((error) => {
  promptText.value = "";
  logBox.textContent = `默认提示词加载失败：${error?.message || error}`;
});

loadRecentJobs().then(hydrateInitialJob);
