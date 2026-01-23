# 晨间播客工厂（NotebookLM 多账号生成器）

一个本地网页工具：把你的「早间新闻报告」粘贴/拖拽进来，选择多个 Google 账号（各自的 `storage_state.json`），自动循环生成 NotebookLM Audio Overview（播客），**自动下载并检测时长**，满足阈值（默认 `>=40min`）就保留并停止/继续直到达到目标条数。

> 注意：底层使用 `notebooklm-py`（非官方、基于未公开接口）。Google 可能随时改动导致不可用；大量/高并发调用可能触发限流或配额。

## 你能做什么

- 网页粘贴或拖拽 `.txt/.md` 报告
- 上传/管理多个账号的 `storage_state.json`
- 可选「分段拼接」：按条目拆成多段（例如 3×15min）生成后自动拼接
- 为每个账号设置最大尝试次数（比如 20 次）
- 设置：目标音频条数、语言、长度（LONG/DEFAULT/SHORT）、风格、提示词
- 后台自动轮询生成 → 下载 → 读取音频时长 → 达标即保留
- 页面实时显示日志与下载列表

## 安装 & 运行（Windows / PowerShell）

在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开：`http://127.0.0.1:8000`

也可以直接双击运行：`run.bat`

如果 8000 端口被占用/被系统保留，可指定端口：`run.bat 8001`

> Windows 注意：`uvicorn --reload` 会切换事件循环策略，可能导致 Playwright 无法启动（浏览器登录添加会报 `NotImplementedError`）。建议默认不启用 reload；需要开发热重载时再用 `run.bat 8000 reload`。

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

## 时长检测

默认用 `mutagen` 读取音频时长；如果你的音频格式导致识别失败，可以安装 FFmpeg（确保 `ffprobe` 在 PATH 里），工具会自动回退到 `ffprobe`。

## 分段拼接

开启「分段拼接」后，服务会用 `ffmpeg` 把多段音频拼成一个文件（因此需要 `ffmpeg` 在 PATH 里）。
