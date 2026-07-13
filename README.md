# AutoMail

基于 PySide6 的 macOS 桌面应用，支持按天定时自动发送邮件。

## 功能

- SMTP 邮件配置（支持 SSL / STARTTLS）
- 自定义收件人、抄送、主题、正文（支持 HTML）
- **每天重复发送 N 次**，可设置**发送间隔**（分钟）
- **最晚发送时刻**约束：当天最后一封邮件不能超过设定时间（默认 23:59，即不超过凌晨）
- 发送计划实时预览与校验
- 测试邮件、配置自动保存（`~/.automail/config.json`）

## 环境要求

- Python 3.10+
- macOS（PySide6 亦可在 Windows / Linux 运行）

## 安装

```bash
cd automail
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 发送计划说明

假设配置：

| 参数 | 值 |
|------|-----|
| 每天发送次数 | 5 |
| 发送间隔 | 30 分钟 |
| 最晚发送时刻 | 23:59 |
| 每日开始时刻 | 09:00 |

则当天发送时间为：09:00、09:30、10:00、10:30、11:00。末封 11:00，满足不超过 23:59。

若每天 5 封、间隔 30 分钟、从 22:00 开始，末封为 24:00，**超过 23:59**，应用会提示计划无效。

计算公式：

```
末封时间 = 首封时间 + (次数 - 1) × 间隔
末封时间 ≤ 最晚发送时刻
```

## 常见邮箱 SMTP

| 邮箱 | 服务器 | 端口 | SSL |
|------|--------|------|-----|
| QQ | smtp.qq.com | 465 | 是 |
| 163 | smtp.163.com | 465 | 是 |
| Gmail | smtp.gmail.com | 465 | 是 |

> 国内邮箱通常需开启 SMTP 并使用**授权码**而非登录密码。

## 在 Windows 下生成 macOS 应用

PyInstaller 不能直接在 Windows 上交叉生成 macOS `.app`。本项目通过 Windows
PowerShell 脚本触发 GitHub Actions 的 macOS 构建机，并自动下载打包结果。

首次使用先安装并登录 GitHub CLI：

```powershell
winget install --id GitHub.cli
gh auth login
```

如果登录使用的是 `github_pat_` 开头的细粒度 PAT，需要让该 Token 包含本仓库，
并把 `Repository permissions > Actions` 设置为 `Read and write`。否则触发构建时
会收到 `HTTP 403: Resource not accessible by personal access token`。也可改用 GitHub
CLI 的浏览器 OAuth 登录：

```powershell
Remove-Item Env:GH_TOKEN, Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
gh auth logout --hostname github.com
gh auth login --hostname github.com --git-protocol https --web --scopes repo,workflow
gh auth status
```

如果连接 GitHub 时出现 `TLS handshake timeout`，先让 GitHub CLI 走本地代理。例如
CordC / Clash 的 mixed 端口为 `7890` 时：

```powershell
$env:HTTP_PROXY="http://127.0.0.1:7890"
$env:HTTPS_PROXY="http://127.0.0.1:7890"
gh auth login --hostname github.com --git-protocol https --web --scopes repo,workflow
```

将当前修改提交并推送到 GitHub 后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_macos_from_windows.ps1 -Architecture all
```

若后续触发和下载也需要代理，可直接传入：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_macos_from_windows.ps1 `
  -Architecture all -ProxyUrl http://127.0.0.1:7890
```

`all` 会同时生成 Apple Silicon (`arm64`) 和 Intel (`x86_64`) 两个版本。也可只构建
一个架构：

```powershell
.\build_macos_from_windows.ps1 -Architecture arm64
.\build_macos_from_windows.ps1 -Architecture x86_64
```

下载结果位于 `dist/github-actions/run-<任务编号>/`，其中包含：

```text
AutoMail-macOS-arm64.zip
AutoMail-macOS-x86_64.zip
```

GitHub Actions 内部会调用 `build_macos.sh` 创建 `dist/AutoMail.app`、检查二进制架构
和签名，再使用 macOS 自带的 `ditto` 打包，以保留应用包所需的文件属性。

若本身就在 macOS 上，也可以直接执行：

```bash
bash build_macos.sh
```

可通过 `PYTHON_BIN`、`ICON_PATH` 和 `CODESIGN_IDENTITY` 环境变量指定 Python、
`.icns` 图标与 Developer ID。未提供正式签名时仅适合本机测试；对外分发还需要
Apple Developer ID 签名与 notarization（公证）。
