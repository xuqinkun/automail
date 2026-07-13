#!/usr/bin/env bash

set -Eeuo pipefail

APP_NAME="AutoMail"
BUNDLE_ID="${BUNDLE_ID:-com.automail.desktop}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

fail() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "此脚本必须在 macOS 上运行；Windows 请改用 build_macos_from_windows.ps1。"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "找不到 Python：$PYTHON_BIN"
[[ -f "$ROOT_DIR/main.py" ]] || fail "找不到入口文件：$ROOT_DIR/main.py"
[[ -f "$ROOT_DIR/requirements.txt" ]] || fail "找不到依赖文件：$ROOT_DIR/requirements.txt"
cd "$ROOT_DIR"

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  fail "需要 Python 3.10 或更高版本。"
fi

PYTHON_ARCH="$("$PYTHON_BIN" -c 'import platform; print(platform.machine())')"
case "$PYTHON_ARCH" in
  arm64|x86_64) ;;
  *) fail "不支持的 Python 架构：$PYTHON_ARCH（仅支持 arm64 / x86_64）。" ;;
esac

BUILD_ROOT="${BUILD_ROOT:-$ROOT_DIR/.build/macos/$PYTHON_ARCH}"
VENV_DIR="${VENV_DIR:-$BUILD_ROOT/venv}"
DIST_DIR="${DIST_DIR:-$ROOT_DIR/dist}"

printf '==> 构建 AutoMail macOS 应用（%s）\n' "$PYTHON_ARCH"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  printf '==> 创建隔离构建环境：%s\n' "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

printf '==> 安装运行与打包依赖\n'
"$VENV_PYTHON" -m pip install --upgrade pip wheel
"$VENV_PYTHON" -m pip install -r "$ROOT_DIR/requirements.txt" pyinstaller

if [[ -d "$ROOT_DIR/tests" ]]; then
  printf '==> 运行单元测试\n'
  "$VENV_PYTHON" -m unittest discover -s "$ROOT_DIR/tests" -v
fi

PYINSTALLER_ARGS=(
  --noconfirm
  --clean
  --windowed
  --onedir
  --name "$APP_NAME"
  --osx-bundle-identifier "$BUNDLE_ID"
  --target-architecture "$PYTHON_ARCH"
  --hidden-import socks
  --distpath "$DIST_DIR"
  --workpath "$BUILD_ROOT/work"
  --specpath "$BUILD_ROOT/spec"
)

if [[ -n "${ICON_PATH:-}" ]]; then
  case "$ICON_PATH" in
    /*) ;;
    *) ICON_PATH="$ROOT_DIR/$ICON_PATH" ;;
  esac
  [[ -f "$ICON_PATH" ]] || fail "找不到图标文件：$ICON_PATH"
  PYINSTALLER_ARGS+=(--icon "$ICON_PATH")
elif [[ -f "$ROOT_DIR/assets/AutoMail.icns" ]]; then
  PYINSTALLER_ARGS+=(--icon "$ROOT_DIR/assets/AutoMail.icns")
fi

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  PYINSTALLER_ARGS+=(--codesign-identity "$CODESIGN_IDENTITY")
fi

printf '==> 生成 %s.app\n' "$APP_NAME"
"$VENV_PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}" "$ROOT_DIR/main.py"

APP_PATH="$DIST_DIR/$APP_NAME.app"
APP_EXECUTABLE="$APP_PATH/Contents/MacOS/$APP_NAME"

[[ -d "$APP_PATH" ]] || fail "打包结束但未找到应用：$APP_PATH"
[[ -x "$APP_EXECUTABLE" ]] || fail "应用主程序不可执行：$APP_EXECUTABLE"
/usr/bin/plutil -lint "$APP_PATH/Contents/Info.plist" >/dev/null

if command -v codesign >/dev/null 2>&1; then
  codesign --verify --deep --strict "$APP_PATH"
fi

printf '\n打包完成：%s\n' "$APP_PATH"
printf '本机打开：open "%s"\n' "$APP_PATH"
