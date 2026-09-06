#!/bin/bash
# wchat 依赖安装脚本:自动检测平台(macOS/Linux/WSL2),安装 wchat.sh 用到的
# 外部组件,已安装的自动跳过。用法: bash setup.sh
#
#   必装: python3 + prompt_toolkit(输入框)、rich(markdown 渲染,全平台一致)、
#         jq(消息处理)、curl(API 调用)
#   推荐: w3m/lynx(网页解析)、ffmpeg(图片转述)、xclip(剪贴板)、zenity(WSLg)
set -u

step() { printf '\033[2m│\033[0m %s\n' "$*"; }
ok()   { printf '\033[2m│\033[0m ✅ %s\n' "$*"; }
warn() { printf '\033[2m│\033[0m ⚠️  %s\n' "$*"; }
fail() { printf '\033[2m│\033[0m ❌ %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

detect_os() {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo macos
    elif [[ -n "$WSL_DISTRO_NAME" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
        echo wsl
    else
        echo linux
    fi
}

OS=$(detect_os)
step "平台: $OS"
rc=0

# ---------- macOS: Homebrew ----------
if [[ "$OS" == "macos" ]]; then
    if ! have brew; then
        fail "未找到 Homebrew:请先安装 https://brew.sh 后重跑本脚本"
        exit 1
    fi
    step "检查/安装 Homebrew 包..."
    # curl/sips/textutil/osascript/pbpaste 为 macOS 系统自带
    for pkg in python3 jq w3m ffmpeg; do
        if have "$pkg"; then
            ok "$pkg 已就绪"
        else
            step "brew install $pkg ..."
            brew install "$pkg" || { fail "$pkg 安装失败"; rc=1; }
        fi
    done
fi

# ---------- Linux / WSL2: apt(仅 Debian/Ubuntu 系) ----------
if [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
    if ! have apt-get; then
        fail "未找到 apt-get(本脚本仅支持 Debian/Ubuntu 系),请手动安装:"
        fail "  python3 python3-pip jq w3m glow ffmpeg xclip zenity"
        exit 1
    fi
    step "检查/安装 apt 包..."
    # (检测命令 → apt 包名)
    check_install() {
        if have "$1"; then
            ok "$1 已就绪"
        else
            to_install+=("$2")
        fi
    }
    to_install=()
    check_install python3 python3
    check_install pip3 python3-pip
    check_install jq jq
    check_install w3m w3m
    check_install ffmpeg ffmpeg
    check_install xclip xclip
    check_install zenity zenity
    if (( ${#to_install[@]} )); then
        step "sudo apt install -y ${to_install[*]} ...(需要输入密码)"
        sudo apt-get update >/dev/null 2>&1
        sudo apt-get install -y "${to_install[@]}" || { fail "apt 安装失败,可重跑本脚本"; rc=1; }
    fi
fi

# ---------- Python 组件(pip,全平台) ----------
# 输入框(prompt_toolkit)与 markdown 渲染(rich)同走 pip 通道,三平台一致
if have python3 && python3 -c 'import prompt_toolkit, rich' 2>/dev/null; then
    ok "prompt_toolkit + rich 已就绪"
else
    if ! have pip3; then
        fail "缺少 pip3:请先安装 python3-pip 后重跑本脚本"
        rc=1
    else
        step "pip3 install --user prompt_toolkit rich ..."
        if ! pip3 install --user prompt_toolkit rich 2>/dev/null; then
            # Debian/Ubuntu 新版受 PEP 668 保护,需显式放开
            warn "普通安装被拒(PEP 668),改用 --break-system-packages"
            pip3 install --user --break-system-packages prompt_toolkit rich \
                || { fail "安装失败:请手动执行 pip3 install --user prompt_toolkit rich"; rc=1; }
        fi
    fi
fi

# ---------- 终检 ----------
step "终检..."
for c in python3 jq curl; do
    have "$c" || { fail "缺少 $c"; rc=1; }
done
python3 -c 'import prompt_toolkit, rich' 2>/dev/null || { fail "prompt_toolkit/rich 不可导入"; rc=1; }
ok "markdown 渲染: rich"
if have w3m || have lynx; then
    ok "网页解析: 就绪"
else
    warn "未安装 w3m/lynx(可选,网页抓取能力受限)"
fi

if (( rc )); then
    fail "存在未解决的依赖,请按上方提示处理后重跑"
    exit 1
fi
ok "全部就绪,可以运行 wchat 了"
