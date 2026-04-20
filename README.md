# 晨间播客工厂（NotebookLM 多账号生成器）

一个本地网页工具：把你的「早间新闻报告」粘贴/拖拽进来，选择多个 Google 账号（各自的 `storage_state.json`），自动循环生成 NotebookLM Audio Overview（播客），**自动下载并检测时长**，满足阈值（默认 `>=40min`）就保留并停止/继续直到达到目标条数。

> 注意：底层使用 `notebooklm-py`（非官方、基于未公开接口）。Google 可能随时改动导致不可用；大量/高并发调用可能触发限流或配额。

## 你能做什么

- 网页粘贴或拖拽 `.txt/.md/.pdf/.docx` 报告（`.doc` 请先转为 `.docx`）
- 上传/管理多个账号的 `storage_state.json`
- 可选「分段拼接」：按条目拆成多段（例如 3×15min）生成后自动拼接
- 为每个账号设置最大尝试次数（比如 20 次）
- 设置：目标音频条数、语言、长度（LONG/DEFAULT/SHORT）、风格、提示词
- 后台自动轮询生成 → 下载 → 读取音频时长 → 达标即保留
- 页面实时显示日志与下载列表
- 分段音频可插入「过渡音频」（可设置淡入淡出、重复次数、时长）
- 内置独立的拼接页面 `/concat`，支持片头/片尾/片尾曲拼接
- 内置独立的报告解说页面 `/report-explain`：支持多 PDF 并行处理、可选仅输出排版文字，调用 `codex exec --full-auto -c model_reasoning_effort="xhigh" -m gpt-5.4`
- 主页面「一键导入拼接」：把生成的音频直接送到拼接页并自动开始

## 安装 & 运行（Windows / PowerShell）

在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开：`http://127.0.0.1:8000`

也可以直接双击运行：`启动-晨间播客工厂.bat`

如果 8000 端口被占用/被系统保留，可指定端口：`启动-晨间播客工厂.bat 8001`

> Windows 注意：`uvicorn --reload` 会切换事件循环策略，可能导致 Playwright 无法启动（浏览器登录添加会报 `NotImplementedError`）。建议默认不启用 reload；需要开发热重载时再用 `启动-晨间播客工厂.bat 8000 reload`。

## 准备多个账号的 storage_state.json

你可以在网页「账号池」里直接用 **“浏览器登录添加”** 自动生成 `storage_state.json`（会弹出 Chromium 窗口），也可以用 CLI 手动生成后上传。

方式 A（推荐）：用 `notebooklm-py` 的 CLI 登录一次导出

```powershell
.\.venv\Scripts\pip install "notebooklm-py[browser]==0.2.1"
.\.venv\Scripts\playwright install chromium

# 每个账号执行一次，并指定不同的输出路径
.\.venv\Scripts\notebooklm --storage C:\path\to\acc1.json login
```

然后在网页「账号池」里上传对应 JSON。

## 产物与存储位置

- 账号：`data/accounts/<account_id>/storage_state.json`
- 任务输出：`data/jobs/<job_id>/outputs/*.mp4`

## 文件命名规则（新版）

所有**新生成/新拼接**的音频文件名统一为可读格式，方便在另一台电脑也能一眼看懂。  
日期默认取“**明天**”（`YYYYMMDD`），时长以 `min` 表示（取整）。

示例：

- 分段候选：`20260202_第1段_候选01_18min_账号名.mp4`
- 分段候选（静音不合格）：`20260202_第1段_候选01_18min_账号名_静音不合格.mp4`
- 整段候选：`20260202_完整_候选01_42min_账号名.mp4`
- 拼接成片：`20260202_成片_45min_账号名.m4a`  
  - 若有多次拼接，会加“第2版/第3版”等前缀：  
    `20260202_第2版_成片_45min_账号名.m4a`

## 时长检测

默认用 `mutagen` 读取音频时长；如果你的音频格式导致识别失败，可以安装 FFmpeg（确保 `ffprobe` 在 PATH 里），工具会自动回退到 `ffprobe`。

## 分段拼接

开启「分段拼接」后，服务会用 `ffmpeg` 把多段音频拼成一个文件（因此需要 `ffmpeg` 在 PATH 里）。

## 拼接页面 / 过渡音频

- 拼接页地址：`http://127.0.0.1:8000/concat`
- 支持片头 / 片尾 / 片尾音乐拼接，可设置重复次数与质量
- 主页面的「一键导入拼接」会自动把音频填入拼接页并开始处理
- 分段拼接的“过渡音频”支持：
  - 自定义路径或拖拽上传
  - 设置“重复次数”“目标时长（秒）”“淡入淡出时长”
  - 目标时长超过原音频会自动循环

## 报告解说页面

- 页面地址：`http://127.0.0.1:8000/report-explain`
- 当前支持拖拽上传一个或多个 PDF，默认读取根目录 `报告解说提示词.txt`
- 可切换输出模式：`Markdown + PDF` 或 `仅文字排版`
- 批量模式下会自动使用短文件名（如 `前缀-1`、`前缀-2`），后端也会自动截断超长输出名，避免 Windows 路径过长报错
- 后台会调用 `codex exec --full-auto -c model_reasoning_effort="xhigh" -m gpt-5.4` 读取解析后的报告正文与提示词，生成 Markdown；开启 PDF 时再自动排版导出
- 产物默认输出到：`data/report_explain_output/`

## 另一台电脑部署 & 更新

### 首次部署

```powershell
git clone git@github.com:cihebi2/notebooklm_tool.git notebooklm_tool
cd notebooklm_tool
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开：`http://127.0.0.1:8000`

### 更新到最新版本

```powershell
cd notebooklm_tool
git pull
.\.venv\Scripts\pip install -r requirements.txt
```

如有运行中的服务，请先停止再启动（`启动-晨间播客工厂.bat` 或上面 uvicorn 命令）。

### 迁移已有账号/提示词/自定义音频

如果你在旧机器上已有账号与自定义音频，请拷贝以下目录到新机器同路径：

- 账号与任务数据：`data/`
- 过渡音频上传：`data/transitions/`
- 拼接固定音频（片头/片尾/片尾曲）：`assets/concat_fixed/`

> 提示：网页上的“上次运行配置”存于浏览器本地缓存（localStorage），不会自动跨机器同步。
