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

## 打包为 macOS 应用

> 必须在 macOS 上执行；PyInstaller 不支持从 Windows 交叉生成 macOS `.app`。

```bash
bash build_macos.sh
```

脚本会在 `.build/macos/` 中创建隔离构建环境，并生成：

```text
dist/AutoMail.app
```

默认使用 `python3`，产物架构跟随该 Python（Apple Silicon 为 `arm64`，Intel 为
`x86_64`）。可选参数：

```bash
# 指定 Python、应用图标和 Developer ID 签名（均为可选）
PYTHON_BIN=/opt/homebrew/bin/python3 \
ICON_PATH=/path/to/AutoMail.icns \
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
bash build_macos.sh
```

未提供 `CODESIGN_IDENTITY` 时，PyInstaller 使用临时签名，适合本机运行。若要分发
给其他用户，还需要 Apple Developer ID 正式签名并完成 notarization（公证）。
