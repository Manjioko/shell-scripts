#!/usr/bin/env bash
# B 阶段 1c：`wchat_py/render.py`（流式渲染）与 bash `stream_box_*` 的 **paint-only** parity。
#
# 为什么要"paint-only"：bash 的 `stream_box_append` 内部会按**节流**（STREAM_THROTTLE +
# EPOCHREALTIME）自己 paint，重绘次数依赖墙钟，无法确定性比对。所以只比
# **相同缓冲状态下 paint() 的字节** —— 那是"画什么"，与"何时画"解耦。
#
# 两个对照细节（都踩过）：
#   1. 用 `printf '\036'`（RS）分隔三次输出，**不要用 echo** —— echo 的换行会混进结果，
#      表现是"差 1 个字节",看起来像渲染错。
#   2. bash 侧要剥掉 sourcing 的 EXIT trap 尾巴（`ESC[?25h ESC[?7h`）。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RLDIR="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$RLDIR${PYTHONPATH:+:$PYTHONPATH}"
pass=0; fail=0
ck() { if [[ "$2" == "$3" ]]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
     else fail=$((fail+1)); printf '  FAIL %s: 得 [%s] 期望 [%s]\n' "$1" "$2" "$3"; fi; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT

cat > "$W/ref.sh" <<'REF'
_WCHAT_LIB_ONLY=1 API_KEY=sk-stub
. "$1" < /dev/null > /dev/null
stream_open=1; stream_anchor=1; stream_drawn=0
stream_buf=("甲行" "乙行" "丙行"); stream_line_buf="残行"; stream_box_paint; printf '\036'
stream_buf=(); stream_line_buf="只有残行"; stream_box_paint; printf '\036'
stream_buf=("x"); stream_line_buf=""; stream_box_paint; printf '\036'
REF
/opt/homebrew/bin/bash "$W/ref.sh" "$RLDIR/wchat_ref" 2>/dev/null > "$W/b.out"

python3 - > "$W/p.out" <<'PY'
import sys
from wchat_py.render import StreamBox
for buf, line in ((["甲行", "乙行", "丙行"], "残行"), ([], "只有残行"), (["x"], "")):
    b = StreamBox(10); b.buf = list(buf); b.line = line
    sys.stdout.write(b.paint()); sys.stdout.write("\036")
PY

python3 - "$W/b.out" "$W/p.out" <<'PY' > "$W/res"
import re, sys
a = open(sys.argv[1], "rb").read()
b = open(sys.argv[2], "rb").read()
a = re.sub(rb"\x1b\[\?25h\x1b\[\?7h", b"", a)      # 剥 EXIT trap 尾巴
sys.stdout.write("same(%d)" % len(a) if a == b else "diff(%d/%d)" % (len(a), len(b)))
PY
ck "paint 三状态逐字节一致" "$(cat "$W/res")" "same(165)"

# 承重自检：改一帧文本，必须红（否则这判据是恒绿）
sed -i '' 's/甲行/甲行X/' "$W/p.out" 2>/dev/null || true
python3 - "$W/b.out" "$W/p.out" <<'PY' > "$W/res2"
import re, sys
a = open(sys.argv[1], "rb").read(); b = open(sys.argv[2], "rb").read()
a = re.sub(rb"\x1b\[\?25h\x1b\[\?7h", b"", a)
sys.stdout.write("same" if a == b else "diff")
PY
ck "篡改一帧文本 → 判据变红" "$(cat "$W/res2")" "diff"

printf 'RESULT total=%d pass=%d fail=%d\n' "$((pass+fail))" "$pass" "$fail"
[[ $fail -eq 0 ]]

