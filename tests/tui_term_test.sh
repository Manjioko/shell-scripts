#!/usr/bin/env bash
# TUI **真终端** resize 冒烟闸门(M3②的自动化补充)。
#
# 为什么要有它(与 tui_visual_test.py 分工):
#   - `tui_visual_test.py` 用 Textual run_test() 跑布局引擎,断言各 widget 的 region —— 它只改
#     **app 尺寸**、验布局不变量,不验"转义序列在真终端模拟器里渲染成什么"(残影/多框是这一层的事)。
#   - 本闸门在**真 tmux 终端模拟器**里跑整个 TUI,多尺寸 resize 后 `capture-pane` 抓真实渲染帧,
#     断言:每次恰好 **1 个**输入框、宽=新宽、**贴底**(顶角在倒数第 3 行)。tmux 是货真价实的终端
#     模拟器(有 alt-screen/重绘),故能抓住"resize 出残影/多框"——手搓 VT 屏抓不住(见 README)。
#
# 仍需用户在 Terminal.app / iTerm2 / Ghostty 上肉眼验(见 docs/tui-redesign.md §6.4 M3②);
# 本闸门是能自动化的那一层回归锁。
#
# 依赖 tmux + 带 textual 的解释器;缺任一 → 打印 SKIP + exit 77(run_gates 约定:环境性缺席)。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# 找一个能 import textual 的解释器
PYPY=""
for c in "${WCHAT_TUI_PY:-}" "$HOME/.ct/venv/bin/python" "$PWD/.scratch/tui_venv/bin/python" python3; do
    [ -n "$c" ] || continue
    if { command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; } && "$c" -c 'import textual' >/dev/null 2>&1; then
        PYPY="$c"; break
    fi
done
[ -n "$PYPY" ] || { echo "SKIP: 找不到带 textual 的解释器(可设 WCHAT_TUI_PY)"; exit 77; }
command -v tmux >/dev/null 2>&1 || { echo "SKIP: 没有 tmux(真终端 resize 冒烟需要 tmux)"; exit 77; }

S="wchattui_$$"
cleanup(){ tmux kill-session -t "$S" 2>/dev/null || true; }
trap cleanup EXIT
cleanup
# 在 detached 会话里跑 TUI 自测 demo(真 tmux 终端 = 真 PTY + 真终端模拟器)
tmux new-session -d -s "$S" -x 100 -y 30 \
    "env -u AUTO_MODE -u WCHAT_AUTO_MODE -u WCHAT_AUTO_ASSUME_YES WCHAT_NO_SETUP=1 WCHAT_TUI=1 $PYPY -m wchat_py.tui" \
    2>/dev/null || { echo "SKIP: tmux new-session 失败(可能需要 TTY;在纯 CI 容器里跳过)"; exit 77; }
sleep 2.5
tmux send-keys -t "$S" "hi" 2>/dev/null || true
tmux send-keys -t "$S" Enter 2>/dev/null || true
sleep 1.2

P=0; F=0
ck(){ if [ "$2" = "$3" ]; then P=$((P+1)); echo "  ok   $1"; else F=$((F+1)); echo "  FAIL $1(实际 [$2] / 期望 [$3])"; fi; }

echo "=== 真 tmux:多尺寸 resize 后每次恰好 1 个输入框、宽=新宽、贴底 ==="
for sz in "100 30" "62 22" "120 34" "40 15" "80 24" "73 31"; do
    set -- $sz; c=$1; r=$2
    tmux resize-window -t "$S" -x "$c" -y "$r" 2>/dev/null || tmux resize-pane -t "$S" -x "$c" -y "$r" 2>/dev/null || true
    sleep 1.0
    cap=$(tmux capture-pane -p -t "$S" 2>/dev/null || true)
    nc=$(printf '%s\n' "$cap" | grep -c '╭')
    topline=$(printf '%s\n' "$cap" | grep '╭' | head -1)
    w=${#topline}
    top_idx=$(printf '%s\n' "$cap" | grep -n '╭' | head -1 | cut -d: -f1)
    want_top=$(( r - 2 ))      # 顶角应在倒数第 3 行
    ck "resize ${c}x${r}:恰好 1 个输入框(╭ 数)" "$nc" "1"
    ck "resize ${c}x${r}:输入框宽=新宽" "$w" "$c"
    ck "resize ${c}x${r}:输入框贴底(顶角行=${r}-2)" "$top_idx" "$want_top"
done

echo "RESULT total=$((P + F)) pass=$P fail=$F"
[ "$F" -eq 0 ]

