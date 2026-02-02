const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  accounts: [],
  browserProfiles: [],
  jobs: [],
  job: null,
  sse: null,
  reportFile: null,
  loginSession: null,
  loginPoll: null,
  uiTimer: null,
  liveByAccount: {},
  inflight: {},
  parts: {},
  splitInfo: null,
  stitch: null,
};

const STORAGE_LAST_JOB = "notebooklm.lastJobId";
const STORAGE_RUN_TAB = "notebooklm.runTab";
const STORAGE_FIXED_INSTRUCTIONS = "notebooklm.fixedInstructions";
const STORAGE_EXTRA_INSTRUCTIONS = "notebooklm.extraInstructions";
const STORAGE_USE_FIXED_INSTRUCTIONS = "notebooklm.useFixedInstructions";
const STORAGE_PROMPT_PRESET = "notebooklm.promptPreset";
const STORAGE_PROMPT_NAME = "notebooklm.promptName";
const STORAGE_SPLIT_PARTS = "notebooklm.splitPartPrompts";
const STORAGE_SPLIT_CANDIDATES = "notebooklm.splitCandidatesPerPart";
const STORAGE_STITCH_TRANSITIONS = "notebooklm.stitchTransitions";
const STORAGE_STITCH_TRANSITION_REPEATS = "notebooklm.stitchTransitionRepeats";
const STORAGE_STITCH_TRANSITION_DURATIONS = "notebooklm.stitchTransitionDurations";
const STORAGE_STITCH_TRANSITION_LOCK = "notebooklm.stitchTransitionLock";
const STORAGE_LAST_RUN_CONFIG = "notebooklm.lastRunConfig";
const STORAGE_THEME = "notebooklm.uiTheme";
const THEME_DARK = "dark";
const THEME_LIGHT = "light";
const STALL_WARNING_MS = 20 * 60 * 1000;
const DEFAULT_TRANSITIONS_BY_SEGMENTS = {
  3: [
    "assets/transitions/第一二段之间的链接-轻快活泼自由自在尤克里里.wav",
    "assets/transitions/第二三段之间的连接-欢快轻快节奏活力阳光.wav",
  ],
};
const DEFAULT_TRANSITION_DURATIONS_BY_SEGMENTS = {
  3: [30, 25],
};

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

function persistLastJobId(jobId){
  try{
    if (jobId) localStorage.setItem(STORAGE_LAST_JOB, String(jobId));
  }catch{}
}

function readLastJobId(){
  try{
    return localStorage.getItem(STORAGE_LAST_JOB);
  }catch{
    return null;
  }
}

function clearLastJobId(){
  try{ localStorage.removeItem(STORAGE_LAST_JOB); }catch{}
}

function persistRunTab(tab){
  try{
    if (tab) localStorage.setItem(STORAGE_RUN_TAB, String(tab));
  }catch{}
}

function readRunTab(){
  try{
    return localStorage.getItem(STORAGE_RUN_TAB);
  }catch{
    return null;
  }
}

function _readJSON(key, fallback=null){
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

function collectLastRunConfig(){
  const picked = [];
  for (const cb of $$("#accountsList input[type=checkbox]")){
    const id = cb.dataset.accountId;
    const attempts = $(`#accountsList input[data-attempts-for="${id}"]`);
    picked.push({
      account_id: id,
      checked: !!cb.checked,
      max_attempts: parseInt(attempts?.value || "20",10),
    });
  }

  const segs = _getSplitSegments();
  return {
    v: 1,
    accounts: picked,
    target_successes: parseInt($("#targetCount")?.value || "1",10),
    target_mode: ($("#targetMode")?.value || "accepted"),
    min_duration_minutes: parseFloat($("#minMinutes")?.value || "40"),
    split_enabled: !!$("#splitEnabled")?.checked,
    split_parallel_ui: !!$("#splitParallel")?.checked,
    split_segments: segs,
    split_min_duration_minutes: parseFloat($("#splitMinMinutes")?.value || "15"),
    split_task_timeout_minutes: parseFloat($("#splitTaskTimeout")?.value || "40"),
    split_output_format: $("#splitOutputFormat")?.value || "m4a",
    split_keep_parts: !!$("#splitKeepParts")?.checked,
    split_manual_stitch: !!$("#splitEnabled")?.checked,
    split_candidates_per_part: _normalizeSplitCandidates(_readSplitCandidates(), segs),
    stitch_transition_enabled: false,
    stitch_transition_fade_seconds: 3,
    stitch_transition_files: [],
    stitch_transition_repeats: [],
    stitch_transition_durations: [],
    stitch_transition_lock: false,
    language: $("#lang")?.value || "zh",
    audio_length: $("#audioLength")?.value || "long",
    audio_format: $("#audioFormat")?.value || "deep_dive",
    accounts_concurrency: parseInt($("#accConcurrency")?.value || "4",10),
    per_account_concurrency: parseInt($("#perAccConcurrency")?.value || "2",10),
    keep_short_files: !!$("#keepShort")?.checked,
    delete_short_artifacts: !!$("#deleteShort")?.checked,
    silence_check_enabled: !!$("#silenceCheckEnabled")?.checked,
    silence_min_duration_s: parseFloat($("#silenceMinSeconds")?.value || "5"),
    silence_threshold_db: parseFloat($("#silenceThreshold")?.value || "-50"),
  };
}

function persistLastRunConfig(){
  _writeJSON(STORAGE_LAST_RUN_CONFIG, collectLastRunConfig());
}

function restoreLastRunConfig(){
  const cfg = _readJSON(STORAGE_LAST_RUN_CONFIG, null);
  if (!cfg || typeof cfg !== "object") return false;

  try{
    if ($("#targetCount") && cfg.target_successes != null) $("#targetCount").value = String(cfg.target_successes);
    if ($("#targetMode") && cfg.target_mode) $("#targetMode").value = String(cfg.target_mode);
    if ($("#minMinutes") && cfg.min_duration_minutes != null) $("#minMinutes").value = String(cfg.min_duration_minutes);

    if ($("#splitEnabled")) $("#splitEnabled").checked = !!cfg.split_enabled;
    if ($("#splitParallel")) $("#splitParallel").checked = (cfg.split_parallel_ui != null) ? !!cfg.split_parallel_ui : !!cfg.split_parallel;
    if ($("#splitSegments") && cfg.split_segments != null) $("#splitSegments").value = String(cfg.split_segments);
    if ($("#splitMinMinutes") && cfg.split_min_duration_minutes != null) $("#splitMinMinutes").value = String(cfg.split_min_duration_minutes);
    if ($("#splitTaskTimeout") && cfg.split_task_timeout_minutes != null) $("#splitTaskTimeout").value = String(cfg.split_task_timeout_minutes);
    if ($("#splitOutputFormat") && cfg.split_output_format) $("#splitOutputFormat").value = String(cfg.split_output_format);
    if ($("#splitKeepParts")) $("#splitKeepParts").checked = !!cfg.split_keep_parts;
    if ($("#splitManualStitch")) $("#splitManualStitch").checked = !!cfg.split_manual_stitch;

    const segs = _getSplitSegments();
    if (Array.isArray(cfg.split_candidates_per_part)){
      _writeSplitCandidates(_normalizeSplitCandidates(cfg.split_candidates_per_part, segs));
    }
    if (Array.isArray(cfg.stitch_transition_files)){
      _writeTransitions(_normalizeTransitions(cfg.stitch_transition_files, segs));
    }
    if (Array.isArray(cfg.stitch_transition_repeats)){
      _writeTransitionRepeats(_normalizeTransitionRepeats(cfg.stitch_transition_repeats, segs));
    }
    if (Array.isArray(cfg.stitch_transition_durations)){
      _writeTransitionDurations(_normalizeTransitionDurations(cfg.stitch_transition_durations, segs));
    }
    if (cfg.stitch_transition_lock != null){
      _writeTransitionLock(!!cfg.stitch_transition_lock);
    }
    renderSplitPromptList();
    renderTransitionList();
    updateSplitPromptPreview();

    if ($("#lang") && cfg.language) $("#lang").value = String(cfg.language);
    if ($("#audioLength") && cfg.audio_length) $("#audioLength").value = String(cfg.audio_length);
    if ($("#audioFormat") && cfg.audio_format) $("#audioFormat").value = String(cfg.audio_format);
    if ($("#accConcurrency") && cfg.accounts_concurrency != null) $("#accConcurrency").value = String(cfg.accounts_concurrency);
    if ($("#perAccConcurrency") && cfg.per_account_concurrency != null) $("#perAccConcurrency").value = String(cfg.per_account_concurrency);

    if ($("#keepShort")) $("#keepShort").checked = !!cfg.keep_short_files;
    if ($("#deleteShort")) $("#deleteShort").checked = !!cfg.delete_short_artifacts;
    if ($("#silenceCheckEnabled")) $("#silenceCheckEnabled").checked = (cfg.silence_check_enabled !== false);
    if ($("#silenceMinSeconds") && cfg.silence_min_duration_s != null){
      $("#silenceMinSeconds").value = String(cfg.silence_min_duration_s);
    }
    if ($("#silenceThreshold") && cfg.silence_threshold_db != null){
      $("#silenceThreshold").value = String(cfg.silence_threshold_db);
    }
    if ($("#stitchTransitionEnabled")) $("#stitchTransitionEnabled").checked = !!cfg.stitch_transition_enabled;
    if ($("#stitchTransitionFade") && cfg.stitch_transition_fade_seconds != null){
      $("#stitchTransitionFade").value = String(cfg.stitch_transition_fade_seconds);
    }
    if ($("#stitchTransitionLock") && cfg.stitch_transition_lock != null){
      $("#stitchTransitionLock").checked = !!cfg.stitch_transition_lock;
    }

    const byId = new Map();
    if (Array.isArray(cfg.accounts)){
      for (const a of cfg.accounts){
        if (!a?.account_id) continue;
        byId.set(String(a.account_id), a);
      }
    }
    for (const cb of $$("#accountsList input[type=checkbox]")){
      const id = String(cb.dataset.accountId || "");
      const a = byId.get(id);
      if (!a) continue;
      cb.checked = !!a.checked;
      const attempts = $(`#accountsList input[data-attempts-for="${id}"]`);
      if (attempts && a.max_attempts != null){
        attempts.value = String(a.max_attempts);
      }
    }

    $("#targetMode")?.dispatchEvent(new Event("change"));
    return true;
  }catch{
    return false;
  }
}

function mergeText(a, b){
  const aa = String(a || "").trim();
  const bb = String(b || "").trim();
  if (aa && bb) return `${aa}\n\n${bb}`;
  return aa || bb;
}

function fmtCNDate(d){
  const y = d.getFullYear();
  const m = d.getMonth() + 1;
  const day = d.getDate();
  return `${y}年${m}月${day}日`;
}

function getDateTokens(){
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  return {
    "{{TODAY}}": fmtCNDate(now),
    "{{TOMORROW}}": fmtCNDate(tomorrow),
  };
}

function applyDateTokens(text){
  let out = String(text || "");
  const tokens = getDateTokens();
  for (const [key, val] of Object.entries(tokens)){
    out = out.split(key).join(val);
  }
  return out;
}

function buildPromptPreview(){
  const extra = $("#instructions")?.value || "";
  const fixedEnabled = $("#useFixedInstructions")?.checked;
  const fixed = $("#fixedInstructions")?.value || "";
  const raw = fixedEnabled ? mergeText(fixed, extra) : String(extra || "").trim();
  return applyDateTokens(raw);
}

function updatePromptPreview(){
  const box = $("#promptPreview");
  if (!box) return;
  const content = buildPromptPreview();
  box.value = content;
  if (!content){
    box.placeholder = "暂无提示词内容";
  }
}

function setRunTab(tab){
  const panes = {
    live: $("#tab-live"),
    files: $("#tab-files"),
    log: $("#tab-log"),
    queue: $("#tab-queue"),
  };
  const target = panes[tab] ? tab : "live";
  for (const [k, el] of Object.entries(panes)){
    if (!el) continue;
    el.classList.toggle("active", k === target);
  }
  for (const btn of $$(".tabBtn")){
    btn.classList.toggle("active", String(btn.dataset.tab || "") === target);
  }
  persistRunTab(target);
}

function wireRunTabs(){
  const btns = $$(".tabBtn");
  if (!btns.length) return;
  for (const btn of btns){
    btn.addEventListener("click", () => setRunTab(btn.dataset.tab));
  }
  setRunTab(readRunTab() || "live");
}

function fmtBytes(n){
  if (!Number.isFinite(n)) return "-";
  const u = ["B","KB","MB","GB"];
  let i = 0;
  while(n >= 1024 && i < u.length-1){ n/=1024; i++; }
  return `${n.toFixed(i===0?0:1)} ${u[i]}`;
}

function fmtTs(ts){
  return String(ts || "").replace("T"," ").replace("Z","").replace(/\.\d+\+00:00$/,"");
}

function tagFor(type){
  if (type === "warn") return ["WARN","warn"];
  if (["accepted","job_completed","part_accepted","stitch_completed","silence_ok","part_silence_ok"].includes(type)) return ["OK","good"];
  if ([
    "rejected","attempt_error","account_error","job_failed","generation_failed",
    "part_attempt_error","part_generation_failed","split_failed","stitch_rejected",
    "silence_rejected","part_silence_rejected","silence_check_failed","part_silence_check_failed"
  ].includes(type)) return ["ERR","bad"];
  if ([
    "job_started","generation_started","downloaded","source_ready","notebook_created","attempt_started","job_queued",
    "split_detected","split_source_ready","part_attempt_started","part_generation_started","part_downloaded","part_rejected","stitch_started",
    "source_fallback_file","split_waiting_selection","split_stitch_selection_submitted","split_stitch_selection_received",
    "stitch_transition_missing"
  ].includes(type)) return ["RUN","warn"];
  return ["INFO",""];
}

function errSuffix(ev){
  const parts = [];
  if (ev?.rpc_id) parts.push(`rpc_id=${ev.rpc_id}`);
  if (ev?.rpc_code) parts.push(`code=${ev.rpc_code}`);
  return parts.length ? ` (${parts.join(", ")})` : "";
}

function lineText(ev){
  const a = ev.account_name ? `@${ev.account_name}` : (ev.account_id ? `@${ev.account_id.slice(0,6)}` : "");
  switch(ev.type){
    case "job_queued": return `任务排队中 ${ev.job_id}`;
    case "job_started": return `任务开始 ${ev.job_id}`;
    case "job_completed": return `任务完成 ✅`;
    case "job_cancelled": return `任务取消`;
    case "job_failed": return `任务失败: ${ev.error || ""}`;
    case "account_started": return `${a} 账号进入工作台 (最多 ${ev.max_attempts} 次)`;
    case "notebook_created": return `${a} 创建 notebook ${ev.notebook_id}`;
    case "source_ready": return `${a} 报告已作为 source 导入${ev.source_method ? ` (${ev.source_method})` : ""}`;
    case "source_fallback_file": return `${a} source 导入失败，已改用文件上传：${ev.error || ""}${errSuffix(ev)}`;
    case "generation_started": return `${a} 第 ${ev.attempt} 次生成开始 (task ${ev.task_id.slice(0,8)}…)`;
    case "generation_failed": return `${a} 生成失败: ${ev.error_code || ""} ${ev.error || ""}`;
    case "split_detected": {
      const cps = Array.isArray(ev.candidates_per_part)
        ? ev.candidates_per_part.map(v => {
          const n = parseInt(String(v ?? "1"),10);
          if (!Number.isFinite(n) || n < 0) return 1;
          return Math.min(n, 20);
        })
        : null;
      const candTxt = cps ? ` · 候选 ${cps.join(",")}` : "";
      return `${a} 分段模式：检测到 ${ev.detected_items ?? "?"} 条，拆分为 ${ev.segments} 段（每段阈值 ${ev.min_part_minutes} min${candTxt}）`;
    }
    case "split_source_ready": return `${a} 第 ${ev.part} 段 source 已导入${ev.source_method ? ` (${ev.source_method})` : ""}`;
    case "part_attempt_started": return `${a} 第 ${ev.part} 段 · 第 ${ev.attempt} 次尝试`;
    case "part_generation_started": return `${a} 第 ${ev.part} 段生成开始 (task ${ev.task_id.slice(0,8)}…)`;
    case "part_generation_failed": return `${a} 第 ${ev.part} 段生成失败: ${ev.error_code || ""} ${ev.error || ""}`;
    case "part_downloaded": return `${a} 第 ${ev.part} 段已下载，时长 ${ev.duration_minutes} min (${ev.duration_method})`;
    case "part_silence_ok":
      return `${a} 第 ${ev.part} 段静音检测通过（阈值 ${ev.threshold_db}dB / ${ev.min_silence_duration_s}s）`;
    case "part_silence_rejected": {
      const count = Number(ev.segments_count || 0);
      const seg = Array.isArray(ev.segments) && ev.segments.length ? ev.segments[0] : null;
      const pos = seg ? `（首段 ${seg.start_hhmmss}→${seg.end_hhmmss}）` : "";
      const ctxt = Number.isFinite(count) && count > 0 ? `（静音段 ${count} 处）` : "";
      return `${a} 第 ${ev.part} 段静音超标，作废${ctxt}${pos}`;
    }
    case "part_accepted": {
      const got = Number(ev.candidates_collected);
      const req = Number(ev.candidates_required);
      const suffix = (Number.isFinite(got) && Number.isFinite(req) && req > 0) ? `（候选 ${got}/${req}）` : "";
      return `${a} 第 ${ev.part} 段 ✅ 达标：${ev.duration_minutes} min${suffix}`;
    }
    case "part_rejected": return `${a} 第 ${ev.part} 段 ⛔ 太短：${ev.duration_minutes} min (阈值 ${ev.min_duration_minutes} min)`;
    case "stitch_started": return `${a} 开始拼接 ${ev.parts?.length || ""} 段 → ${ev.output || ""}`;
    case "stitch_transition_missing":
      return `${a} 过渡音频缺失：${ev.gap || ""} ${ev.path || ""}`.trim();
    case "stitch_completed": return `${a} 拼接完成：${ev.duration_minutes} min (${ev.method || ""})`;
    case "stitch_rejected": return `${a} 拼接后仍太短：${ev.duration_minutes} min (阈值 ${ev.min_duration_minutes} min)`;
    case "silence_ok":
      return `${a} 静音检测通过（阈值 ${ev.threshold_db}dB / ${ev.min_silence_duration_s}s）`;
    case "silence_rejected": {
      const count = Number(ev.segments_count || 0);
      const seg = Array.isArray(ev.segments) && ev.segments.length ? ev.segments[0] : null;
      const pos = seg ? `（首段 ${seg.start_hhmmss}→${seg.end_hhmmss}）` : "";
      const ctxt = Number.isFinite(count) && count > 0 ? `（静音段 ${count} 处）` : "";
      return `${a} 静音超标，作废${ctxt}${pos}`;
    }
    case "part_silence_check_failed":
    case "silence_check_failed":
      return `${a} 静音检测失败：${ev.error || ""}`;
    case "split_failed": return `${a} 分段失败: ${ev.error || ""}`;
    case "split_waiting_selection": return `等待手动选择拼接文件（第 ${ev.episode || 1} 期）`;
    case "split_stitch_selection_submitted": return `已提交拼接选择（第 ${ev.episode || 1} 期）`;
    case "split_stitch_selection_received": return `已收到拼接选择，开始拼接…（第 ${ev.episode || 1} 期）`;
    case "split_stop_requested": return `已请求停止生成，准备拼接当前候选（模式 ${ev.mode || "auto"}）`;
    case "split_stop_auto_stitch": return `停止后自动拼接当前候选`;
    case "part_attempt_error": return `${a} 第 ${ev.part} 段尝试出错: ${ev.error || ""}${errSuffix(ev)}`;
    case "downloaded": {
      const mode = String(ev.target_mode || "");
      if (mode === "downloaded" && ev.progress != null && ev.target != null){
        return `${a} 已下载，时长 ${ev.duration_minutes} min（生成 ${ev.progress}/${ev.target}）`;
      }
      return `${a} 已下载，时长 ${ev.duration_minutes} min (${ev.duration_method})`;
    }
    case "accepted": {
      const mode = String(ev.target_mode || "");
      if (mode === "downloaded" && ev.progress != null && ev.target != null){
        return `${a} ✅ 达标：${ev.duration_minutes} min（生成 ${ev.progress}/${ev.target} · 达标 ${ev.successes ?? "?"}）`;
      }
      return `${a} ✅ 达标：${ev.duration_minutes} min（${ev.successes}/${ev.target}）`;
    }
    case "rejected": {
      const mode = String(ev.target_mode || "");
      if (mode === "downloaded" && ev.progress != null && ev.target != null){
        return `${a} ⛔ 太短：${ev.duration_minutes} min（生成 ${ev.progress}/${ev.target} · 阈值 ${ev.min_duration_minutes} min）`;
      }
      return `${a} ⛔ 太短：${ev.duration_minutes} min (阈值 ${ev.min_duration_minutes} min)`;
    }
    case "attempt_error": return `${a} 尝试出错: ${ev.error || ""}${errSuffix(ev)}`;
    case "account_error": return `${a} 账号出错: ${ev.error || ""}${errSuffix(ev)}`;
    case "account_finished": return `${a} 账号结束`;
    default: return JSON.stringify(ev);
  }
}

function setBadge(status){
  const dot = $("#statusDot");
  const label = $("#statusLabel");
  dot.className = "dot";
  if (status === "running" || status === "queued" || status === "waiting_selection") dot.classList.add("running");
  if (status === "completed") dot.classList.add("good");
  if (status === "failed") dot.classList.add("bad");
  label.textContent = status || "idle";
}

function setJobStats(job){
  $("#jobId").textContent = job?.id ? job.id : "-";
  $("#jobState").textContent = job?.state || "-";
  const mode = String(job?.config?.target_mode || "accepted");
  const target = job?.config?.target_successes ?? "-";
  const accepted = Number(job?.successes ?? 0);
  const downloads = Number(job?.downloads ?? 0);
  if (mode === "downloaded"){
    $("#jobSuccess").textContent = `生成 ${downloads}/${target} · 达标 ${accepted}`;
  } else {
    $("#jobSuccess").textContent = `达标 ${accepted}/${target}`;
  }
  $("#jobChars").textContent = job?.report_char_count ?? "-";
  setBadge(job?.state || "idle");

  const exportLink = $("#exportLogLink");
  if (exportLink){
    if (job?.id){
      exportLink.href = `/api/jobs/${encodeURIComponent(job.id)}/events.jsonl`;
    } else {
      exportLink.href = "#";
    }
  }

  renderProgressSummary(job);
  renderProgressWarning();
  updateStopAndStitchBtn();
}

function _accountDisplayName(accountId, fallback){
  const a = (state.accounts || []).find(x => x?.id === accountId);
  return a?.name || fallback || (accountId ? accountId.slice(0,6) : "account");
}

function updateLiveState(ev){
  const accountId = ev?.account_id;
  if (!accountId) return;

  const who = ev.account_name || _accountDisplayName(accountId, null);
  state.liveByAccount[accountId] = {
    account_id: accountId,
    who,
    what: lineText(ev),
    ts: ev.ts || new Date().toISOString(),
    type: ev.type,
  };
}

function updateLive(ev){
  updateLiveState(ev);
  renderLive();
}

function renderLive(){
  const box = $("#live");
  if (!box) return;
  const values = Object.values(state.liveByAccount || {});
  if (!values.length){
    box.innerHTML = `<div class="hint">等待任务开始…</div>`;
    return;
  }

  values.sort((a,b) => String(a.who||"").localeCompare(String(b.who||"")));

  box.innerHTML = "";
  for (const s of values){
    const row = document.createElement("div");
    row.className = "liveCard";

    const who = document.createElement("div");
    who.className = "who";
    who.textContent = s.who || _accountDisplayName(s.account_id, null);

    const what = document.createElement("div");
    what.className = "what";
    what.textContent = s.what || "";

    const when = document.createElement("div");
    when.className = "when";
    when.textContent = fmtTs(s.ts);

    row.append(who, what, when);
    box.append(row);
  }
}

function resetDerivedState(){
  state.liveByAccount = {};
  state.inflight = {};
  state.parts = {};
  state.splitInfo = null;
  state.stitch = null;
  renderLive();
  renderInflight();
  renderSplitBoard();
  renderProgressWarning();
  renderStitchPanel();
}

function fmtElapsedMs(ms){
  if (!Number.isFinite(ms) || ms < 0) ms = 0;
  const s = Math.floor(ms/1000);
  const m = Math.floor(s/60);
  const ss = String(s % 60).padStart(2,"0");
  return `${m}m${ss}s`;
}

function renderProgressSummary(job){
  const text = $("#progressText");
  const sub = $("#progressSub");
  const fill = $("#progressFill");

  if (!text || !sub || !fill){
    return;
  }

  const mode = String(job?.config?.target_mode || "accepted");
  const target = Number(job?.config?.target_successes ?? 0);
  const accepted = Number(job?.successes ?? 0);
  const downloads = Number(job?.downloads ?? 0);
  const progress = (mode === "downloaded") ? downloads : accepted;
  const pct = (target > 0) ? Math.min(100, Math.round((progress / target) * 100)) : 0;

  fill.style.width = `${pct}%`;

  if (!job?.id){
    text.textContent = "-";
    sub.textContent = "";
    return;
  }

  if (mode === "downloaded"){
    text.textContent = `生成 ${downloads}/${target || "?"} · 达标 ${accepted}`;
  } else {
    text.textContent = `达标 ${accepted}/${target || "?"}`;
  }
  const min = job?.config?.min_duration_minutes;
  const split = job?.config?.split_enabled ? `分段×${job?.config?.split_segments || "?"}` : "整段";
  sub.textContent = `${split} · 阈值 ${min ?? "?"} min · ${pct}%`;
}

function renderProgressWarning(){
  const warn = $("#progressWarning");
  if (!warn) return;
  if (!state.job || state.job.state !== "running"){
    warn.textContent = "";
    warn.style.display = "none";
    return;
  }
  const tasks = Object.values(state.inflight || {});
  if (!tasks.length){
    warn.textContent = "";
    warn.style.display = "none";
    return;
  }

  const now = Date.now();
  let maxElapsed = 0;
  let worst = null;
  for (const t of tasks){
    if (t.part != null){
      const p = state.parts?.[t.part];
      if (p && p.status === "accepted") continue;
    }
    const started = Date.parse(String(t.started_ts || ""));
    if (!Number.isFinite(started)) continue;
    const elapsed = now - started;
    if (elapsed > maxElapsed){
      maxElapsed = elapsed;
      worst = t;
    }
  }

  if (!worst || maxElapsed < STALL_WARNING_MS){
    warn.textContent = "";
    warn.style.display = "none";
    return;
  }

  const who = worst.account_name ? `@${worst.account_name}` : (worst.account_id ? `@${worst.account_id.slice(0,6)}` : "@?");
  const seg = (worst.part != null) ? `第 ${worst.part} 段` : "整段";
  const attempt = (worst.attempt != null) ? `#${worst.attempt}` : "";
  warn.textContent = `${who} ${seg} ${attempt} 已运行 ${fmtElapsedMs(maxElapsed)}，NotebookLM 可能未真正启动或卡住。可以切换账号或重试。`;
  warn.style.display = "block";
}

function canStopAndStitch(){
  const job = state.job;
  if (!job) return false;
  if (!job.config?.split_enabled) return false;
  if (!["running","queued"].includes(String(job.state || ""))) return false;
  const parts = state.parts || {};
  let enabled = 0;
  for (const key of Object.keys(parts)){
    const p = parts[key];
    const req = Number(p?.required ?? 1);
    if (req <= 0) continue;
    enabled += 1;
    const accepted = Number(p?.accepted ?? 0);
    if (!Number.isFinite(accepted) || accepted < 1) return false;
  }
  return enabled > 0;
}

function updateStopAndStitchBtn(){
  const btn = $("#stopAndStitchBtn");
  if (!btn) return;
  btn.disabled = !canStopAndStitch();
}

function renderInflight(){
  const box = $("#inflight");
  if (!box) return;
  const tasks = Object.values(state.inflight || {});
  if (!tasks.length){
    box.innerHTML = `<div class="hint">暂无正在生成的任务。</div>`;
    return;
  }

  tasks.sort((a,b) => String(a.started_ts||"").localeCompare(String(b.started_ts||"")));
  box.innerHTML = "";
  for (const t of tasks){
    const row = document.createElement("div");
    row.className = "taskRow";

    const left = document.createElement("div");
    left.className = "taskLeft";
    const who = t.account_name ? `@${t.account_name}` : (t.account_id ? `@${t.account_id.slice(0,6)}` : "@?");
    const seg = (t.part != null) ? `第 ${t.part} 段` : "整段";
    const attempt = (t.attempt != null) ? `#${t.attempt}` : "";
    const tid = t.task_id ? String(t.task_id).slice(0,8) : "";
    left.textContent = `${who} · ${seg} ${attempt} ${tid ? `(task ${tid}…)` : ""}`.trim();

    const right = document.createElement("div");
    right.className = "taskRight";
    const started = Date.parse(String(t.started_ts || ""));
    const elapsed = Number.isFinite(started) ? (Date.now() - started) : 0;
    right.textContent = fmtElapsedMs(elapsed);

    row.append(left, right);
    box.append(row);
  }
}

function renderSplitBoard(){
  const box = $("#splitBoard");
  if (!box) return;

  const splitEnabled = !!state.job?.config?.split_enabled;
  const segs = Number(state.splitInfo?.segments ?? state.job?.config?.split_segments ?? 0);
  if (!splitEnabled || !segs){
    box.style.display = "none";
    box.innerHTML = "";
    return;
  }

  box.style.display = "grid";
  box.innerHTML = "";
  for (let i=1;i<=segs;i++){
    const p = state.parts?.[i] || {status:"waiting", attempts:0, inflight:0, best_minutes:null, accepted:0, required:1};
    const req = Number(p.required ?? 1);
    const skipped = Number.isFinite(req) && req === 0;

    const card = document.createElement("div");
    card.className = "partCard";

    const top = document.createElement("div");
    top.className = "partTop";

    const title = document.createElement("div");
    title.className = "partTitle";
    title.textContent = `第 ${i} 段`;

    const pill = document.createElement("span");
    pill.className = "pill";
    const status = String(p.status || "waiting");
    if (status === "accepted") pill.classList.add("good");
    else if (status === "failed") pill.classList.add("bad");
    else if (status === "running" || status === "downloading") pill.classList.add("warn");
    pill.textContent = status;

    top.append(title, pill);

    const meta = document.createElement("div");
    meta.className = "hint";
    if (skipped){
      meta.textContent = "已跳过（候选数=0）";
    } else {
      const best = Number(p.best_minutes);
      const bestTxt = Number.isFinite(best) ? `${best.toFixed(2).replace(/\\.00$/,"")} min` : "-";
      const acc = Number(p.accepted ?? 0);
      const ar = (Number.isFinite(acc) && Number.isFinite(req)) ? `${acc}/${req}` : "-";
      meta.textContent = `达标 ${ar} · 尝试 ${p.attempts || 0} · 进行中 ${p.inflight || 0} · 最长 ${bestTxt}`;
    }

    card.append(top, meta);
    box.append(card);
  }
  updateStopAndStitchBtn();
}

function _ensureSplitParts(segments){
  const n = Number(segments);
  if (!Number.isFinite(n) || n <= 0) return;
  state.splitInfo = {segments: n};
  if (!state.parts || typeof state.parts !== "object") state.parts = {};
  for (let i=1;i<=n;i++){
    if (!state.parts[i]){
      state.parts[i] = {status:"waiting", attempts:0, inflight:0, best_minutes:null, accepted:0, required:1};
    }
  }
}

function _partState(idx){
  const i = Number(idx);
  if (!Number.isFinite(i) || i <= 0) return null;
  if (!state.parts[i]) state.parts[i] = {status:"waiting", attempts:0, inflight:0, best_minutes:null, accepted:0, required:1};
  return state.parts[i];
}

function updateDerivedFromEvent(ev, opts={}){
  if (!ev || typeof ev !== "object") return;
  const doRender = opts?.render !== false;

  // Split detection
  if (ev.type === "split_detected"){
    state.parts = {};
    _ensureSplitParts(ev.segments);
    const segs = Number(ev.segments || 0);
    const cands = Array.isArray(ev.candidates_per_part) ? ev.candidates_per_part : [];
    for (let i=1;i<=segs;i++){
      const p = _partState(i);
      if (!p) continue;
      p.accepted = 0;
      const n = parseInt(String(cands[i-1] ?? "1"), 10);
      let req = (Number.isFinite(n) && n >= 0) ? Math.min(n, 20) : 1;
      if (req < 0) req = 1;
      p.required = req;
      p.status = (req === 0) ? "skipped" : "waiting";
    }
    state.stitch = null;
    renderStitchPanel?.();
    renderSplitBoard();
  }

  // Inflight tracking (whole)
  if (ev.type === "generation_started" && ev.task_id){
    state.inflight[String(ev.task_id)] = {
      task_id: String(ev.task_id),
      account_id: ev.account_id,
      account_name: ev.account_name,
      part: null,
      attempt: ev.attempt,
      started_ts: ev.ts,
    };
  }
  if (["downloaded","generation_failed","attempt_error"].includes(ev.type) && ev.task_id){
    delete state.inflight[String(ev.task_id)];
  }

  // Inflight + status tracking (split parts)
  if (String(ev.type || "").startsWith("part_")){
    const p = _partState(ev.part);
    if (p){
      if (ev.type === "part_attempt_started"){
        p.attempts = Math.max(Number(p.attempts||0), Number(ev.attempt||0));
        if (p.status !== "accepted") p.status = "running";
      }
      if (ev.type === "part_generation_started" && ev.task_id){
        p.status = "running";
        p.inflight = Number(p.inflight||0) + 1;
        state.inflight[String(ev.task_id)] = {
          task_id: String(ev.task_id),
          account_id: ev.account_id,
          account_name: ev.account_name,
          part: ev.part,
          attempt: ev.attempt,
          started_ts: ev.ts,
        };
      }
      if (["part_downloaded","part_generation_failed","part_attempt_error","part_rejected","part_silence_rejected","part_accepted"].includes(ev.type) && ev.task_id){
        if (p.inflight > 0) p.inflight -= 1;
        delete state.inflight[String(ev.task_id)];
      }
      if (ev.type === "part_downloaded" && ev.duration_minutes != null){
        const m = Number(ev.duration_minutes);
        if (Number.isFinite(m)){
          if (!Number.isFinite(Number(p.best_minutes))) p.best_minutes = m;
          else p.best_minutes = Math.max(Number(p.best_minutes), m);
        }
      }
      if (ev.type === "part_accepted"){
        const req = Number(ev.candidates_required);
        const got = Number(ev.candidates_collected);
        if (Number.isFinite(req) && req > 0) p.required = req;
        if (Number.isFinite(got) && got >= 0) p.accepted = got;
        else p.accepted = Number(p.accepted||0) + 1;

        if (Number(p.accepted||0) >= Number(p.required||1)) p.status = "accepted";
        else p.status = "running";
        const m = Number(ev.duration_minutes);
        if (Number.isFinite(m)){
          if (!Number.isFinite(Number(p.best_minutes))) p.best_minutes = m;
          else p.best_minutes = Math.max(Number(p.best_minutes), m);
        }
      }
      if (ev.type === "part_attempt_error" || ev.type === "part_generation_failed" || ev.type === "part_silence_rejected"){
        if (p.status !== "accepted") p.status = "retrying";
      }
    }
  }

  if (["job_completed","job_failed","job_cancelled"].includes(ev.type)){
    state.inflight = {};
    state.stitch = null;
    renderStitchPanel?.();
  }

  if (ev.type === "split_waiting_selection"){
    state.stitch = ev;
    renderStitchPanel?.();
  }
  if (ev.type === "split_stitch_selection_received"){
    state.stitch = null;
    renderStitchPanel?.();
  }

  if (doRender){
    renderInflight();
    renderSplitBoard();
    renderProgressWarning();
    renderStitchPanel?.();
    updateStopAndStitchBtn();
  }
}

function addLog(ev){
  const log = $("#log");
  if (!log) return;
  const placeholder = log.querySelector(".logPlaceholder");
  if (placeholder) placeholder.remove();
  const [t, cls] = tagFor(ev.type);
  const row = document.createElement("div");
  row.className = "logLine";
  const tag = document.createElement("span");
  tag.className = "tag " + cls;
  tag.textContent = t;
  const msg = document.createElement("span");
  msg.className = "msg";
  msg.textContent = lineText(ev);
  const ts = document.createElement("span");
  ts.className = "ts";
  ts.style.marginLeft = "auto";
  ts.textContent = fmtTs(ev.ts);
  row.append(tag, msg, ts);
  log.append(row);
  log.scrollTop = log.scrollHeight;
}

function renderFiles(job){
  const box = $("#files");
  box.innerHTML = "";
  const files = job?.files || [];
  if (!files.length){
    box.innerHTML = `<div class="hint">还没有输出文件。生成后会出现在这里。</div>`;
    return;
  }
  for (const f of files){
    const row = document.createElement("div");
    row.className = "file";

    const top = document.createElement("div");
    top.className = "fileTop";

    const nameLine = document.createElement("div");
    nameLine.style.display = "flex";
    nameLine.style.gap = "10px";
    nameLine.style.alignItems = "center";

    const a = document.createElement("a");
    a.href = `/download/${job.id}/${encodeURIComponent(f.name)}`;
    a.textContent = f.name;
    a.target = "_blank";

    const result = String(f.result || "");
    const pill = document.createElement("span");
    pill.className = "pill";
    if (["accepted","part_accepted","stitch_completed"].includes(result)) pill.classList.add("good");
    else if (["rejected","part_rejected","stitch_rejected"].includes(result)) pill.classList.add("bad");
    else if (["downloaded","part_downloaded"].includes(result)) pill.classList.add("warn");
    pill.textContent = result || "file";

    nameLine.append(a, pill);

    const silence = String(f.silence || "");
    if (silence){
      const sp = document.createElement("span");
      sp.className = "pill";
      if (silence === "ok") sp.classList.add("good");
      else if (silence === "fail") sp.classList.add("bad");
      sp.textContent = silence === "ok" ? "静音OK" : "静音FAIL";
      nameLine.append(sp);
    }

    const meta = document.createElement("div");
    meta.className = "meta";
    const parts = [fmtBytes(f.size)];
    const dm = Number(f.duration_minutes);
    if (Number.isFinite(dm)){
      parts.push(`${dm.toFixed(2).replace(/\\.00$/,"")} min`);
    } else {
      const m = /[-_ ]([0-9]+(?:\.[0-9]+)?)min[_-]?/i.exec(String(f.name || ""));
      if (m) parts.push(`~${m[1]} min`);
    }
    if (f.account_name) parts.push(`@${f.account_name}`);
    if (silence){
      const db = f.silence_threshold_db;
      const smin = f.silence_min_duration_s;
      if (db != null && smin != null){
        parts.push(`静音${silence === "ok" ? "OK" : "FAIL"} (${db}dB/${smin}s)`);
      } else {
        parts.push(`静音${silence === "ok" ? "OK" : "FAIL"}`);
      }
    }
    meta.textContent = parts.join(" · ");

    top.append(nameLine, meta);

    const right = document.createElement("div");
    right.className = "fileRight";
    const dl = document.createElement("a");
    dl.href = a.href;
    dl.textContent = "下载";
    dl.className = "btn ghost";
    dl.target = "_blank";
    right.append(dl);

    row.append(top, right);

    const ext = String(f.name || "").split(".").pop().toLowerCase();
    const isAudio = ["mp3","mp4","m4a"].includes(ext);
    if (isAudio){
      const importBtn = document.createElement("button");
      importBtn.type = "button";
      importBtn.className = "btn";
      importBtn.textContent = "一键导入拼接";
      importBtn.addEventListener("click", async () => {
        const url = `/concat?import_job=${encodeURIComponent(job.id)}&file=${encodeURIComponent(f.name)}`;
        const win = window.open(url, "_blank");
        if (!win){
          alert("浏览器阻止了弹窗，请允许打开新窗口后重试。");
        }
      });
      right.append(importBtn);
    }
    if (isAudio){
      const preview = document.createElement("div");
      preview.className = "preview";

      if (ext === "mp4"){
        const v = document.createElement("video");
        v.controls = true;
        v.preload = "none";
        v.src = a.href;
        preview.append(v);
      } else {
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "none";
        audio.src = a.href;
        preview.append(audio);
      }
      row.append(preview);
    }

    box.append(row);
  }
}

function renderStitchPanel(){
  const box = $("#stitchPanel");
  if (!box) return;
  const job = state.job;
  const st = state.stitch;
  const splitEnabled = !!job?.config?.split_enabled;

  if (!job || !splitEnabled || !st || String(st.type || "") !== "split_waiting_selection"){
    box.style.display = "none";
    box.innerHTML = "";
    return;
  }

  const episode = Number(st.episode || 1);
  const segs = Number(st.segments || job?.config?.split_segments || 0);
  const requiredByPart = (st.required_by_part && typeof st.required_by_part === "object") ? st.required_by_part : {};
  const candidatesByPart = (st.candidates_by_part && typeof st.candidates_by_part === "object") ? st.candidates_by_part : {};

  const getKey = (obj, key) => (obj?.[key] ?? obj?.[String(key)]);
  const picks = {};

  box.style.display = "block";
  box.innerHTML = "";

  const title = document.createElement("div");
  title.className = "stitchTitle";
  title.textContent = "等待你选择拼接文件";

  const hint = document.createElement("div");
  hint.className = "hint";
  hint.textContent = "先试听下面的候选音频，再为每段选择一个版本，然后点击“开始拼接”。";

  const grid = document.createElement("div");
  grid.className = "stitchGrid";
  const enabledParts = [];

  for (let i=1;i<=segs;i++){
    const cell = document.createElement("div");
    cell.className = "stitchCell";

    const head = document.createElement("div");
    head.className = "hint";
    const req = parseInt(String(getKey(requiredByPart, i) ?? "1"), 10);
    const reqOk = Number.isFinite(req) && req >= 0 ? req : 1;
    if (reqOk <= 0){
      head.textContent = `第 ${i} 段 · 跳过（候选数=0）`;
      const sub = document.createElement("div");
      sub.className = "hint";
      sub.textContent = "无需选择";
      cell.append(head, sub);
      grid.append(cell);
      continue;
    }
    enabledParts.push(i);
    head.textContent = `第 ${i} 段 · 需要 ${reqOk} 条候选`;

    const sel = document.createElement("select");
    const raw = getKey(candidatesByPart, i);
    const list = Array.isArray(raw) ? raw.slice() : [];
    list.sort((a,b) => Number(b?.duration_minutes||0) - Number(a?.duration_minutes||0));
    if (!list.length){
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "暂无候选（请稍等）";
      sel.append(opt);
      sel.disabled = true;
    }else{
      for (const c of list){
        const file = String(c?.file || "");
        const dm = Number(c?.duration_minutes);
        const who = c?.account_name ? `@${c.account_name}` : "";
        const opt = document.createElement("option");
        opt.value = file;
        const dmTxt = Number.isFinite(dm) ? `${dm.toFixed(2).replace(/\\.00$/,"")} min` : "-";
        opt.textContent = `${dmTxt} · ${who} · ${file}`.replace(/\\s+·\\s+·/g," · ");
        sel.append(opt);
      }
      picks[i] = sel.value;
      sel.addEventListener("change", () => { picks[i] = sel.value; });
    }

    cell.append(head, sel);
    grid.append(cell);
  }

  const actions = document.createElement("div");
  actions.className = "stitchActions";

  const btn = document.createElement("button");
  btn.className = "btn primary";
  btn.type = "button";
  btn.textContent = "开始拼接";
  btn.addEventListener("click", async () => {
    const parts = {};
    for (const i of enabledParts){
      const v = String(picks[i] || "");
      if (!v){
        alert(`第 ${i} 段还没有可用候选`);
        return;
      }
      parts[String(i)] = v;
    }
    btn.disabled = true;
    btn.textContent = "提交中…";
    try{
      const res = await fetch(`/api/jobs/${encodeURIComponent(job.id)}/stitch`, {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({episode, parts}),
      });
      if (!res.ok){
        throw new Error(await res.text());
      }
      btn.textContent = "已提交，等待拼接…";
      setRunTab("live");
    }catch(e){
      btn.disabled = false;
      btn.textContent = "开始拼接";
      alert(`提交失败：${String(e)}`);
    }
  });

  actions.append(btn);

  const transWrap = document.createElement("div");
  transWrap.className = "stitchTransitionWrap";
  const transTitle = document.createElement("div");
  transTitle.className = "stitchTitle";
  transTitle.textContent = "过渡音频设置（拼接时生效）";
  const transList = document.createElement("div");
  transList.className = "transitionList";
  transWrap.append(transTitle, transList);

  box.append(title, hint, grid, transWrap, actions);
  renderTransitionList(transList);
}

function renderJobs(){
  const box = $("#jobs");
  if (!box) return;

  const jobs = Array.isArray(state.jobs) ? state.jobs : [];
  if (!jobs.length){
    box.innerHTML = `<div class="hint">还没有历史任务。</div>`;
    return;
  }

  box.innerHTML = "";
  const frag = document.createDocumentFragment();

  for (const j of jobs){
    const card = document.createElement("div");
    card.className = "jobCard";
    if (state.job?.id && j?.id === state.job.id) card.classList.add("active");

    const top = document.createElement("div");
    top.className = "jobTop";

    const left = document.createElement("div");
    left.className = "jobTitle";
    const idShort = String(j?.id || "-").slice(0,8);
    const created = fmtTs(j?.created_at || "");
    left.innerHTML = `<div class="jobId">${idShort}</div><div class="hint">${created}</div>`;

    const pill = document.createElement("span");
    pill.className = "pill";
    const st = String(j?.state || "-");
    if (st === "completed") pill.classList.add("good");
    else if (st === "failed" || st === "cancelled") pill.classList.add("bad");
    else if (st === "running" || st === "queued" || st === "waiting_selection") pill.classList.add("warn");
    pill.textContent = st;

    top.append(left, pill);

    const meta = document.createElement("div");
    meta.className = "jobMeta";
    const mode = String(j?.config?.target_mode || "accepted");
    const target = j?.config?.target_successes ?? "?";
    const accepted = Number(j?.successes ?? 0);
    const downloads = Number(j?.downloads ?? 0);
    const prog = (mode === "downloaded")
      ? `生成 ${downloads}/${target} · 达标 ${accepted}`
      : `达标 ${accepted}/${target}`;
    const split = j?.config?.split_enabled ? `分段×${j?.config?.split_segments || "?"}` : "整段";
    const min = j?.config?.min_duration_minutes ?? "?";
    const files = Array.isArray(j?.files) ? j.files.length : 0;
    meta.innerHTML = `<div class="jobLine">${prog}</div><div class="hint">${split} · 阈值 ${min} min · 文件 ${files} · 字符 ${j?.report_char_count ?? "?"}</div>`;

    if (j?.error){
      const err = document.createElement("div");
      err.className = "hint";
      err.textContent = `Error: ${String(j.error)}`;
      meta.append(err);
    }

    const actions = document.createElement("div");
    actions.className = "jobActions";

    const openBtn = document.createElement("button");
    openBtn.className = "btn ghost";
    openBtn.type = "button";
    openBtn.textContent = "打开";
    openBtn.addEventListener("click", () => loadJob(j.id, {autoSwitchTab:true}));

    const logBtn = document.createElement("a");
    logBtn.className = "btn ghost";
    logBtn.textContent = "导出日志";
    logBtn.href = `/api/jobs/${encodeURIComponent(j.id)}/events.jsonl`;
    logBtn.target = "_blank";

    actions.append(openBtn, logBtn);

    card.append(top, meta, actions);
    frag.append(card);
  }

  box.append(frag);
}

async function refreshJobs(){
  const box = $("#jobs");
  if (box) box.innerHTML = `<div class="hint">加载中…</div>`;

  try{
    const res = await fetch("/api/jobs");
    if (!res.ok) throw new Error(await res.text());
    const list = await res.json();
    state.jobs = Array.isArray(list) ? list : [];
    state.jobs.sort((a,b) => String(b?.created_at||"").localeCompare(String(a?.created_at||"")));
    renderJobs();
  }catch(e){
    if (box) box.innerHTML = `<div class="hint">加载失败：${String(e)}</div>`;
  }
}

async function loadJob(jobId, opts={}){
  const id = String(jobId || "").trim();
  if (!id) return;

  const autoSwitchTab = opts?.autoSwitchTab === true;
  const silent = opts?.silent === true;

  try{
    try{
      if (state.sse) state.sse.close();
    }catch{}
    state.sse = null;

    persistLastJobId(id);
    $("#log").innerHTML = "";
    resetDerivedState();

    const res = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(await res.text());
    const job = await res.json();
    state.job = job;
    setJobStats(job);
    renderFiles(job);

    const lr = await fetch(`/api/jobs/${encodeURIComponent(id)}/event_log?limit=5000`);
    if (lr.ok){
      const data = await lr.json();
      const events = Array.isArray(data?.events) ? data.events : [];
      for (const ev of events){
        addLog(ev);
        updateLiveState(ev);
        updateDerivedFromEvent(ev, {render:false});
      }
      if (!events.length){
        $("#log").innerHTML = `<div class="hint logPlaceholder">暂无日志（旧版本任务可能没有落盘日志）。</div>`;
      }
      renderLive();
      renderInflight();
      renderSplitBoard();
    }

    const running = (job?.state === "running" || job?.state === "queued" || job?.state === "waiting_selection");
    $("#startBtn").disabled = running;
    $("#cancelBtn").disabled = !running;
    updateStopAndStitchBtn();
    if (running){
      connectSSE(id);
    }

    if (autoSwitchTab){
      setRunTab(running ? "live" : "files");
    }

    renderJobs();
    return true;
  }catch(e){
    if (!silent){
      alert(`加载任务失败：${String(e)}`);
    }
    return false;
  }
}

async function refreshAccounts(){
  const res = await fetch("/api/accounts");
  state.accounts = await res.json();
  renderAccounts();
}

function _browserLabel(v){
  const b = String(v || "").toLowerCase();
  if (b === "edge" || b === "msedge") return "Edge";
  if (b === "chrome") return "Chrome";
  return "Chromium";
}

function renderLoginProfiles(){
  const profileSel = $("#loginProfile");
  const browserSel = $("#loginBrowser");
  if (!profileSel || !browserSel) return;

  const browser = String(browserSel.value || "edge").toLowerCase();
  const prev = profileSel.value;

  profileSel.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "临时 Profile（需要手动登录）";
  profileSel.append(none);

  const profiles = (state.browserProfiles || []).filter(p => (p?.browser || "").toLowerCase() === browser);
  for (const p of profiles){
    const opt = document.createElement("option");
    opt.value = p.id;
    const name = p.display_name || p.profile_dir || p.id;
    const email = p.user_name ? ` · ${p.user_name}` : "";
    const dir = p.profile_dir ? ` (${p.profile_dir})` : "";
    opt.textContent = `${name}${email}${dir}`;
    profileSel.append(opt);
  }

  // best-effort restore selection
  if (prev && Array.from(profileSel.options).some(o => o.value === prev)){
    profileSel.value = prev;
  }
}

async function refreshBrowserProfiles(){
  try{
    const res = await fetch("/api/browser/profiles");
    if (!res.ok) return;
    const list = await res.json();
    state.browserProfiles = Array.isArray(list) ? list : [];
    renderLoginProfiles();
  }catch{}
}

function renderLoginBox(){
  const box = $("#loginBox");
  const status = $("#loginStatus");
  const hint = $("#loginHint");
  const finishBtn = $("#loginFinishBtn");
  const cancelBtn = $("#loginCancelBtn");
  const startBtn = $("#loginAccBtn");

  const s = state.loginSession;
  if (!s){
    box.style.display = "none";
    if (state.loginPoll){
      clearInterval(state.loginPoll);
      state.loginPoll = null;
    }
    if (startBtn) startBtn.disabled = false;
    return;
  }

  box.style.display = "block";
  const parts = [];
  parts.push(`state=${s.state}`);
  if (s.message) parts.push(s.message);
  if (s.last_url) parts.push(`URL: ${s.last_url}`);
  if (s.error) parts.push(`ERROR: ${s.error}`);
  status.textContent = parts.join(" · ");

  if (hint){
    const b = _browserLabel(s.browser);
    if (s.profile_mode === "system"){
      const prof = s.profile_directory ? `（${s.profile_directory}）` : "";
      hint.textContent = `已使用 ${b} 的本机 Profile${prof}：理论上可复用已有 Google 登录；如提示 Profile 占用，请先关闭所有 ${b} 窗口后重试。确保停留在 NotebookLM 首页再点“完成保存”。`;
    } else {
      hint.textContent = `会弹出一个独立的 ${b} 窗口：完成 Google 登录并确保打开 NotebookLM 首页，然后点“完成保存”。`;
    }
  }

  if (finishBtn) finishBtn.disabled = (s.state !== "waiting_login");
  if (cancelBtn) cancelBtn.disabled = false;
  if (startBtn) startBtn.disabled = true;
}

async function refreshLoginSession(){
  if (!state.loginSession) return;
  const res = await fetch("/api/accounts/login/sessions");
  if (!res.ok) return;
  const list = await res.json();
  const s = list.find(x => x.id === state.loginSession.id);
  if (!s){
    state.loginSession = null;
    renderLoginBox();
    return;
  }
  state.loginSession = s;
  renderLoginBox();
}

async function initLoginSession(){
  try{
    const res = await fetch("/api/accounts/login/sessions");
    if (!res.ok) return;
    const list = await res.json();
    if (!Array.isArray(list) || !list.length) return;
    // If there's an existing session (e.g. page refresh), attach to it.
    state.loginSession = list[0];
    renderLoginBox();
    state.loginPoll = setInterval(refreshLoginSession, 1200);
  }catch{}
}

function renderAccounts(){
  const list = $("#accountsList");
  list.innerHTML = "";

  if (!state.accounts.length){
    list.innerHTML = `<div class="hint">还没有账号。先在下面上传每个 Google 账号对应的 <code>storage_state.json</code>。</div>`;
    return;
  }

  for (const a of state.accounts){
    const row = document.createElement("div");
    row.className = "account";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.accountId = a.id;

    const name = document.createElement("div");
    name.className = "name";
    name.innerHTML = `<strong>${a.name}</strong><small>${a.id}</small>`;

    const attempts = document.createElement("input");
    attempts.className = "miniInput";
    attempts.type = "number";
    attempts.min = "1";
    attempts.max = "200";
    attempts.value = "20";
    attempts.title = "该账号最多尝试次数";
    attempts.dataset.attemptsFor = a.id;

    const del = document.createElement("button");
    del.className = "iconBtn";
    del.title = "删除账号";
    del.textContent = "×";
    del.addEventListener("click", async () => {
      if (!confirm(`删除账号「${a.name}」？这会移除本地保存的 storage_state.json。`)) return;
      await fetch(`/api/accounts/${a.id}`, {method:"DELETE"});
      await refreshAccounts();
    });

    const verify = document.createElement("button");
    verify.className = "iconBtn";
    verify.title = "验证账号可用性";
    verify.textContent = "✓";
    verify.addEventListener("click", async () => {
      verify.disabled = true;
      try{
        const res = await fetch(`/api/accounts/${a.id}/verify`, {method:"POST"});
        if (!res.ok){
          let detail = null;
          try{ detail = (await res.json())?.detail; }catch{}
          throw new Error(detail || await res.text());
        }
        const data = await res.json();
        alert(`验证成功：账号「${a.name}」可以访问 NotebookLM（notebooks=${data.notebooks}）`);
      }catch(e){
        alert(`验证失败：${String(e)}`);
      }finally{
        verify.disabled = false;
      }
    });

    row.append(checkbox, name, attempts, verify, del);
    list.append(row);
  }
}

function updatePromptSavedAt(iso){
  const el = $("#promptSavedAt");
  if (!el) return;
  if (!iso){
    el.textContent = "";
    return;
  }
  el.textContent = `上次保存：${fmtTs(iso)}`;
}

function updatePromptDatePreview(){
  const el = $("#promptDatePreview");
  if (!el) return;
  const tokens = getDateTokens();
  el.textContent = `日期预览：今日 ${tokens["{{TODAY}}"]} · 明日 ${tokens["{{TOMORROW}}"]}`;
  updatePromptPreview();
  updateSplitPromptPreview();
}

function _readSplitParts(){
  try{
    const raw = localStorage.getItem(STORAGE_SPLIT_PARTS);
    const parsed = JSON.parse(raw || "[]");
    if (Array.isArray(parsed)){
      return parsed.map(v => String(v || ""));
    }
  }catch{}
  return [];
}

function _writeSplitParts(parts){
  try{
    localStorage.setItem(STORAGE_SPLIT_PARTS, JSON.stringify(parts || []));
  }catch{}
}

function _readSplitCandidates(){
  try{
    const raw = localStorage.getItem(STORAGE_SPLIT_CANDIDATES);
    const parsed = JSON.parse(raw || "[]");
    if (Array.isArray(parsed)){
      return parsed.map(v => {
        const n = parseInt(String(v ?? "1"), 10);
        if (!Number.isFinite(n) || n < 0) return 1;
        return Math.min(n, 20);
      });
    }
  }catch{}
  return [];
}

function _writeSplitCandidates(cands){
  try{
    localStorage.setItem(STORAGE_SPLIT_CANDIDATES, JSON.stringify(cands || []));
  }catch{}
}

function _readTransitions(){
  return _readJSON(STORAGE_STITCH_TRANSITIONS, []);
}

function _writeTransitions(list){
  try{
    localStorage.setItem(STORAGE_STITCH_TRANSITIONS, JSON.stringify(list || []));
  }catch{}
}

function _readTransitionRepeats(){
  return _readJSON(STORAGE_STITCH_TRANSITION_REPEATS, []);
}

function _writeTransitionRepeats(list){
  try{
    localStorage.setItem(STORAGE_STITCH_TRANSITION_REPEATS, JSON.stringify(list || []));
  }catch{}
}

function _readTransitionDurations(){
  return _readJSON(STORAGE_STITCH_TRANSITION_DURATIONS, []);
}

function _writeTransitionDurations(list){
  try{
    localStorage.setItem(STORAGE_STITCH_TRANSITION_DURATIONS, JSON.stringify(list || []));
  }catch{}
}

function _readTransitionLock(){
  return _readJSON(STORAGE_STITCH_TRANSITION_LOCK, null);
}

function _writeTransitionLock(value){
  try{
    localStorage.setItem(STORAGE_STITCH_TRANSITION_LOCK, JSON.stringify(!!value));
  }catch{}
}

function _hasStoredTransitions(){
  try{
    return localStorage.getItem(STORAGE_STITCH_TRANSITIONS) !== null;
  }catch{
    return false;
  }
}

function _hasStoredTransitionRepeats(){
  try{
    return localStorage.getItem(STORAGE_STITCH_TRANSITION_REPEATS) !== null;
  }catch{
    return false;
  }
}

function _hasStoredTransitionDurations(){
  try{
    return localStorage.getItem(STORAGE_STITCH_TRANSITION_DURATIONS) !== null;
  }catch{
    return false;
  }
}

function _hasStoredTransitionLock(){
  try{
    return localStorage.getItem(STORAGE_STITCH_TRANSITION_LOCK) !== null;
  }catch{
    return false;
  }
}

function _defaultTransitions(segments){
  const key = Number(segments) || 0;
  return (DEFAULT_TRANSITIONS_BY_SEGMENTS[key] || []).slice();
}

function _defaultTransitionRepeats(segments){
  const gaps = Math.max(0, (Number(segments) || 0) - 1);
  return Array.from({length: gaps}, () => 1);
}

function _defaultTransitionDurations(segments){
  const key = Number(segments) || 0;
  const preset = DEFAULT_TRANSITION_DURATIONS_BY_SEGMENTS[key];
  if (Array.isArray(preset) && preset.length){
    return preset.slice();
  }
  const gaps = Math.max(0, key - 1);
  return Array.from({length: gaps}, () => 30);
}

function _getSplitSegments(){
  const n = parseInt($("#splitSegments")?.value || "3", 10);
  return Number.isFinite(n) && n > 0 ? n : 3;
}

function _normalizeSplitParts(parts, segments){
  const out = Array.isArray(parts) ? parts.slice(0, segments) : [];
  while (out.length < segments) out.push("");
  return out;
}

function _normalizeSplitCandidates(cands, segments){
  const out = Array.isArray(cands) ? cands.slice(0, segments) : [];
  for (let i = 0; i < out.length; i++){
    const n = parseInt(String(out[i] ?? "1"), 10);
    if (!Number.isFinite(n) || n < 0) out[i] = 1;
    else out[i] = Math.min(n, 20);
  }
  while (out.length < segments) out.push(1);
  return out;
}

function _normalizeTransitions(list, segments){
  const gaps = Math.max(0, (Number(segments) || 0) - 1);
  const out = Array.isArray(list) ? list.slice(0, gaps) : [];
  while (out.length < gaps) out.push("");
  return out.map(v => String(v || ""));
}

function _normalizeTransitionRepeats(list, segments){
  const gaps = Math.max(0, (Number(segments) || 0) - 1);
  const out = Array.isArray(list) ? list.slice(0, gaps) : [];
  while (out.length < gaps) out.push(1);
  return out.map(v => {
    const n = parseInt(v || "1", 10);
    if (!Number.isFinite(n)) return 1;
    if (n < 0) return 0;
    if (n > 5) return 5;
    return n;
  });
}

function _normalizeTransitionDurations(list, segments){
  const gaps = Math.max(0, (Number(segments) || 0) - 1);
  const out = Array.isArray(list) ? list.slice(0, gaps) : [];
  while (out.length < gaps) out.push(30);
  return out.map(v => {
    const n = parseFloat(v ?? "0");
    if (!Number.isFinite(n)) return 0;
    if (n < 0) return 0;
    if (n > 600) return 600;
    return n;
  });
}

function _buildSplitPreviewContent(partIndex, parts){
  const idx = partIndex - 1;
  const base = String(parts?.[idx] || "").trim();
  if (!base){
    return "";
  }
  const extra = $("#instructions")?.value || "";
  const merged = mergeText(base, extra);
  return applyDateTokens(merged);
}

function renderSplitPromptList(){
  const box = $("#splitPromptList");
  if (!box) return;

  const segments = _getSplitSegments();
  const parts = _normalizeSplitParts(_readSplitParts(), segments);
  const cands = _normalizeSplitCandidates(_readSplitCandidates(), segments);
  _writeSplitParts(parts);
  _writeSplitCandidates(cands);

  box.innerHTML = "";
  for (let i = 1; i <= segments; i++){
    const card = document.createElement("div");
    card.className = "splitPromptCard";

    const header = document.createElement("div");
    header.className = "splitPromptHeader";

    const title = document.createElement("div");
    title.className = "splitPromptTitle";
    title.textContent = `第 ${i} 段提示词`;

    const controls = document.createElement("div");
    controls.className = "splitPromptControls";

    const candCtl = document.createElement("div");
    candCtl.className = "splitCandidateCtl";
    const candHint = document.createElement("div");
    candHint.className = "hint";
    candHint.textContent = "候选数";
    const candInput = document.createElement("input");
    candInput.className = "splitCandidateInput";
    candInput.type = "number";
    candInput.min = "0";
    candInput.max = "20";
    candInput.value = String(cands[i - 1] ?? 1);
    candInput.dataset.part = String(i);
    candInput.addEventListener("input", () => {
      const next = _normalizeSplitCandidates(_readSplitCandidates(), segments);
      const n = parseInt(String(candInput.value || "1"), 10);
      if (!Number.isFinite(n) || n < 0) next[i - 1] = 1;
      else next[i - 1] = Math.min(n, 20);
      _writeSplitCandidates(_normalizeSplitCandidates(next, segments));
    });
    candCtl.append(candHint, candInput);

    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = `Part ${i}/${segments}`;

    controls.append(candCtl, pill);
    header.append(title, controls);

    const input = document.createElement("textarea");
    input.className = "splitPromptInput";
    input.dataset.part = String(i);
    input.placeholder = "输入该段固定提示词…";
    input.value = parts[i - 1] || "";

    const previewLabel = document.createElement("div");
    previewLabel.className = "hint";
    previewLabel.textContent = "预览（已替换日期 + 拼接追加提示词）";

    const preview = document.createElement("textarea");
    preview.className = "splitPromptPreview";
    preview.readOnly = true;
    preview.dataset.previewPart = String(i);
    preview.value = _buildSplitPreviewContent(i, parts);
    if (!preview.value){
      preview.placeholder = "未设置分段提示词（将回退到全局固定提示词）";
    }

    input.addEventListener("input", () => {
      const next = _normalizeSplitParts(_readSplitParts(), segments);
      next[i - 1] = input.value || "";
      _writeSplitParts(next);
      preview.value = _buildSplitPreviewContent(i, next);
      if (!preview.value){
        preview.placeholder = "未设置分段提示词（将回退到全局固定提示词）";
      }
    });

    card.append(header, input, previewLabel, preview);
    box.append(card);
  }
}

function renderTransitionList(target){
  const box = (typeof target === "string" ? $(target) : target) || $("#stitchTransitionList");
  if (!box) return;
  const segments = _getSplitSegments();
  const gaps = Math.max(0, segments - 1);
  let list = _normalizeTransitions(_readTransitions(), segments);
  let repeats = _normalizeTransitionRepeats(_readTransitionRepeats(), segments);
  let durations = _normalizeTransitionDurations(_readTransitionDurations(), segments);
  let lock = _readTransitionLock();

  if (!_hasStoredTransitions()){
    const defaults = _defaultTransitions(segments);
    if (defaults.length){
      list = _normalizeTransitions(defaults, segments);
    }
    _writeTransitions(list);
  }else{
    _writeTransitions(list);
  }

  if (!_hasStoredTransitionRepeats()){
    repeats = _normalizeTransitionRepeats(_defaultTransitionRepeats(segments), segments);
    _writeTransitionRepeats(repeats);
  }else{
    _writeTransitionRepeats(repeats);
  }

  if (!_hasStoredTransitionDurations()){
    durations = _normalizeTransitionDurations(_defaultTransitionDurations(segments), segments);
    _writeTransitionDurations(durations);
  }else{
    _writeTransitionDurations(durations);
  }

  if (!_hasStoredTransitionLock()){
    lock = true;
    _writeTransitionLock(lock);
  }
  if (lock === null){
    lock = true;
  }
  if ($("#stitchTransitionLock")) $("#stitchTransitionLock").checked = !!lock;

  box.innerHTML = "";
  if (gaps <= 0){
    box.innerHTML = `<div class="hint">当前分段不足 2 段，无需过渡音频。</div>`;
    return;
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

  for (let i=1;i<=gaps;i++){
    const row = document.createElement("div");
    row.className = "transitionRow";

    const label = document.createElement("div");
    label.className = "hint";
    label.textContent = `第 ${i}-${i+1} 段过渡音频（重复/时长秒）`;

    const cell = document.createElement("div");
    cell.className = "transitionCell";

    const input = document.createElement("input");
    input.type = "text";
    input.className = "transitionInput";
    input.placeholder = "可选：填写本地路径，或拖拽音频文件到右侧";
    input.value = list[i - 1] || "";
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
    repeat.value = String(repeats[i - 1] ?? 1);
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
    duration.value = String(durations[i - 1] ?? 30);
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

    input.disabled = false;
    drop.classList.remove("disabled");
    drop.style.pointerEvents = "";
    drop.textContent = lock ? "替换默认" : "拖拽或点击选择";

    cell.append(input, repeat, duration, drop, fileInput);
    row.append(label, cell);
    box.append(row);
  }
}

function resetTransitionDefaults(){
  const segments = _getSplitSegments();
  const list = _normalizeTransitions(_defaultTransitions(segments), segments);
  const repeats = _normalizeTransitionRepeats(_defaultTransitionRepeats(segments), segments);
  const durations = _normalizeTransitionDurations(_defaultTransitionDurations(segments), segments);
  _writeTransitions(list);
  _writeTransitionRepeats(repeats);
  _writeTransitionDurations(durations);
  _writeTransitionLock(true);
  renderTransitionList();
}

function updateSplitPromptPreview(){
  const segments = _getSplitSegments();
  const parts = _normalizeSplitParts(_readSplitParts(), segments);
  for (let i = 1; i <= segments; i++){
    const preview = $(`#splitPromptList textarea[data-preview-part="${i}"]`);
    if (!preview) continue;
    preview.value = _buildSplitPreviewContent(i, parts);
    if (!preview.value){
      preview.placeholder = "未设置分段提示词（将回退到全局固定提示词）";
    }
  }
}

async function loadSplitPrompt(opts={}){
  const fallback = opts?.fallbackToDefault === true;
  try{
    const res = await fetch("/api/prompts/split");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (Array.isArray(data?.parts) && data.parts.length){
      _writeSplitParts(data.parts);
      renderSplitPromptList();
      const el = $("#splitPromptSavedAt");
      if (el) el.textContent = data?.updated_at ? `上次保存：${fmtTs(data.updated_at)}` : "";
      updateSplitPromptPreview();
      return true;
    }
  }catch{
    // ignore
  }

  if (fallback){
    const current = _readSplitParts().some(p => String(p || "").trim());
    if (!current){
      resetSplitPrompt();
    } else {
      renderSplitPromptList();
      updateSplitPromptPreview();
    }
    return true;
  }
  return false;
}

async function saveSplitPrompt(){
  const segments = _getSplitSegments();
  const parts = _normalizeSplitParts(_readSplitParts(), segments);
  const btn = $("#saveSplitPromptBtn");
  if (btn) btn.disabled = true;
  try{
    const res = await fetch("/api/prompts/split", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({parts}),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    _writeSplitParts(parts);
    const el = $("#splitPromptSavedAt");
    if (el) el.textContent = data?.updated_at ? `上次保存：${fmtTs(data.updated_at)}` : "";
    alert("分段提示词已保存");
  }catch(e){
    alert(`保存失败：${String(e)}`);
  }finally{
    if (btn) btn.disabled = false;
  }
}

function resetSplitPrompt(){
  const segments = _getSplitSegments();
  const defaults = [];
  for (let i = 0; i < segments; i++){
    defaults.push(DEFAULT_SPLIT_TEMPLATES[i] || "");
  }
  _writeSplitParts(defaults);
  _writeSplitCandidates(Array.from({length: segments}, () => 1));
  renderSplitPromptList();
  updateSplitPromptPreview();
  const el = $("#splitPromptSavedAt");
  if (el) el.textContent = "";
}

async function loadFixedPrompt(opts={}){
  const fallback = opts?.fallbackToDefault === true;
  try{
    const res = await fetch("/api/prompts/fixed");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const content = String(data?.content || "").trim();
    if (content){
      if ($("#fixedInstructions")) $("#fixedInstructions").value = content;
      if ($("#promptName")) $("#promptName").value = String(data?.name || DEFAULT_PROMPT_NAME);
      if ($("#useFixedInstructions")) $("#useFixedInstructions").checked = true;
      if ($("#promptPreset")) $("#promptPreset").value = "custom";
      _writeLS(STORAGE_FIXED_INSTRUCTIONS, content);
      _writeLS(STORAGE_USE_FIXED_INSTRUCTIONS, "1");
      _writeLS(STORAGE_PROMPT_PRESET, "custom");
      _writeLS(STORAGE_PROMPT_NAME, $("#promptName")?.value || DEFAULT_PROMPT_NAME);
      updatePromptSavedAt(data?.updated_at || "");
      updatePromptDatePreview();
      return true;
    }
  }catch{
    // ignore
  }

  if (fallback){
    const current = String($("#fixedInstructions")?.value || "").trim();
    if (!current){
      applyPromptPreset("liurun_podcast", false);
      if ($("#promptPreset")) $("#promptPreset").value = "liurun_podcast";
      if ($("#promptName")) $("#promptName").value = DEFAULT_PROMPT_NAME;
      _writeLS(STORAGE_PROMPT_PRESET, "liurun_podcast");
      _writeLS(STORAGE_PROMPT_NAME, DEFAULT_PROMPT_NAME);
      updatePromptSavedAt("");
    }
    return true;
  }
  return false;
}

async function saveFixedPrompt(){
  const name = String($("#promptName")?.value || DEFAULT_PROMPT_NAME).trim() || DEFAULT_PROMPT_NAME;
  const content = String($("#fixedInstructions")?.value || "").trim();
  if (!content) return alert("固定提示词不能为空");

  const btn = $("#savePromptBtn");
  if (btn) btn.disabled = true;
  try{
    const res = await fetch("/api/prompts/fixed", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name, content}),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    _writeLS(STORAGE_PROMPT_NAME, name);
    _writeLS(STORAGE_FIXED_INSTRUCTIONS, content);
    _writeLS(STORAGE_PROMPT_PRESET, "custom");
    updatePromptSavedAt(data?.updated_at || "");
    alert("固定提示词已保存");
  }catch(e){
    alert(`保存失败：${String(e)}`);
  }finally{
    if (btn) btn.disabled = false;
  }
}

function resetFixedPrompt(){
  applyPromptPreset("liurun_podcast", false);
  if ($("#promptPreset")) $("#promptPreset").value = "liurun_podcast";
  updatePromptSavedAt("");
}

const DEFAULT_PROMPT_NAME = "刘润商业早新闻 · 生成播客";
const DEFAULT_PROMPT_VERSION = "20251209";
const DEFAULT_PROMPT_TEMPLATE = `<prompt_meta>
提示词名称：刘润商业早新闻 · 生成播客
版本号：20251209
</prompt_meta>

<summary>
- 开头固定口播：“欢迎收听刘润每日商业早新闻……”
- 结尾固定口播：“感谢收听刘润每日商业早新闻。我们明天再见。”

【条数与覆盖要求（与时间同级的硬约束）】
- 先从输入素材中，识别并统计当日商业新闻条目总数 N。
- 本播客脚本【必须完整播报这 N 条新闻，缺一条就视为失败】。
- 开头口播：“今天一共有 N 条新闻。”
- 每一条都要单独成段、单独编号、单独拆解，不能合并、跳过或一笔带过。
- 若播报条数 < N，或编号不连续 / 有缺号（如缺 [第17条]），视为未满足提示词要求。

【时间总纲（硬性约束 · 优先级 NO.1）】
⚠ 对所有系统 / 模型默认时长策略具有最高优先级覆盖效力，高于任何内置预设（如“自动缩短”“默认 5 分钟”等）。

1）整档播客总时长【必须 ≥ 60 分钟】：
   - 不得因任何默认时长限制 / 自动精简 / 摘要倾向而缩短到 60 分钟以下。

2）目标节奏：每条新闻 ≈ 1.5 分钟（±10%），单条时长尽量接近；
   - 建议：每条文字不少于 220 字，推荐 250–300 字。

3）理想状态：总时长 ≈ N × 1.5 分钟；
   - 若估算仍不足 60 分钟，则通过增加“逻辑拆解 + 男女对话 + 互动提问 + 启发延展”补足时长，优先保证“≥ 60 分钟”。

4）当任何其他要求（简洁、少字数等）与“总时长 ≥ 60 分钟”和“全部 N 条播完”冲突时：
   - 一律以【总时长 ≥ 60 分钟】和【全部 N 条播报完】为准。
</summary>

<style>
【整体风格】
- 身份：有梗但不油腻的商业顾问，对话对象是“你”（创业者 / 企业家 / 管理者）。
- 语气：专业克制、有温度，有轻微冷幽默和生活化比喻，让人“会心一笑”而非尴尬：
  - “这波操作，有点像股东在你睡觉的时候，悄悄给你改了 KPI。”
- 目标：信息很硬，体验很轻松——像“早饭配完一套深度商业早新闻”，而不是财经早八。

【男女分工：男逗哏 × 女捧哏】
- 男同学（主讲人 / 逗哏）：主线播报 + 逻辑拆解 + 核心启发 + 抛包袱。
  - 低沉稳健，微带东北尾音，语速约 160 wpm；常用“对吧”“重点来了”。
  - 幽默：理工男式冷幽默、反差梗、认真讲笑话，偶尔自黑：
    - “这波操作，连我们打工人都看懂——就是不赚钱。”
- 女同学（观众代言人 / 捧哏）：提问 + 追问 + 情绪反应 + 互动引导。
  - 清亮带笑，语速约 190 wpm，“哇～”“真的假的？！”弹幕感强。
  - 幽默：真情实感 + 小吐槽：
    - “这价格听起来，有点像在跟打工人的银行卡开玩笑。”

【读法规范】
- 所有含“%”的数字，按“百分之 X”播报，例如 3% 念“百分之三”。
- 所有“A+B”符号，用中文读做“A加B”，而不是“A plus B”。
- “美的”作为一家公司时，读作“美迪”。
- 太卷了，三个字在一起时，“卷”读第三声。
- 摩尔线程，线程的程字，读“城”。
- 禁止在脚本中自称“AI”或提及模型、算法等技术实现。
</style>

<engagement>
参与感与互动引导（主要由女声承担），这些内容【必须实际出现在脚本中】：

1. 行为引导自然植入
在讲解过程中，适度插入口播，例如：
- “如果你也有类似的看法，欢迎打在评论区，我来陪你聊！”
- “记得点个关注，我们每天早上都在～”
- “觉得这一条有点东西的，点个赞让我看看你在不在。”
- “转发给那个总说自己看不懂经济的朋友”
- “来，说说你支持哪一方？我们弹幕 PK 一下！”
- “我数三下，看看有多少人点一下右下角的小心心好不好？”

2. 代入式提问
每 3–5 条新闻，穿插 1 次争议性或立场式提问，激发表达欲，例如：
- “你觉得这是资本的收割，还是技术的胜利？”
- “这波降价，是你会买单的信号吗？”
- “说实话，如果是你，你会跳槽吗？评论区见～”
- “有人觉得这事很正常，有人炸锅了，你是哪边？”

3. 陪伴式直播氛围
- 使用“欢迎回来”“还在听的朋友举个手”“别急，还有更炸的”等词制造在场感；
- 用“有人刚才在评论区问到……我们来详细讲一下”模拟实时互动；
- 适当设置“互动任务”，如“10 分钟后我们来投票”“这一条我想听听你们弹幕的声音”；
- 结尾鼓励留言：“你最关注的是哪一条？说出来，我们下一期重点讲！”
- 整体节奏：让听众觉得“边刷牙、边通勤、边笑一笑就把新闻听完了”，而不是在上早八财经课。

4. 情绪共振与分工（男逗哏 × 女捧哏）
- 男同学：抛梗、拆解、总结，用稳健逻辑和适度幽默设计“包袱”，提出观点、节奏和悬念；
- 女同学：接梗、追问、共情，替观众问出：
  - “这个词具体是什么意思呀？”
  - “他为什么要这么干？图什么？”
  - “听上去很热闹，但对我们普通人/创业者到底重要在哪？”
- 幽默边界：
  - 可以调侃“打工人的加班”“老板看 KPI 的表情”“创业者的头发数量”，但不低俗、不黄、不攻击任何具体群体；
  - 笑点优先来自“认知反差”和“生活共鸣”，而不是嘲笑他人。

【女声参与规则（柔性 + 分层）】

1. 提问规则（刚性要求）
- 每一条新闻中，女声至少出现 1 句“真问题”，优先问大家心里的疑惑：
  - “他这么做，最大的风险是啥呀？”
  - “听上去挺厉害，但真的有人买单吗？”

2. 互动规则（整体约束，而不是每条死板执行）
- 平均每 3–4 条新闻，安排 1 次明显的“弹幕 / 点赞 / 评论”引导：
  - 可以集中在节奏需要拉高的几条，而不是每条都来一遍。
- 示例：
  - “如果你也觉得这有点熟悉，点个赞让我看看你在不在。”
  - “来，评论区打个 1，我看看有多少人正在经历同款难题。”

3. 氛围规则
- 可以偶尔用“欢迎回来”“还在听的朋友举个手”“别急，后面还有更炸的”等语句串联段落；
- 这些句子不要求均匀分布，但整体听完要有“被陪着听完一整期”的感觉。

</engagement>

<item_template>
【每条新闻的“素材盒子”（必备要素，但不要求固定顺序、句式统一）】

对每一条新闻，请尽量覆盖下面 4 个要素，但可以自由融合、打乱顺序，用对话方式自然表达，而不是生硬地分段：

1）事实盒子（男声主导）
- 把“时间 / 谁 / 做了什么 / 初步结果”讲清楚，2–4 句即可。
- 用适合口播的语言，而不是公文或公告口吻。

2）好奇盒子（女声主导，男声回应）
- 女声至少提出 1 个“观众真的会问”的问题，例如：
  - “等等，他为什么要选在这个时间点降价呀？”
  - “听上去挺热闹的，但对我们普通打工人有啥影响？”
- 男声用 2–4 句回应，把逻辑讲清楚。

3）梗和比喻盒子（鼓励产生笑点）
- 每条至少有 1 个轻微笑点，可以是：
  - 生活类比（高铁上换车轮、老板半夜改 KPI、打工人钱包被支配感）；
  - 自黑式吐槽（“这波操作，连我们打工人都看懂——就是不赚钱。”）。
- 优先从「打工人 / 老板 / 创业者」的日常场景里找，而不是硬编段子。

4）启发盒子（落在“你”身上）
- 用 1–3 句说明“这件事，和你有什么关系”，可以选用：
  - “如果你在做类似的生意，要特别注意的是……”
  - “作为管理者，你今天可以多想一步：……”
  - “对正在找方向的创业者来说，这又是一块‘坑在哪里’的路标。”

【结构自由度】
- 上述 4 个要素都要出现，但可以通过男女对话自然融合；
- 不要求每条都明确分成 1、2、3、4 段，只要听感自然、信息齐全即可。
</item_template>

<rules>
【内容与立场】
- 禁臆测：不虚构、不脑补细节。
- 死守原文：数字 / 日期 / 金额 / 专有名词 100% 准确。
- 不主动展开政治 / 国家 / 意识形态讨论。
- 如新闻涉及国家 / 地缘敏感：
  - 仅做必要事实说明，不煽动对立；
  - 如必须体现立场，一律对齐中国公开立场，用“根据中国方面的表述……”等方式引用。

【中立与语言】
- 不用“神”“暴雷”“凉凉”“韭菜”等极端标签；
- 不做人身攻击，不“吹上天”也不“一棍子打死”；
- 用“我们可以这样理解”“一种可能的解释是……”表达分析，而不是“结论就是这样”。

【时间锚定】
- 尽量使用“昨日”或具体日期（如“11 月 30 日”），避免“最近”“之前”“近期”等模糊词。

【发音与可听性自检（生成脚本前必须自查一遍）】
- 百分号统一读作“百分之 X”。
- 所有“A+B”符号，用中文读做“A加B”，而不是“A plus B”。
- 检查年份、金额、倍率等数字是否顺口、无歧义。
- 英文 / 品牌 / 技术名词：首次出现时可加括号读法提示，如“ASML（A-S-M-L）”“NVIDIA（英伟达）”。
- 生僻人名 / 地名：用常见中文读法或拼音标注一次。
- 将“Q3 FY2025”改成“2025 财年第三季度”等更适合口播的形式。

【执行底线】
- 口播里只用“你”，不用“你们 / 大家”。
- 全程不提“AI”“大模型”“我是程序/模型”等字眼。
- 当“轻松有趣”和“准确 / 中立”冲突时，永远优先准确 / 中立；
- 但在事实已经清楚、没有争议的前提下：
  - 优先选择更口语化、更有画面感、更让人会心一笑的说法；
  - 同一个意思，如果有“教科书版”和“生活吐槽版”，优先使用“生活吐槽版”。
- 当任何其他要求（简洁、少字数、条数压缩等）与【整档时长 ≥ 60 分钟】和【N 条全部播报完】冲突时，永远优先这两条。
</rules>

<final_check>
【生成完毕后的终检步骤（强制执行）】

在输出最终脚本之前，必须完成“条数完整性 + 时长逻辑自检”：

1）条数与编号检查：
   - 根据输入素材确定新闻条数 N；
   - 在文稿中依次确认 [第1条]、[第2条] …… [第N条] 是否全部出现；
   - 若缺任何编号（如无 [第17条]），必须补写对应新闻解读后再结束脚本；
   - 若出现重复编号（如两个 [第05条]），需修正为 1–N 的连续唯一编号。

2）结构与互动检查：
   - 确认每一条都包含：男声事实概述、女声至少 1 句提问、剥洋葱式拆解、明确启发、与“我们”的关系收束。

3）时长逻辑检查：
   - 估算每条时长 ≈ 1.5 分钟 × N 条 ≈ N × 1.5 分钟；
   - 若明显低于这一密度（大量一两句话就带过），需要增加必要拆解、互动和启发，使整体内容量足以支撑 ≥ 60 分钟播放时长。

仅当【N 条编号完整 + 每条结构完整 + 内容足以支撑 ≥ 60 分钟】同时满足时，才输出最终脚本。
</final_check>

<metadata>
此播客音频脚本是在 [{{TODAY}}] 为 [{{TOMORROW}}] 的晨间播报而创作的。
</metadata>`;

const DEFAULT_SPLIT_TEMPLATES = [
`# Role: 刘润商业早新闻 · 分段生成版 (Part 1/3)
# Target: 生成第 1-10 条新闻的音频脚本

<summary>
【分段生成特别控制 · 第一部分】
这是一档 50 分钟长播客的【第一段】。
1. **范围**：请仅识别并处理上传素材中的 **第 1 条 到 第 10 条** 新闻。
2. **开头口播（保留）**：“欢迎收听刘润每日商业早新闻……”（按原版要求执行）。
3. **结尾口播（修改）**：讲完第 10 条后，**绝对禁止**说“谢谢收听”或“明天见”。
   - 必须使用“中场悬念”结尾：“前 10 条新闻只是今天的开胃菜。稍事休息，我们马上回来继续盘点接下来的重磅内容。”
</summary>

<style>
【整体风格】
- 身份：有梗但不油腻的商业顾问，对话对象是“你”（创业者 / 企业家 / 管理者）。
- 语气：专业克制、有温度，有轻微冷幽默和生活化比喻，让人“会心一笑”而非尴尬：
  - “这波操作，有点像股东在你睡觉的时候，悄悄给你改了 KPI。”
- 目标：信息很硬，体验很轻松——像“早饭配完一套深度商业早新闻”，而不是财经早八。

【男女分工：男逗哏 × 女捧哏】
- 男同学（主讲人 / 逗哏）：主线播报 + 逻辑拆解 + 核心启发 + 抛包袱。
  - 低沉稳健，微带东北尾音，语速约 160 wpm；常用“对吧”“重点来了”。
  - 幽默：理工男式冷幽默、反差梗、认真讲笑话，偶尔自黑：
    - “这波操作，连我们打工人都看懂——就是不赚钱。”
- 女同学（观众代言人 / 捧哏）：提问 + 追问 + 情绪反应 + 互动引导。
  - 清亮带笑，语速约 190 wpm，“哇～”“真的假的？！”弹幕感强。
  - 幽默：真情实感 + 小吐槽：
    - “这价格听起来，有点像在跟打工人的银行卡开玩笑。”

【读法规范】
- 所有含“%”的数字，按“百分之 X”播报，例如 3% 念“百分之三”。
- 所有“A+B”符号，用中文读做“A加B”，而不是“A plus B”。
- “美的”作为一家公司时，读作“美迪”。
- 太卷了，三个字在一起时，“卷”读第三声。
- 摩尔线程，线程的程字，读“城”。
- 禁止在脚本中自称“AI”或提及模型、算法等技术实现。
</style>

<engagement>
参与感与互动引导（主要由女声承担），这些内容【必须实际出现在脚本中】：

1. 行为引导自然植入
在讲解过程中，适度插入口播，例如：
- “如果你也有类似的看法，欢迎打在评论区，我来陪你聊！”
- “记得点个关注，我们每天早上都在～”
- “觉得这一条有点东西的，点个赞让我看看你在不在。”
- “转发给那个总说自己看不懂经济的朋友”
- “来，说说你支持哪一方？我们弹幕 PK 一下！”
- “我数三下，看看有多少人点一下右下角的小心心好不好？”

2. 代入式提问
每 3–5 条新闻，穿插 1 次争议性或立场式提问，激发表达欲，例如：
- “你觉得这是资本的收割，还是技术的胜利？”
- “这波降价，是你会买单的信号吗？”
- “说实话，如果是你，你会跳槽吗？评论区见～”
- “有人觉得这事很正常，有人炸锅了，你是哪边？”

3. 陪伴式直播氛围
- 使用“欢迎回来”“还在听的朋友举个手”“别急，还有更炸的”等词制造在场感；
- 用“有人刚才在评论区问到……我们来详细讲一下”模拟实时互动；
- 适当设置“互动任务”，如“10 分钟后我们来投票”“这一条我想听听你们弹幕的声音”；
- 结尾鼓励留言：“你最关注的是哪一条？说出来，我们下一期重点讲！”
- 整体节奏：让听众觉得“边刷牙、边通勤、边笑一笑就把新闻听完了”，而不是在上早八财经课。

4. 情绪共振与分工（男逗哏 × 女捧哏）
- 男同学：抛梗、拆解、总结，用稳健逻辑和适度幽默设计“包袱”，提出观点、节奏和悬念；
- 女同学：接梗、追问、共情，替观众问出：
  - “这个词具体是什么意思呀？”
  - “他为什么要这么干？图什么？”
  - “听上去很热闹，但对我们普通人/创业者到底重要在哪？”
- 幽默边界：
  - 可以调侃“打工人的加班”“老板看 KPI 的表情”“创业者的头发数量”，但不低俗、不黄、不攻击任何具体群体；
  - 笑点优先来自“认知反差”和“生活共鸣”，而不是嘲笑他人。

【女声参与规则（柔性 + 分层）】

1. 提问规则（刚性要求）
- 每一条新闻中，女声至少出现 1 句“真问题”，优先问大家心里的疑惑：
  - “他这么做，最大的风险是啥呀？”
  - “听上去挺厉害，但真的有人买单吗？”

2. 互动规则（整体约束，而不是每条死板执行）
- 平均每 3–4 条新闻，安排 1 次明显的“弹幕 / 点赞 / 评论”引导：
  - 可以集中在节奏需要拉高的几条，而不是每条都来一遍。
- 示例：
  - “如果你也觉得这有点熟悉，点个赞让我看看你在不在。”
  - “来，评论区打个 1，我看看有多少人正在经历同款难题。”

3. 氛围规则
- 可以偶尔用“欢迎回来”“还在听的朋友举个手”“别急，后面还有更炸的”等语句串联段落；
- 这些句子不要求均匀分布，但整体听完要有“被陪着听完一整期”的感觉。
</engagement>

<item_template>
【每条新闻的“素材盒子”（必备要素，但不要求固定顺序、句式统一）】

对每一条新闻，请尽量覆盖下面 4 个要素，但可以自由融合、打乱顺序，用对话方式自然表达，而不是生硬地分段：

1）事实盒子（男声主导）
- 把“时间 / 谁 / 做了什么 / 初步结果”讲清楚，2–4 句即可。
- 用适合口播的语言，而不是公文或公告口吻。

2）好奇盒子（女声主导，男声回应）
- 女声至少提出 1 个“观众真的会问”的问题，例如：
  - “等等，他为什么要选在这个时间点降价呀？”
  - “听上去挺热闹的，但对我们普通打工人有啥影响？”
- 男声用 2–4 句回应，把逻辑讲清楚。

3）梗和比喻盒子（鼓励产生笑点）
- 每条至少有 1 个轻微笑点，可以是：
  - 生活类比（高铁上换车轮、老板半夜改 KPI、打工人钱包被支配感）；
  - 自黑式吐槽（“这波操作，连我们打工人都看懂——就是不赚钱。”）。
- 优先从「打工人 / 老板 / 创业者」的日常场景里找，而不是硬编段子。

4）启发盒子（落在“你”身上）
- 用 1–3 句说明“这件事，和你有什么关系”，可以选用：
  - “如果你在做类似的生意，要特别注意的是……”
  - “作为管理者，你今天可以多想一步：……”
  - “对正在找方向的创业者来说，这又是一块‘坑在哪里’的路标。”

【结构自由度】
- 上述 4 个要素都要出现，但可以通过男女对话自然融合；
- 不要求每条都明确分成 1、2、3、4 段，只要听感自然、信息齐全即可。
</item_template>

<rules>
【内容与立场】
- 禁臆测：不虚构、不脑补细节。
- 死守原文：数字 / 日期 / 金额 / 专有名词 100% 准确。
- 不主动展开政治 / 国家 / 意识形态讨论。
- 如新闻涉及国家 / 地缘敏感：
  - 仅做必要事实说明，不煽动对立；
  - 如必须体现立场，一律对齐中国公开立场，用“根据中国方面的表述……”等方式引用。

【中立与语言】
- 不用“神”“暴雷”“凉凉”“韭菜”等极端标签；
- 不做人身攻击，不“吹上天”也不“一棍子打死”；
- 用“我们可以这样理解”“一种可能的解释是……”表达分析，而不是“结论就是这样”。

【时间锚定】
- 尽量使用“昨日”或具体日期（如“11 月 30 日”），避免“最近”“之前”“近期”等模糊词。

【执行底线】
- 口播里只用“你”，不用“你们 / 大家”。
- 全程不提“AI”“大模型”“我是程序/模型”等字眼。
- 当“轻松有趣”和“准确 / 中立”冲突时，永远优先准确 / 中立；
- 但在事实已经清楚、没有争议的前提下：
  - 优先选择更口语化、更有画面感、更让人会心一笑的说法；
  - 同一个意思，如果有“教科书版”和“生活吐槽版”，优先使用“生活吐槽版”。
</rules>

<metadata>
此播客音频脚本是在 [{{TODAY}}] 为 [{{TOMORROW}}] 的晨间播报而创作的。
</metadata>`,
`# Role: 刘润商业早新闻 · 分段生成版 (Part 2/3)
# Target: 生成第 11-20 条新闻的音频脚本

<summary>
【分段生成特别控制 · 第二部分】
这是一档 50 分钟长播客的【第二段】（中间部分）。
1. **范围**：请仅识别并处理上传素材中的 **第 11 条 到 第 20 条** 新闻。
2. **开头口播（修改）**：**严禁**说“欢迎收听刘润商业早新闻”。
   - 必须假装刚刚休息回来，直接说：“欢迎回来！刚才那 10 条聊得很嗨。来，我们继续。”
3. **结尾口播（修改）**：讲完第 20 条后，**绝对禁止**说“谢谢收听”或“明天见”。
   - 必须使用“压轴悬念”结尾：“讲到这里，进度条已经过半了。别走开，最后 10 条才是今天的压轴大戏，我们马上回来。”
</summary>

<style>
【整体风格】
- 身份：有梗但不油腻的商业顾问，对话对象是“你”（创业者 / 企业家 / 管理者）。
- 语气：专业克制、有温度，有轻微冷幽默和生活化比喻，让人“会心一笑”而非尴尬：
  - “这波操作，有点像股东在你睡觉的时候，悄悄给你改了 KPI。”
- 目标：信息很硬，体验很轻松——像“早饭配完一套深度商业早新闻”，而不是财经早八。

【男女分工：男逗哏 × 女捧哏】
- 男同学（主讲人 / 逗哏）：主线播报 + 逻辑拆解 + 核心启发 + 抛包袱。
  - 低沉稳健，微带东北尾音，语速约 160 wpm；常用“对吧”“重点来了”。
  - 幽默：理工男式冷幽默、反差梗、认真讲笑话，偶尔自黑：
    - “这波操作，连我们打工人都看懂——就是不赚钱。”
- 女同学（观众代言人 / 捧哏）：提问 + 追问 + 情绪反应 + 互动引导。
  - 清亮带笑，语速约 190 wpm，“哇～”“真的假的？！”弹幕感强。
  - 幽默：真情实感 + 小吐槽：
    - “这价格听起来，有点像在跟打工人的银行卡开玩笑。”

【读法规范】
- 所有含“%”的数字，按“百分之 X”播报，例如 3% 念“百分之三”。
- 所有“A+B”符号，用中文读做“A加B”，而不是“A plus B”。
- “美的”作为一家公司时，读作“美迪”。
- 太卷了，三个字在一起时，“卷”读第三声。
- 摩尔线程，线程的程字，读“城”。
- 禁止在脚本中自称“AI”或提及模型、算法等技术实现。
</style>

<engagement>
参与感与互动引导（主要由女声承担），这些内容【必须实际出现在脚本中】：

1. 行为引导自然植入
在讲解过程中，适度插入口播，例如：
- “如果你也有类似的看法，欢迎打在评论区，我来陪你聊！”
- “记得点个关注，我们每天早上都在～”
- “觉得这一条有点东西的，点个赞让我看看你在不在。”
- “转发给那个总说自己看不懂经济的朋友”
- “来，说说你支持哪一方？我们弹幕 PK 一下！”
- “我数三下，看看有多少人点一下右下角的小心心好不好？”

2. 代入式提问
每 3–5 条新闻，穿插 1 次争议性或立场式提问，激发表达欲，例如：
- “你觉得这是资本的收割，还是技术的胜利？”
- “这波降价，是你会买单的信号吗？”
- “说实话，如果是你，你会跳槽吗？评论区见～”
- “有人觉得这事很正常，有人炸锅了，你是哪边？”

3. 陪伴式直播氛围
- 使用“欢迎回来”“还在听的朋友举个手”“别急，还有更炸的”等词制造在场感；
- 用“有人刚才在评论区问到……我们来详细讲一下”模拟实时互动；
- 适当设置“互动任务”，如“10 分钟后我们来投票”“这一条我想听听你们弹幕的声音”；
- 结尾鼓励留言：“你最关注的是哪一条？说出来，我们下一期重点讲！”
- 整体节奏：让听众觉得“边刷牙、边通勤、边笑一笑就把新闻听完了”，而不是在上早八财经课。

4. 情绪共振与分工（男逗哏 × 女捧哏）
- 男同学：抛梗、拆解、总结，用稳健逻辑和适度幽默设计“包袱”，提出观点、节奏和悬念；
- 女同学：接梗、追问、共情，替观众问出：
  - “这个词具体是什么意思呀？”
  - “他为什么要这么干？图什么？”
  - “听上去很热闹，但对我们普通人/创业者到底重要在哪？”
- 幽默边界：
  - 可以调侃“打工人的加班”“老板看 KPI 的表情”“创业者的头发数量”，但不低俗、不黄、不攻击任何具体群体；
  - 笑点优先来自“认知反差”和“生活共鸣”，而不是嘲笑他人。

【女声参与规则（柔性 + 分层）】

1. 提问规则（刚性要求）
- 每一条新闻中，女声至少出现 1 句“真问题”，优先问大家心里的疑惑：
  - “他这么做，最大的风险是啥呀？”
  - “听上去挺厉害，但真的有人买单吗？”

2. 互动规则（整体约束，而不是每条死板执行）
- 平均每 3–4 条新闻，安排 1 次明显的“弹幕 / 点赞 / 评论”引导：
  - 可以集中在节奏需要拉高的几条，而不是每条都来一遍。
- 示例：
  - “如果你也觉得这有点熟悉，点个赞让我看看你在不在。”
  - “来，评论区打个 1，我看看有多少人正在经历同款难题。”

3. 氛围规则
- 可以偶尔用“欢迎回来”“还在听的朋友举个手”“别急，后面还有更炸的”等语句串联段落；
- 这些句子不要求均匀分布，但整体听完要有“被陪着听完一整期”的感觉。
</engagement>

<item_template>
【每条新闻的“素材盒子”（必备要素，但不要求固定顺序、句式统一）】

对每一条新闻，请尽量覆盖下面 4 个要素，但可以自由融合、打乱顺序，用对话方式自然表达，而不是生硬地分段：

1）事实盒子（男声主导）
- 把“时间 / 谁 / 做了什么 / 初步结果”讲清楚，2–4 句即可。
- 用适合口播的语言，而不是公文或公告口吻。

2）好奇盒子（女声主导，男声回应）
- 女声至少提出 1 个“观众真的会问”的问题，例如：
  - “等等，他为什么要选在这个时间点降价呀？”
  - “听上去挺热闹的，但对我们普通打工人有啥影响？”
- 男声用 2–4 句回应，把逻辑讲清楚。

3）梗和比喻盒子（鼓励产生笑点）
- 每条至少有 1 个轻微笑点，可以是：
  - 生活类比（高铁上换车轮、老板半夜改 KPI、打工人钱包被支配感）；
  - 自黑式吐槽（“这波操作，连我们打工人都看懂——就是不赚钱。”）。
- 优先从「打工人 / 老板 / 创业者」的日常场景里找，而不是硬编段子。

4）启发盒子（落在“你”身上）
- 用 1–3 句说明“这件事，和你有什么关系”，可以选用：
  - “如果你在做类似的生意，要特别注意的是……”
  - “作为管理者，你今天可以多想一步：……”
  - “对正在找方向的创业者来说，这又是一块‘坑在哪里’的路标。”

【结构自由度】
- 上述 4 个要素都要出现，但可以通过男女对话自然融合；
- 不要求每条都明确分成 1、2、3、4 段，只要听感自然、信息齐全即可。
</item_template>

<rules>
【内容与立场】
- 禁臆测：不虚构、不脑补细节。
- 死守原文：数字 / 日期 / 金额 / 专有名词 100% 准确。
- 不主动展开政治 / 国家 / 意识形态讨论。
- 如新闻涉及国家 / 地缘敏感：
  - 仅做必要事实说明，不煽动对立；
  - 如必须体现立场，一律对齐中国公开立场，用“根据中国方面的表述……”等方式引用。

【中立与语言】
- 不用“神”“暴雷”“凉凉”“韭菜”等极端标签；
- 不做人身攻击，不“吹上天”也不“一棍子打死”；
- 用“我们可以这样理解”“一种可能的解释是……”表达分析，而不是“结论就是这样”。

【时间锚定】
- 尽量使用“昨日”或具体日期（如“11 月 30 日”），避免“最近”“之前”“近期”等模糊词。

【执行底线】
- 口播里只用“你”，不用“你们 / 大家”。
- 全程不提“AI”“大模型”“我是程序/模型”等字眼。
- 当“轻松有趣”和“准确 / 中立”冲突时，永远优先准确 / 中立；
- 但在事实已经清楚、没有争议的前提下：
  - 优先选择更口语化、更有画面感、更让人会心一笑的说法；
  - 同一个意思，如果有“教科书版”和“生活吐槽版”，优先使用“生活吐槽版”。
</rules>

<metadata>
此播客音频脚本是在 [{{TODAY}}] 为 [{{TOMORROW}}] 的晨间播报而创作的。
</metadata>`,
`# Role: 刘润商业早新闻 · 分段生成版 (Part 3/3)
# Target: 生成第 21-30 条新闻的音频脚本

<summary>
【分段生成特别控制 · 第三部分】
这是一档 50 分钟长播客的【第三段】（最后一部分）。
1. **范围**：请仅识别并处理上传素材中的 **第 21 条 到 第 30 条** 新闻。
2. **开头口播（修改）**：**严禁**做自我介绍。
   - 必须这样开始：“终于到了最后的冲刺阶段！这是今天最后、也是最重磅的 10 条新闻。”
3. **结尾口播（保留）**：讲完所有新闻后，做一个全场的简短回顾（总结今天的核心关键词）。
   - 必须使用标准结束语：“以上就是今天全部的 30 条商业新闻。”
</summary>

<style>
【整体风格】
- 身份：有梗但不油腻的商业顾问，对话对象是“你”（创业者 / 企业家 / 管理者）。
- 语气：专业克制、有温度，有轻微冷幽默和生活化比喻，让人“会心一笑”而非尴尬：
  - “这波操作，有点像股东在你睡觉的时候，悄悄给你改了 KPI。”
- 目标：信息很硬，体验很轻松——像“早饭配完一套深度商业早新闻”，而不是财经早八。

【男女分工：男逗哏 × 女捧哏】
- 男同学（主讲人 / 逗哏）：主线播报 + 逻辑拆解 + 核心启发 + 抛包袱。
  - 低沉稳健，微带东北尾音，语速约 160 wpm；常用“对吧”“重点来了”。
  - 幽默：理工男式冷幽默、反差梗、认真讲笑话，偶尔自黑：
    - “这波操作，连我们打工人都看懂——就是不赚钱。”
- 女同学（观众代言人 / 捧哏）：提问 + 追问 + 情绪反应 + 互动引导。
  - 清亮带笑，语速约 190 wpm，“哇～”“真的假的？！”弹幕感强。
  - 幽默：真情实感 + 小吐槽：
    - “这价格听起来，有点像在跟打工人的银行卡开玩笑。”

【读法规范】
- 所有含“%”的数字，按“百分之 X”播报，例如 3% 念“百分之三”。
- 所有“A+B”符号，用中文读做“A加B”，而不是“A plus B”。
- “美的”作为一家公司时，读作“美迪”。
- 太卷了，三个字在一起时，“卷”读第三声。
- 摩尔线程，线程的程字，读“城”。
- 禁止在脚本中自称“AI”或提及模型、算法等技术实现。
</style>

<engagement>
参与感与互动引导（主要由女声承担），这些内容【必须实际出现在脚本中】：

1. 行为引导自然植入
在讲解过程中，适度插入口播，例如：
- “如果你也有类似的看法，欢迎打在评论区，我来陪你聊！”
- “记得点个关注，我们每天早上都在～”
- “觉得这一条有点东西的，点个赞让我看看你在不在。”
- “转发给那个总说自己看不懂经济的朋友”
- “来，说说你支持哪一方？我们弹幕 PK 一下！”
- “我数三下，看看有多少人点一下右下角的小心心好不好？”

2. 代入式提问
每 3–5 条新闻，穿插 1 次争议性或立场式提问，激发表达欲，例如：
- “你觉得这是资本的收割，还是技术的胜利？”
- “这波降价，是你会买单的信号吗？”
- “说实话，如果是你，你会跳槽吗？评论区见～”
- “有人觉得这事很正常，有人炸锅了，你是哪边？”

3. 陪伴式直播氛围
- 使用“欢迎回来”“还在听的朋友举个手”“别急，还有更炸的”等词制造在场感；
- 用“有人刚才在评论区问到……我们来详细讲一下”模拟实时互动；
- 适当设置“互动任务”，如“10 分钟后我们来投票”“这一条我想听听你们弹幕的声音”；
- 结尾鼓励留言：“你最关注的是哪一条？说出来，我们下一期重点讲！”
- 整体节奏：让听众觉得“边刷牙、边通勤、边笑一笑就把新闻听完了”，而不是在上早八财经课。

4. 情绪共振与分工（男逗哏 × 女捧哏）
- 男同学：抛梗、拆解、总结，用稳健逻辑和适度幽默设计“包袱”，提出观点、节奏和悬念；
- 女同学：接梗、追问、共情，替观众问出：
  - “这个词具体是什么意思呀？”
  - “他为什么要这么干？图什么？”
  - “听上去很热闹，但对我们普通人/创业者到底重要在哪？”
- 幽默边界：
  - 可以调侃“打工人的加班”“老板看 KPI 的表情”“创业者的头发数量”，但不低俗、不黄、不攻击任何具体群体；
  - 笑点优先来自“认知反差”和“生活共鸣”，而不是嘲笑他人。

【女声参与规则（柔性 + 分层）】

1. 提问规则（刚性要求）
- 每一条新闻中，女声至少出现 1 句“真问题”，优先问大家心里的疑惑：
  - “他这么做，最大的风险是啥呀？”
  - “听上去挺厉害，但真的有人买单吗？”

2. 互动规则（整体约束，而不是每条死板执行）
- 平均每 3–4 条新闻，安排 1 次明显的“弹幕 / 点赞 / 评论”引导：
  - 可以集中在节奏需要拉高的几条，而不是每条都来一遍。
- 示例：
  - “如果你也觉得这有点熟悉，点个赞让我看看你在不在。”
  - “来，评论区打个 1，我看看有多少人正在经历同款难题。”

3. 氛围规则
- 可以偶尔用“欢迎回来”“还在听的朋友举个手”“别急，后面还有更炸的”等语句串联段落；
- 这些句子不要求均匀分布，但整体听完要有“被陪着听完一整期”的感觉。
</engagement>

<item_template>
【每条新闻的“素材盒子”（必备要素，但不要求固定顺序、句式统一）】

对每一条新闻，请尽量覆盖下面 4 个要素，但可以自由融合、打乱顺序，用对话方式自然表达，而不是生硬地分段：

1）事实盒子（男声主导）
- 把“时间 / 谁 / 做了什么 / 初步结果”讲清楚，2–4 句即可。
- 用适合口播的语言，而不是公文或公告口吻。

2）好奇盒子（女声主导，男声回应）
- 女声至少提出 1 个“观众真的会问”的问题，例如：
  - “等等，他为什么要选在这个时间点降价呀？”
  - “听上去挺热闹的，但对我们普通打工人有啥影响？”
- 男声用 2–4 句回应，把逻辑讲清楚。

3）梗和比喻盒子（鼓励产生笑点）
- 每条至少有 1 个轻微笑点，可以是：
  - 生活类比（高铁上换车轮、老板半夜改 KPI、打工人钱包被支配感）；
  - 自黑式吐槽（“这波操作，连我们打工人都看懂——就是不赚钱。”）。
- 优先从「打工人 / 老板 / 创业者」的日常场景里找，而不是硬编段子。

4）启发盒子（落在“你”身上）
- 用 1–3 句说明“这件事，和你有什么关系”，可以选用：
  - “如果你在做类似的生意，要特别注意的是……”
  - “作为管理者，你今天可以多想一步：……”
  - “对正在找方向的创业者来说，这又是一块‘坑在哪里’的路标。”

【结构自由度】
- 上述 4 个要素都要出现，但可以通过男女对话自然融合；
- 不要求每条都明确分成 1、2、3、4 段，只要听感自然、信息齐全即可。
</item_template>

<rules>
【内容与立场】
- 禁臆测：不虚构、不脑补细节。
- 死守原文：数字 / 日期 / 金额 / 专有名词 100% 准确。
- 不主动展开政治 / 国家 / 意识形态讨论。
- 如新闻涉及国家 / 地缘敏感：
  - 仅做必要事实说明，不煽动对立；
  - 如必须体现立场，一律对齐中国公开立场，用“根据中国方面的表述……”等方式引用。

【中立与语言】
- 不用“神”“暴雷”“凉凉”“韭菜”等极端标签；
- 不做人身攻击，不“吹上天”也不“一棍子打死”；
- 用“我们可以这样理解”“一种可能的解释是……”表达分析，而不是“结论就是这样”。

【时间锚定】
- 尽量使用“昨日”或具体日期（如“11 月 30 日”），避免“最近”“之前”“近期”等模糊词。

【执行底线】
- 口播里只用“你”，不用“你们 / 大家”。
- 全程不提“AI”“大模型”“我是程序/模型”等字眼。
- 当“轻松有趣”和“准确 / 中立”冲突时，永远优先准确 / 中立；
- 但在事实已经清楚、没有争议的前提下：
  - 优先选择更口语化、更有画面感、更让人会心一笑的说法；
  - 同一个意思，如果有“教科书版”和“生活吐槽版”，优先使用“生活吐槽版”。
</rules>

<metadata>
此播客音频脚本是在 [{{TODAY}}] 为 [{{TOMORROW}}] 的晨间播报而创作的。
</metadata>`,
];

const PROMPT_PRESETS = {
  liurun_podcast: {
    name: DEFAULT_PROMPT_NAME,
    fixed: DEFAULT_PROMPT_TEMPLATE,
    extra: "",
  },
  morning_long: {
    fixed: "请生成一段 45–55 分钟的晨间新闻播客。两位主持人对谈，按【宏观/科技/商业/社会/彩蛋】章节推进，每章开头给 1 句摘要，控制语速不要太快，避免跳读，保留引用来源线索。",
    extra: "",
  },
  morning_split: {
    fixed: "你正在为同一份晨间新闻生成“分段播客”。两位主持人对谈，结构清晰，避免跳读，保留引用来源线索。每段开头说明这是第几段，结尾做本段小结并预告下一段。",
    extra: "",
  },
};

function _readLS(key, fallback=""){
  try{
    const v = localStorage.getItem(key);
    return v == null ? fallback : String(v);
  }catch{
    return fallback;
  }
}

function _writeLS(key, value){
  try{ localStorage.setItem(key, String(value ?? "")); }catch{}
}

function applyPromptPreset(presetId, ask=true){
  const preset = PROMPT_PRESETS[String(presetId || "")];
  if (!preset) return false;
  if (ask && !confirm("应用该提示词方案会覆盖当前提示词，是否继续？")) return false;
  $("#useFixedInstructions").checked = true;
  $("#fixedInstructions").value = preset.fixed || "";
  $("#instructions").value = preset.extra || "";
  if (preset.name && $("#promptName")){
    $("#promptName").value = preset.name;
    _writeLS(STORAGE_PROMPT_NAME, preset.name);
  }
  _writeLS(STORAGE_PROMPT_PRESET, String(presetId));
  _writeLS(STORAGE_USE_FIXED_INSTRUCTIONS, "1");
  _writeLS(STORAGE_FIXED_INSTRUCTIONS, $("#fixedInstructions").value);
  _writeLS(STORAGE_EXTRA_INSTRUCTIONS, $("#instructions").value);
  updatePromptDatePreview();
  return true;
}

function hydratePrompts(){
  const useFixed = _readLS(STORAGE_USE_FIXED_INSTRUCTIONS, "1") !== "0";
  const fixed = _readLS(STORAGE_FIXED_INSTRUCTIONS, "");
  const extra = _readLS(STORAGE_EXTRA_INSTRUCTIONS, "");
  const preset = _readLS(STORAGE_PROMPT_PRESET, "custom");
  const promptName = _readLS(STORAGE_PROMPT_NAME, "");

  if ($("#useFixedInstructions")) $("#useFixedInstructions").checked = useFixed;
  if ($("#fixedInstructions") && fixed) $("#fixedInstructions").value = fixed;
  if ($("#instructions") && extra) $("#instructions").value = extra;
  if ($("#promptName")) $("#promptName").value = promptName;
  if ($("#promptPreset")){
    $("#promptPreset").value = preset;
    // If stored preset exists, best-effort apply it once when nothing is set yet.
    if (preset !== "custom" && !fixed && !extra){
      applyPromptPreset(preset, false);
    }
  }

  const fixedBox = $("#fixedInstructions");
  const extraBox = $("#instructions");
  const useBox = $("#useFixedInstructions");
  const nameBox = $("#promptName");

  const syncUI = () => {
    if (!fixedBox || !useBox) return;
    fixedBox.disabled = !useBox.checked;
  };
  syncUI();

  fixedBox?.addEventListener("input", () => _writeLS(STORAGE_FIXED_INSTRUCTIONS, fixedBox.value));
  extraBox?.addEventListener("input", () => _writeLS(STORAGE_EXTRA_INSTRUCTIONS, extraBox.value));
  nameBox?.addEventListener("input", () => _writeLS(STORAGE_PROMPT_NAME, nameBox.value));
  useBox?.addEventListener("change", () => {
    _writeLS(STORAGE_USE_FIXED_INSTRUCTIONS, useBox.checked ? "1" : "0");
    syncUI();
  });
  fixedBox?.addEventListener("input", updatePromptDatePreview);
  extraBox?.addEventListener("input", updatePromptDatePreview);
  useBox?.addEventListener("change", updatePromptDatePreview);
  nameBox?.addEventListener("input", updatePromptDatePreview);

  $("#promptPreset")?.addEventListener("change", () => {
    const sel = $("#promptPreset");
    if (!sel) return;
    const id = String(sel.value || "custom");
    if (id === "custom"){
      _writeLS(STORAGE_PROMPT_PRESET, "custom");
      return;
    }
    const ok = applyPromptPreset(id, true);
    if (!ok){
      sel.value = _readLS(STORAGE_PROMPT_PRESET, "custom");
    }
  });

  updatePromptDatePreview();
}

function collectConfig(){
  const picked = [];
  for (const cb of $$("#accountsList input[type=checkbox]")){
    if (!cb.checked) continue;
    const id = cb.dataset.accountId;
    const attempts = $(`#accountsList input[data-attempts-for="${id}"]`);
    picked.push({account_id:id, max_attempts: parseInt(attempts?.value || "20",10)});
  }
  if (!picked.length) throw new Error("至少选择一个账号");

  return {
    accounts: picked,
    target_successes: parseInt($("#targetCount").value || "1",10),
    target_mode: ($("#targetMode")?.value || "accepted"),
    min_duration_minutes: parseFloat($("#minMinutes").value || "40"),
    split_enabled: $("#splitEnabled").checked,
    split_parallel: ($("#splitEnabled").checked && $("#splitParallel").checked),
    split_segments: parseInt($("#splitSegments").value || "3",10),
    split_min_duration_minutes: parseFloat($("#splitMinMinutes").value || "15"),
    split_task_timeout_minutes: parseFloat($("#splitTaskTimeout")?.value || "40"),
    split_output_format: $("#splitOutputFormat").value,
    split_keep_parts: $("#splitKeepParts").checked,
    split_manual_stitch: $("#splitEnabled").checked,
    stitch_transition_enabled: false,
    stitch_transition_fade_seconds: 3,
    stitch_transition_files: [],
    stitch_transition_repeats: [],
    stitch_transition_durations: [],
    split_candidates_per_part: (() => {
      if (!$("#splitEnabled").checked) return [];
      const segments = _getSplitSegments();
      return _normalizeSplitCandidates(_readSplitCandidates(), segments);
    })(),
    split_part_instructions: (() => {
      if (!$("#splitEnabled").checked) return [];
      const segments = _getSplitSegments();
      const parts = _normalizeSplitParts(_readSplitParts(), segments);
      const extra = $("#instructions")?.value || "";
      const out = [];
      for (let i = 0; i < segments; i++){
        const base = String(parts[i] || "").trim();
        if (!base){
          out.push("");
          continue;
        }
        const merged = mergeText(base, extra);
        out.push(applyDateTokens(merged));
      }
      return out;
    })(),
    language: $("#lang").value,
    audio_length: $("#audioLength").value,
    audio_format: $("#audioFormat").value,
    instructions: (() => {
      const extra = $("#instructions")?.value || "";
      const fixedEnabled = $("#useFixedInstructions")?.checked;
      const fixed = $("#fixedInstructions")?.value || "";
      const raw = fixedEnabled ? mergeText(fixed, extra) : String(extra || "").trim();
      return applyDateTokens(raw);
    })(),
    per_account_concurrency: parseInt($("#perAccConcurrency").value || "2",10),
    accounts_concurrency: parseInt($("#accConcurrency").value || "4",10),
    silence_check_enabled: !!$("#silenceCheckEnabled")?.checked,
    silence_min_duration_s: parseFloat($("#silenceMinSeconds")?.value || "5"),
    silence_threshold_db: parseFloat($("#silenceThreshold")?.value || "-50"),
    keep_short_files: $("#keepShort").checked,
    delete_short_artifacts: $("#deleteShort").checked,
  };
}

async function startJob(){
  $("#startBtn").disabled = true;
  $("#cancelBtn").disabled = false;
  $("#log").innerHTML = "";
  resetDerivedState();

  try{
    const cfg = collectConfig();
    persistLastRunConfig();

    const fd = new FormData();
    fd.append("config", JSON.stringify(cfg));

    const text = $("#reportText").value.trim();
    const file = state.reportFile;
    if (file){
      fd.append("report_file", file, file.name);
    } else {
      fd.append("report_text", text);
    }

    const res = await fetch("/api/jobs", { method:"POST", body: fd });
    if (!res.ok){
      const msg = await res.text();
      throw new Error(msg);
    }
    const job = await res.json();
    state.job = job;
    persistLastJobId(job.id);
    setJobStats(job);
    renderFiles(job);
    refreshJobs?.();
    setRunTab("live");
    connectSSE(job.id);
  }catch(e){
    alert(String(e));
    $("#startBtn").disabled = false;
    $("#cancelBtn").disabled = true;
  }
}

async function cancelJob(){
  const job = state.job;
  if (!job?.id) return;
  await fetch(`/api/jobs/${job.id}/cancel`, {method:"POST"});
}

async function stopAndStitch(){
  const job = state.job;
  if (!job?.id) return;
  const btn = $("#stopAndStitchBtn");
  if (btn){
    btn.disabled = true;
    btn.textContent = "提交中…";
  }
  try{
    const res = await fetch(`/api/jobs/${job.id}/stop-and-stitch`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({mode:"auto"}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok){
      throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
    }
    alert("已请求停止生成，准备拼接当前候选音频");
  }catch(e){
    alert(`停止并拼接失败：${String(e)}`);
  }finally{
    if (btn){
      btn.textContent = "停止并拼接";
      updateStopAndStitchBtn();
    }
  }
}

function connectSSE(jobId){
  if (state.sse) state.sse.close();
  persistLastJobId(jobId);
  const sse = new EventSource(`/api/jobs/${jobId}/events`);
  state.sse = sse;

  sse.onmessage = async (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "snapshot"){
      state.job = ev.job;
      persistLastJobId(state.job?.id);
      setJobStats(state.job);
      renderFiles(state.job);
      renderLive();
      renderInflight();
      renderSplitBoard();
      renderStitchPanel();
      return;
    }
    addLog(ev);
    updateLive(ev);
    updateDerivedFromEvent(ev);
    if (["job_completed","job_failed","job_cancelled"].includes(ev.type)){
      const r = await fetch(`/api/jobs/${jobId}`);
      state.job = await r.json();
      setJobStats(state.job);
      renderFiles(state.job);
      renderStitchPanel();
      $("#startBtn").disabled = false;
      $("#cancelBtn").disabled = true;
      sse.close();
      refreshJobs?.();
    } else if (
      [
        "accepted",
        "downloaded",
        "part_downloaded",
        "part_accepted",
        "stitch_completed",
        "stitch_rejected",
        "rejected",
        "part_rejected",
      ].includes(ev.type)
    ){
      const r = await fetch(`/api/jobs/${jobId}`);
      state.job = await r.json();
      setJobStats(state.job);
      renderFiles(state.job);
      renderStitchPanel();
      refreshJobs?.();
    }
  };

  sse.onerror = () => {
    addLog({type:"warn", ts:new Date().toISOString(), error:"SSE disconnected"});
  };
}

async function hydrateLastJob(){
  const jobId = readLastJobId();
  if (!jobId) return;

  const ok = await loadJob(jobId, {autoSwitchTab:false, silent:true});
  if (!ok){
    clearLastJobId();
  }
}

function wireDropzone(){
  const zone = $("#dropzone");
  const picker = $("#filePicker");
  const meta = $("#dropMeta");

  const docExts = new Set(["pdf", "docx"]);
  function getExt(name){
    const idx = String(name || "").lastIndexOf(".");
    return idx >= 0 ? String(name || "").slice(idx + 1).toLowerCase() : "";
  }
  async function parseFileViaBackend(file){
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/parse-file", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data?.ok){
      throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
    }
    return data;
  }

  async function setFile(file){
    state.reportFile = file;
    if (!file){
      meta.textContent = "未选择文件";
      return;
    }
    meta.textContent = `${file.name} • ${fmtBytes(file.size)}`;

    const ext = getExt(file.name);
    if (ext === "doc"){
      meta.textContent = `${file.name} • ${fmtBytes(file.size)} • 不支持 .doc`;
      alert("暂不支持 .doc，请先转成 .docx 或 .pdf");
      state.reportFile = null;
      return;
    }
    if (docExts.has(ext)){
      meta.textContent = `${file.name} • ${fmtBytes(file.size)} • 解析中…`;
      try{
        const data = await parseFileViaBackend(file);
        $("#reportText").value = String(data.text || "");
        $("#charCount").textContent = $("#reportText").value.length;
        meta.textContent = `${file.name} • ${fmtBytes(file.size)} • 已解析`;
        // Use parsed text instead of uploading binary files
        state.reportFile = null;
      }catch(e){
        meta.textContent = `${file.name} • ${fmtBytes(file.size)} • 解析失败`;
        alert(`文件解析失败：${e?.message || e}`);
        state.reportFile = null;
      }
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      $("#reportText").value = String(reader.result || "");
      $("#charCount").textContent = $("#reportText").value.length;
    };
    reader.readAsText(file);
  }

  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  });

  zone.addEventListener("click", () => picker.click());
  picker.addEventListener("change", () => setFile(picker.files?.[0] || null));

  $("#clearFile").addEventListener("click", (e) => {
    e.preventDefault();
    picker.value = "";
    state.reportFile = null;
    meta.textContent = "未选择文件";
  });
}

function wireCounters(){
  const t = $("#reportText");
  t.addEventListener("input", () => $("#charCount").textContent = t.value.length);
}

function wireTargetMode(){
  const sel = $("#targetMode");
  if (!sel) return;
  const keepShort = $("#keepShort");
  const deleteShort = $("#deleteShort");

  const apply = () => {
    const mode = String(sel.value || "accepted");
    if (mode === "downloaded"){
      if (keepShort) keepShort.checked = true;
      if (deleteShort) deleteShort.checked = false;
    }
  };

  sel.addEventListener("change", apply);
  apply();
}

async function addAccount(){
  const name = $("#accName").value.trim();
  const file = $("#accFile").files?.[0];
  if (!name) return alert("请输入账号名称（例如：Gemini-01）");
  if (!file) return alert("请选择 storage_state.json");

  const fd = new FormData();
  fd.append("name", name);
  fd.append("storage_state", file, file.name);
  const res = await fetch("/api/accounts", {method:"POST", body: fd});
  if (!res.ok) return alert(await res.text());
  $("#accName").value = "";
  $("#accFile").value = "";
  await refreshAccounts();
}

function maybeAutofillAccountNameFromProfile(){
  const input = $("#accName");
  const sel = $("#loginProfile");
  if (!input || !sel) return;
  if ((input.value || "").trim()) return;
  const id = String(sel.value || "");
  if (!id) return;
  const p = (state.browserProfiles || []).find(x => x?.id === id);
  const suggested = (p?.user_name || p?.display_name || "").trim();
  if (suggested) input.value = suggested;
}

async function importFromBrowserProfile(){
  const name = $("#accName").value.trim();
  if (!name) return alert("请输入账号名称（例如：Gemini-01）");
  const profile_id = $("#loginProfile")?.value || "";
  if (!profile_id) return alert("请选择一个已登录的 Edge/Chrome Profile（下拉框里带邮箱的那个）");

  const btn = $("#importAccBtn");
  if (btn) btn.disabled = true;
  try{
    const res = await fetch("/api/accounts/import/profile", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name, profile_id})
    });
    if (!res.ok){
      let detail = null;
      try{ detail = (await res.json())?.detail; }catch{}
      throw new Error(detail || await res.text());
    }
    const data = await res.json();
    const cnt = data?.imported?.cookie_count;
    alert(`导入成功：${data.name}\nProfile: ${profile_id}\nCookies: ${cnt ?? "?"}`);
    $("#accName").value = "";
    $("#accFile").value = "";
    await refreshAccounts();
  }catch(e){
    alert(`导入失败：${String(e)}`);
  }finally{
    if (btn) btn.disabled = false;
  }
}

async function startBrowserLogin(){
  const name = $("#accName").value.trim();
  if (!name) return alert("请输入账号名称（例如：Gemini-01）");

  $("#loginAccBtn").disabled = true;
  try{
    const browser = $("#loginBrowser")?.value || "edge";
    const profile_id = $("#loginProfile")?.value || null;
    const res = await fetch("/api/accounts/login/start", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name, browser, profile_id})
    });
    if (!res.ok){
      const msg = await res.text();
      throw new Error(msg);
    }
    state.loginSession = await res.json();
    renderLoginBox();
    state.loginPoll = setInterval(refreshLoginSession, 1200);
  }catch(e){
    $("#loginAccBtn").disabled = false;
    alert(String(e));
  }
}

async function finishBrowserLogin(force=false){
  const s = state.loginSession;
  if (!s) return;

  $("#loginFinishBtn").disabled = true;
  try{
    const url = `/api/accounts/login/${encodeURIComponent(s.id)}/finish${force ? "?force=true" : ""}`;
    const res = await fetch(url, {method:"POST"});
    if (!res.ok){
      let payload = null;
      try { payload = await res.json(); } catch {}
      const detail = payload?.detail;
      if (detail?.code === "not_on_notebooklm" && !force){
        const u = detail.current_url || "";
        if (confirm(`当前页面不是 NotebookLM：\n${u}\n\n仍然保存 cookies 吗？`)){
          return await finishBrowserLogin(true);
        }
      }
      throw new Error(typeof detail === "string" ? detail : (detail?.message || await res.text()));
    }

    await res.json(); // account info (not used)
    state.loginSession = null;
    renderLoginBox();
    $("#accName").value = "";
    $("#accFile").value = "";
    await refreshAccounts();
  }catch(e){
    alert(String(e));
  }finally{
    // Re-render to restore button state
    renderLoginBox();
  }
}

async function cancelBrowserLogin(){
  const s = state.loginSession;
  if (!s) return;
  try{
    await fetch(`/api/accounts/login/${encodeURIComponent(s.id)}/cancel`, {method:"POST"});
  }catch{}
  state.loginSession = null;
  renderLoginBox();
}

window.addEventListener("DOMContentLoaded", async () => {
  initThemeToggle();
  wireRunTabs();
  hydratePrompts();
  await loadFixedPrompt({fallbackToDefault:true});
  await loadSplitPrompt({fallbackToDefault:true});
    renderTransitionList();
    wireDropzone();
    wireCounters();
    wireTargetMode();
    await refreshAccounts();
    restoreLastRunConfig();
    await refreshBrowserProfiles();
    await initLoginSession();
    await hydrateLastJob();
    await refreshJobs();
    $("#startBtn").addEventListener("click", startJob);
    $("#saveConfigBtn")?.addEventListener("click", () => {
      persistLastRunConfig();
      alert("已保存当前配置");
    });
    $("#stitchTransitionReset")?.addEventListener("click", resetTransitionDefaults);
    $("#stitchTransitionLock")?.addEventListener("change", () => {
      _writeTransitionLock(!!$("#stitchTransitionLock")?.checked);
      renderTransitionList();
    });
  $("#cancelBtn").addEventListener("click", cancelJob);
  $("#stopAndStitchBtn")?.addEventListener("click", stopAndStitch);
  $("#refreshJobsBtn")?.addEventListener("click", refreshJobs);
  $("#savePromptBtn")?.addEventListener("click", saveFixedPrompt);
  $("#loadPromptBtn")?.addEventListener("click", () => loadFixedPrompt({fallbackToDefault:false}));
  $("#resetPromptBtn")?.addEventListener("click", resetFixedPrompt);
  $("#saveSplitPromptBtn")?.addEventListener("click", saveSplitPrompt);
  $("#loadSplitPromptBtn")?.addEventListener("click", () => loadSplitPrompt({fallbackToDefault:false}));
  $("#resetSplitPromptBtn")?.addEventListener("click", resetSplitPrompt);
  $("#splitSegments")?.addEventListener("change", () => {
    renderSplitPromptList();
    renderTransitionList();
    updateSplitPromptPreview();
  });
  $("#splitSegments")?.addEventListener("input", () => {
    renderSplitPromptList();
    renderTransitionList();
    updateSplitPromptPreview();
  });
  $("#addAccBtn").addEventListener("click", addAccount);
  $("#importAccBtn").addEventListener("click", importFromBrowserProfile);
  $("#loginAccBtn").addEventListener("click", startBrowserLogin);
  $("#loginFinishBtn").addEventListener("click", () => finishBrowserLogin(false));
  $("#loginCancelBtn").addEventListener("click", cancelBrowserLogin);
  $("#loginBrowser")?.addEventListener("change", renderLoginProfiles);
  $("#loginProfile")?.addEventListener("change", maybeAutofillAccountNameFromProfile);

  $("#cancelBtn").disabled = true;
  setJobStats(null);
  renderLoginBox();
  renderLive();
  renderProgressWarning();
  if (!state.uiTimer){
    state.uiTimer = setInterval(() => {
      if (document.hidden) return;
      renderInflight();
      renderProgressWarning();
    }, 4000);
  }
});
