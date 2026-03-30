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
  file: null,
  parsedText: "",
  jobId: "",
  cachedSourceJobId: "",
  pollTimer: null,
  outputNameAuto: true,
  lastAutoName: "",
  recentJobs: [],
};

const dropzone = $("#dropzone");
const filePicker = $("#filePicker");
const fileStats = $("#fileStats");
const clearFileBtn = $("#clearFileBtn");
const outputNameInput = $("#outputName");
const reportPreview = $("#reportPreview");
const charCount = $("#charCount");
const promptText = $("#promptText");
const reloadPromptBtn = $("#reloadPromptBtn");
const startBtn = $("#startBtn");
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

if (openResultPageBtn) openResultPageBtn.disabled = true;
if (rerunJobBtn) rerunJobBtn.disabled = true;

function readStorage(key){
  try{
    return localStorage.getItem(key) || "";
  }catch(_){
    return "";
  }
}

function writeStorage(key, value){
  try{
    if (value) localStorage.setItem(key, value);
  }catch(_){}
}

function clearStorage(key){
  try{
    localStorage.removeItem(key);
  }catch(_){}
}

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

function saveLastJobId(jobId){
  writeStorage(STORAGE_LAST_JOB, jobId);
}

function readLastJobId(){
  return readStorage(STORAGE_LAST_JOB);
}

function clearLastJobId(){
  clearStorage(STORAGE_LAST_JOB);
}

function setStatusPill(text, kind = "idle"){
  jobStatusPill.textContent = text;
  jobStatusPill.classList.remove("ok", "error", "busy");
  if (kind === "ok") jobStatusPill.classList.add("ok");
  else if (kind === "error") jobStatusPill.classList.add("error");
  else if (kind === "busy") jobStatusPill.classList.add("busy");
}

function updateActionButtons(){
  const hasUploadedFile = !!state.file;
  const hasCachedSource = !!state.cachedSourceJobId;
  if (rerunJobBtn){
    rerunJobBtn.disabled = !hasCachedSource;
  }
  if (!startBtn) return;
  startBtn.textContent = hasUploadedFile ? "开始生成报告解说" : (hasCachedSource ? "基于缓存重新生成" : "开始生成报告解说");
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
  if (status === "failed"){
    activeStage = data?.markdownReady ? "rendering" : "running";
  }
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

function renderResultBox(data){
  const status = String(data?.status || "");
  if (!data?.jobId){
    resultBox.innerHTML = '<div class="resultLine muted">暂无任务结果。</div>';
    return;
  }

  if (status === "succeeded"){
    const pdfButton = data.downloadPdfUrl
      ? `<a class="actionLink primaryLink" href="${data.downloadPdfUrl}" target="_blank" rel="noreferrer">下载 PDF</a>`
      : "";
    const mdButton = data.downloadMarkdownUrl
      ? `<a class="actionLink secondaryLink" href="${data.downloadMarkdownUrl}" target="_blank" rel="noreferrer">下载 Markdown</a>`
      : "";
    const resultButton = data.resultPageUrl
      ? `<a class="actionLink secondaryLink" href="${data.resultPageUrl}" target="_blank" rel="noreferrer">打开结果页</a>`
      : "";
    const preview = data.markdownPreview
      ? `<div class="resultLine">预览：${escapeHtml(String(data.markdownPreview).slice(0, 220)).replace(/\n/g, "<br/>")}</div>`
      : "";
    resultBox.innerHTML = `
      <div class="resultLine"><strong>输出完成</strong>：${escapeHtml(data.pdfFile || "PDF")}</div>
      <div class="resultLine muted">完成时间：${escapeHtml(formatDateTime(data.completedAt))} · 总耗时：${escapeHtml(formatDuration(data.durationSeconds))}</div>
      <div class="resultLinks">${pdfButton}${mdButton}${resultButton}</div>
      ${preview}
    `;
    return;
  }

  if (status === "failed"){
    resultBox.innerHTML = `
      <div class="resultLine error">任务失败：${escapeHtml(data.error || data.message || "未知错误")}</div>
      <div class="resultLine muted">你可以查看下方日志，或从任务缓存中重新加载该任务。</div>
    `;
    return;
  }

  resultBox.innerHTML = `
    <div class="resultLine"><strong>任务运行中</strong></div>
    <div class="resultLine muted">${escapeHtml(data?.message || "正在等待 Codex 与 PDF 排版完成。")}</div>
  `;
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
  const events = data?.events || [];
  renderEventFeed(events);
  logMeta.textContent = state.jobId
    ? `任务 ${state.jobId} · 日志大小 ${humanSize(data?.logSize || 0)} · 更新时间 ${formatDateTime(data?.updatedAt)}`
    : "未选择任务";
  logBox.textContent = String(data?.logText || "暂无日志");
}

function renderRecentJobs(items){
  state.recentJobs = Array.isArray(items) ? items : [];
  if (!state.recentJobs.length){
    recentJobs.innerHTML = '<div class="eventEmpty">暂无缓存任务</div>';
    return;
  }

  recentJobs.innerHTML = state.recentJobs.map((item) => {
    const active = item.jobId === state.jobId ? " active" : "";
    const statusClass = item.status === "succeeded"
      ? "ok"
      : item.status === "failed"
        ? "error"
        : "busy";
    return `
      <button class="cacheItem${active}" type="button" data-job-id="${escapeHtml(item.jobId)}">
        <div class="cacheTitle">${escapeHtml(item.outputBaseName || item.sourceFilename || item.jobId)}</div>
        <div class="cacheMeta">
          <span class="cacheStatus ${statusClass}">${escapeHtml(item.stageLabel || item.status || "unknown")}</span>
          <span>${escapeHtml(formatDateTime(item.updatedAt || item.createdAt))}</span>
        </div>
        <div class="cacheSub">${escapeHtml(item.sourceFilename || "")}</div>
      </button>
    `;
  }).join("");

  recentJobs.querySelectorAll(".cacheItem").forEach((button) => {
    button.addEventListener("click", async () => {
      const jobId = button.dataset.jobId || "";
      if (jobId) await loadJob(jobId, { pushQuery: true });
    });
  });
}

function applyJobToUI(data){
  state.jobId = String(data?.jobId || "");
  state.cachedSourceJobId = data?.rerunReady ? state.jobId : "";
  if (data?.sourceFilename){
    state.file = null;
  }
  if (state.jobId) saveLastJobId(state.jobId);
  jobIdEl.textContent = state.jobId || "-";
  jobStageEl.textContent = data?.stageLabel || STAGE_LABELS[data?.status] || "-";
  sourceCharsEl.textContent = data?.sourceChars ?? "-";
  promptCharsEl.textContent = data?.promptChars ?? String(promptText.value.length || 0);
  statusTextEl.textContent = data?.error
    ? `${data?.message || "任务失败"}：${data.error}`
    : (data?.message || "等待中");

  if (data?.status === "succeeded"){
    setStatusPill("完成", "ok");
  } else if (data?.status === "failed"){
    setStatusPill("失败", "error");
  } else if (state.jobId){
    setStatusPill(data?.stageLabel || "运行中", "busy");
  } else {
    setStatusPill("idle");
  }

  setProgressState(data || {});
  renderResultBox(data || {});

  if (typeof data?.promptText === "string" && data.promptText){
    promptText.value = data.promptText;
    promptCharsEl.textContent = String(promptText.value.length);
  }
  if (typeof data?.sourcePreview === "string"){
    state.parsedText = data.sourcePreview;
    reportPreview.value = data.sourcePreview;
    charCount.textContent = String(data.sourceChars ?? data.sourcePreview.length);
    fileStats.textContent = data.sourceFilename ? `${data.sourceFilename} · 缓存任务` : "缓存任务";
    suggestOutputName(data.sourceFilename || "");
    if (data.outputBaseName) outputNameInput.value = data.outputBaseName;
  }

  if (cacheSourceNote){
    if (!state.jobId){
      cacheSourceNote.textContent = "加载缓存任务后，可以直接重跑，无需重新上传 PDF。";
    } else if (data?.rerunFromJobId) {
      cacheSourceNote.textContent = `当前任务可直接从缓存重跑；它由缓存任务 ${data.rerunFromJobId} 派生生成。`;
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

async function setFile(file){
  state.file = null;
  state.parsedText = "";
  reportPreview.value = "";
  charCount.textContent = "0";
  updateActionButtons();

  if (!file){
    fileStats.textContent = "未选择";
    return;
  }
  if (extOf(file.name) !== ".pdf"){
    window.alert("当前页面只支持 PDF 文件。");
    fileStats.textContent = "格式不支持";
    return;
  }

  state.file = file;
  fileStats.textContent = `${file.name} · ${humanSize(file.size)} · 解析中`;
  suggestOutputName(file.name);
  updateActionButtons();

  try{
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/parse-file", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data?.ok){
      throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
    }
    state.parsedText = String(data.text || "");
    reportPreview.value = state.parsedText.slice(0, 12000);
    charCount.textContent = String(state.parsedText.length);
    fileStats.textContent = `${file.name} · ${humanSize(file.size)} · 已解析`;
  }catch(error){
    state.file = null;
    state.parsedText = "";
    reportPreview.value = "";
    charCount.textContent = "0";
    fileStats.textContent = "解析失败";
    updateActionButtons();
    window.alert(`PDF 解析失败：${error?.message || error}`);
  }
}

function suggestOutputName(fileName){
  const base = String(fileName || "").replace(/\.pdf$/i, "").trim();
  const suggested = base ? `${base}（报告解说）` : "";
  if (!suggested) return;
  const current = String(outputNameInput?.value || "").trim();
  if (!current || state.outputNameAuto || current === state.lastAutoName){
    outputNameInput.value = suggested;
    state.lastAutoName = suggested;
    state.outputNameAuto = true;
  }
}

async function loadDefaultPrompt(){
  const data = await fetchJson("/report-explain/api/default-prompt");
  promptText.value = String(data.promptText || "");
  promptCharsEl.textContent = String(promptText.value.length);
}

async function loadRecentJobs(){
  try{
    const data = await fetchJson("/report-explain/api/jobs?limit=12");
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
  const { pushQuery = false } = options;
  const data = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(jobId)}/detail`);
  applyJobToUI(data);
  await loadJobLogs(jobId);
  await loadRecentJobs();

  if (pushQuery){
    const url = new URL(window.location.href);
    url.searchParams.set("job", jobId);
    history.replaceState({}, "", url.toString());
  }

  if (JOB_TERMINAL.has(String(data.status || ""))){
    stopPolling();
    startBtn.disabled = false;
  } else {
    startBtn.disabled = true;
    startPolling(jobId);
  }
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
      applyJobToUI(data);
      await loadJobLogs(jobId);
      await loadRecentJobs();
      if (JOB_TERMINAL.has(String(data.status || ""))){
        stopPolling();
        startBtn.disabled = false;
      }
    }catch(error){
      logMeta.textContent = `状态刷新失败：${error?.message || error}`;
    }
  }, 2500);
}

async function createJob(options = {}){
  const forceRerun = !!options.forceRerun;
  const promptValue = String(promptText.value || "").trim();
  if (promptValue.length < 50){
    window.alert("Prompt is too short. Please extend it before running.");
    return;
  }

  const rerunJobId = !state.file ? String(state.cachedSourceJobId || state.jobId || "").trim() : "";
  if (!state.file && !rerunJobId){
    window.alert("Upload a PDF first, or load a cached job before rerunning.");
    return;
  }
  if (forceRerun && !rerunJobId){
    window.alert("No cached job is available for rerun.");
    return;
  }

  startBtn.disabled = true;
  if (rerunJobBtn) rerunJobBtn.disabled = true;
  const isRerun = forceRerun || (!state.file && !!rerunJobId);
  setStatusPill(isRerun ? "rerun" : "creating", "busy");
  statusTextEl.textContent = isRerun ? "Creating a new run from cached source…" : "Uploading PDF and creating job…";
  progressFill.style.width = "8%";
  progressPercentEl.textContent = "8%";
  resultBox.innerHTML = `<div class="resultLine muted">${isRerun ? "Starting rerun from cached source…" : "Creating task…"}</div>`;

  const fd = new FormData();
  fd.append("prompt_text", promptValue);
  fd.append("output_name", String(outputNameInput.value || "").trim());

  let url = "/report-explain/api/jobs";
  if (isRerun){
    url = `/report-explain/api/jobs/${encodeURIComponent(rerunJobId)}/rerun`;
  } else if (state.file){
    fd.append("report_file", state.file, state.file.name);
  }

  try{
    const res = await fetch(url, { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data?.ok){
      throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
    }
    applyJobToUI(data);
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("job", String(data.jobId || ""));
    history.replaceState({}, "", nextUrl.toString());
    await loadJobLogs(String(data.jobId || ""));
    await loadRecentJobs();
    startPolling(String(data.jobId || ""));
  }catch(error){
    startBtn.disabled = false;
    updateActionButtons();
    setStatusPill("failed", "error");
    statusTextEl.textContent = `Failed to create task: ${error?.message || error}`;
    progressFill.style.width = "0%";
    progressPercentEl.textContent = "0%";
    resultBox.innerHTML = `<div class="resultLine error">Failed to create task: ${escapeHtml(error?.message || error)}</div>`;
  }
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
  dropzone.addEventListener("click", () => filePicker.click());
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
    const file = event.dataTransfer?.files?.[0];
    if (file) setFile(file);
  });
  filePicker.addEventListener("change", () => {
    const file = filePicker.files?.[0];
    if (file) setFile(file);
    filePicker.value = "";
  });
}

async function hydrateInitialJob(){
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("job") || readLastJobId();
  if (!jobId) return;
  try{
    await loadJob(jobId, { pushQuery: !!params.get("job") });
  }catch(_){
    clearLastJobId();
  }
}

outputNameInput?.addEventListener("input", () => {
  const value = String(outputNameInput.value || "").trim();
  if (!value){
    state.outputNameAuto = true;
    state.lastAutoName = "";
    return;
  }
  if (value !== state.lastAutoName){
    state.outputNameAuto = false;
  }
});

clearFileBtn?.addEventListener("click", () => {
  state.file = null;
  state.parsedText = "";
  reportPreview.value = "";
  charCount.textContent = "0";
  fileStats.textContent = "No file selected";
  updateActionButtons();
});

reloadPromptBtn?.addEventListener("click", async () => {
  try{
    await loadDefaultPrompt();
  }catch(error){
    window.alert(`默认提示词加载失败：${error?.message || error}`);
  }
});

promptText?.addEventListener("input", () => {
  promptCharsEl.textContent = String(promptText.value.length);
});

startBtn?.addEventListener("click", createJob);
rerunJobBtn?.addEventListener("click", () => createJob({ forceRerun: true }));

openOutputBtn?.addEventListener("click", async () => {
  try{
    const job = state.jobId ? await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(state.jobId)}`) : null;
    const file = job?.pdfFile || job?.markdownFile || "";
    await fetch(`/report-explain/api/open-output?file=${encodeURIComponent(file)}`, { method: "POST" });
  }catch(_){}
});

openResultPageBtn?.addEventListener("click", () => {
  const href = openResultPageBtn.dataset.href || "";
  if (href){
    window.open(href, "_blank", "noopener");
  }
});

refreshCacheBtn?.addEventListener("click", loadRecentJobs);

loadJobBtn?.addEventListener("click", async () => {
  const jobId = String(jobLookupInput.value || "").trim();
  if (!jobId) return;
  try{
    await loadJob(jobId, { pushQuery: true });
  }catch(error){
    window.alert(`加载缓存失败：${error?.message || error}`);
  }
});

refreshLogsBtn?.addEventListener("click", async () => {
  await loadJobLogs(state.jobId);
});

wireTheme();
wireDropzone();
updateActionButtons();
loadDefaultPrompt().catch((error) => {
  promptText.value = "";
  logBox.textContent = `默认提示词加载失败：${error?.message || error}`;
});
loadRecentJobs().then(hydrateInitialJob);
