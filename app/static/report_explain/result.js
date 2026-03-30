const $ = (selector) => document.querySelector(selector);

const STORAGE_THEME = "reportExplain.theme";
const TERMINAL_STATUS = new Set(["succeeded", "failed"]);
const STAGES = ["queued", "running", "rendering", "succeeded"];
const STAGE_LABELS = {
  queued: "任务创建",
  running: "Codex 写作",
  rendering: "PDF 排版",
  succeeded: "结果就绪",
  failed: "执行失败",
};

const state = {
  jobId: "",
  pollTimer: null,
  currentData: null,
};

const resultTitle = $("#resultTitle");
const resultSubtitle = $("#resultSubtitle");
const resultStatusLabel = $("#resultStatusLabel");
const resultCreatedAt = $("#resultCreatedAt");
const resultDuration = $("#resultDuration");
const resultJobId = $("#resultJobId");
const resultStatusPill = $("#resultStatusPill");
const resultSourceFile = $("#resultSourceFile");
const resultOutputBase = $("#resultOutputBase");
const resultSourceChars = $("#resultSourceChars");
const resultPromptChars = $("#resultPromptChars");
const resultCacheSourceNote = $("#resultCacheSourceNote");
const resultStatusText = $("#resultStatusText");
const resultProgressPercent = $("#resultProgressPercent");
const resultProgressFill = $("#resultProgressFill");
const resultStageRail = $("#resultStageRail");
const resultEventFeed = $("#resultEventFeed");
const markdownRender = $("#markdownRender");
const pdfFrame = $("#pdfFrame");
const resultLogMeta = $("#resultLogMeta");
const resultLogBox = $("#resultLogBox");
const downloadPdfBtn = $("#downloadPdfBtn");
const downloadMdBtn = $("#downloadMdBtn");
const rerunResultBtn = $("#rerunResultBtn");
const openOutputBtn = $("#openOutputBtn");
const refreshResultBtn = $("#refreshResultBtn");
const backToWorkspace = $("#backToWorkspace");
const themeToggle = $("#themeToggle");

if (downloadPdfBtn) downloadPdfBtn.disabled = true;
if (downloadMdBtn) downloadMdBtn.disabled = true;
if (rerunResultBtn) rerunResultBtn.disabled = true;

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

function escapeHtml(value){
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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

async function fetchJson(url, options = {}){
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false){
    throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
  }
  return data;
}

function setStatusPill(text, kind = "idle"){
  resultStatusPill.textContent = text;
  resultStatusPill.classList.remove("ok", "error", "busy");
  if (kind === "ok") resultStatusPill.classList.add("ok");
  else if (kind === "error") resultStatusPill.classList.add("error");
  else if (kind === "busy") resultStatusPill.classList.add("busy");
}

function setProgressState(data){
  const percent = Number(data?.progressPercent || 0);
  resultProgressFill.style.width = `${percent}%`;
  resultProgressPercent.textContent = `${Math.round(percent)}%`;

  const status = String(data?.status || "");
  if (!status){
    resultStageRail?.querySelectorAll(".stageNode").forEach((node) => {
      node.classList.remove("done", "active", "error");
    });
    return;
  }
  let activeStage = status;
  if (status === "failed"){
    activeStage = data?.markdownReady ? "rendering" : "running";
  }
  const activeIndex = Math.max(0, STAGES.indexOf(activeStage));

  resultStageRail?.querySelectorAll(".stageNode").forEach((node) => {
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
    if (index === activeIndex && !TERMINAL_STATUS.has(status)){
      node.classList.add("active");
    }
  });
}

function renderEventFeed(events){
  const rows = Array.isArray(events) ? events : [];
  if (!rows.length){
    resultEventFeed.innerHTML = '<div class="eventEmpty">暂无事件</div>';
    return;
  }
  resultEventFeed.innerHTML = rows.slice(-12).map((item) => {
    const level = escapeHtml(item.level || "info");
    const title = escapeHtml(item.title || "事件");
    const timestamp = escapeHtml(formatDateTime(item.timestamp));
    const detail = escapeHtml(item.detail || "");
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

function inlineMarkup(text){
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function renderMarkdown(markdownText){
  const lines = String(markdownText || "").split(/\r?\n/);
  const blocks = [];
  let paragraph = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const text = paragraph.join(" ").trim();
    paragraph = [];
    if (!text) return;
    blocks.push(`<p>${inlineMarkup(text)}</p>`);
  };

  for (let index = 0; index < lines.length; index += 1){
    const raw = lines[index] || "";
    const trimmed = raw.trim();

    if (!trimmed){
      flushParagraph();
      continue;
    }
    if (trimmed === "---"){
      flushParagraph();
      blocks.push("<hr />");
      continue;
    }
    if (trimmed.startsWith(">")){
      flushParagraph();
      const quotes = [];
      while (index < lines.length && String(lines[index] || "").trim().startsWith(">")){
        quotes.push(String(lines[index]).trim().replace(/^>\s?/, ""));
        index += 1;
      }
      index -= 1;
      blocks.push(`<blockquote>${quotes.map((line) => inlineMarkup(line)).join("<br/>")}</blockquote>`);
      continue;
    }
    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading){
      flushParagraph();
      const level = Math.min(heading[1].length + 1, 5);
      blocks.push(`<h${level}>${inlineMarkup(heading[2])}</h${level}>`);
      continue;
    }
    paragraph.push(trimmed);
  }

  flushParagraph();
  markdownRender.innerHTML = blocks.join("") || '<div class="eventEmpty">暂无 Markdown 内容</div>';
}

async function loadLogs(){
  const data = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(state.jobId)}/logs?max_chars=36000&event_limit=24`);
  renderEventFeed(data.events || []);
  resultLogMeta.textContent = `任务 ${state.jobId} · 日志大小 ${data.logSize || 0} bytes · 更新时间 ${formatDateTime(data.updatedAt)}`;
  resultLogBox.textContent = String(data.logText || "暂无日志");
}

function applyData(data){
  state.currentData = data;
  resultTitle.textContent = data.outputBaseName || "报告解说结果";
  resultSubtitle.textContent = data.message || "任务详情已加载。";
  resultStatusLabel.textContent = data.stageLabel || data.status || "-";
  resultCreatedAt.textContent = formatDateTime(data.createdAt);
  resultDuration.textContent = formatDuration(data.durationSeconds);
  resultJobId.textContent = data.jobId || "-";
  resultSourceFile.textContent = data.sourceFilename || "-";
  resultOutputBase.textContent = data.outputBaseName || "-";
  resultSourceChars.textContent = data.sourceChars ?? "-";
  resultPromptChars.textContent = data.promptChars ?? "-";
  resultStatusText.textContent = data.error
    ? `${data.message || "任务失败"}：${data.error}`
    : (data.message || "等待中");
  if (resultCacheSourceNote){
    if (data?.rerunFromJobId){
      resultCacheSourceNote.textContent = `当前结果由缓存任务 ${data.rerunFromJobId} 直接重跑生成；再次执行时也无需重新上传 PDF。`;
    } else if (data?.rerunReady && data?.jobId){
      resultCacheSourceNote.textContent = `当前任务 ${data.jobId} 已具备缓存重跑条件，无需重新上传 PDF。`;
    } else {
      resultCacheSourceNote.textContent = "当前任务未记录可用的缓存重跑来源。";
    }
  }
  if (rerunResultBtn){
    rerunResultBtn.disabled = !data.rerunReady;
  }

  if (data.status === "succeeded") setStatusPill("完成", "ok");
  else if (data.status === "failed") setStatusPill("失败", "error");
  else setStatusPill(data.stageLabel || "运行中", "busy");

  setProgressState(data);
  renderMarkdown(data.markdownText || "");

  if (data.downloadPdfUrl){
    downloadPdfBtn.disabled = false;
    downloadPdfBtn.dataset.href = data.downloadPdfUrl;
    pdfFrame.src = data.previewPdfUrl || data.downloadPdfUrl;
  } else {
    downloadPdfBtn.disabled = true;
    downloadPdfBtn.dataset.href = "";
    pdfFrame.removeAttribute("src");
  }

  if (data.downloadMarkdownUrl){
    downloadMdBtn.disabled = false;
    downloadMdBtn.dataset.href = data.downloadMarkdownUrl;
  } else {
    downloadMdBtn.disabled = true;
    downloadMdBtn.dataset.href = "";
  }

  backToWorkspace.href = `/report-explain?job=${encodeURIComponent(data.jobId || state.jobId)}`;
}

function stopPolling(){
  if (state.pollTimer){
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startPolling(){
  stopPolling();
  state.pollTimer = window.setInterval(async () => {
    try{
      await refreshPage();
      if (TERMINAL_STATUS.has(String(state.currentData?.status || ""))){
        stopPolling();
      }
    }catch(_){}
  }, 2500);
}

async function refreshPage(){
  const detail = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(state.jobId)}/detail`);
  applyData(detail);
  await loadLogs();
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

function getJobIdFromPath(){
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts[parts.length - 1] || "";
}

downloadPdfBtn?.addEventListener("click", () => {
  const href = downloadPdfBtn.dataset.href || "";
  if (href) window.open(href, "_blank", "noopener");
});

downloadMdBtn?.addEventListener("click", () => {
  const href = downloadMdBtn.dataset.href || "";
  if (href) window.open(href, "_blank", "noopener");
});

openOutputBtn?.addEventListener("click", async () => {
  try{
    const file = state.currentData?.pdfFile || state.currentData?.markdownFile || "";
    await fetch(`/report-explain/api/open-output?file=${encodeURIComponent(file)}`, { method: "POST" });
  }catch(_){}
});

rerunResultBtn?.addEventListener("click", async () => {
  if (!state.jobId) return;
  rerunResultBtn.disabled = true;
  try{
    const fd = new FormData();
    const data = await fetchJson(`/report-explain/api/jobs/${encodeURIComponent(state.jobId)}/rerun`, {
      method: "POST",
      body: fd,
    });
    const href = data?.resultPageUrl || `/report-explain/result/${encodeURIComponent(data?.jobId || "")}`;
    window.location.href = href;
  }catch(error){
    rerunResultBtn.disabled = false;
    window.alert(`Failed to rerun cached job: ${error?.message || error}`);
  }
});

refreshResultBtn?.addEventListener("click", refreshPage);

wireTheme();

state.jobId = getJobIdFromPath();
if (!state.jobId){
  resultSubtitle.textContent = "未识别到任务 ID。";
  resultLogBox.textContent = "无法加载任务。";
} else {
  refreshPage()
    .then(() => {
      if (!TERMINAL_STATUS.has(String(state.currentData?.status || ""))){
        startPolling();
      }
    })
    .catch((error) => {
      resultSubtitle.textContent = `任务加载失败：${error?.message || error}`;
      resultLogBox.textContent = "";
    });
}
