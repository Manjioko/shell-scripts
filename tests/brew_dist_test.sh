#!/usr/bin/env bash
# 发行包验收:证明 dist/ 里的 tarball 装出来**真的能用**,不是"打得出来"就算。
#
# 锁的性质(每条都是行为断言,不看源码字符串):
#   1. 包内不带 __pycache__/.pyc —— 残留旧 .pyc 可能盖住新源码,装出来与仓库不一致;
#   2. 同源码两次打包 sha 相同 —— Formula 的 sha256 才不会每次无意义抖动;
#   3. 按 Formula 的布局(prefix/bin + prefix/libexec)安装后,-h 与真实一轮都能跑;
#   4. 脱离源码树也能跑(到 / 下、PYTHONPATH 只留 user-site);
#   5. 真实一轮走桩网关:工具真的执行了、正文真的到了、请求数=2;
#   6. Formula 里写的 sha256/url/version 与实际产物一致(防手改漏改);
#   7. bootstrap 开关语义:WCHAT_NO_BOOTSTRAP=1 不建 venv,venv 路径跟随 CT_HOME/WCHAT_VENV。
set -u
cd "$(dirname "$0")/.." || exit 1
REPO=$PWD
PY=$(command -v python3)
# 真 user-site 必须在改 HOME 之前算好:site.getusersitepackages() 读 HOME,
# 沙箱 HOME 会把 user-site 指到沙箱里,导致 openai 找不到(踩过多次)。
USERSITE=$("$PY" -c 'import site;print(site.getusersitepackages())')
BASE=$(mktemp -d "${TMPDIR:-/tmp}/brewdist.XXXXXX")
P=0; F=0
ck() {
    local name="$1" got="$2" want="$3"
    if [[ "$got" == "$want" ]]; then P=$((P+1)); printf '  ✅ %s\n' "$name"
    else F=$((F+1)); printf '  ❌ %s\n     期望 [%s] / 实际 [%s]\n' "$name" "$want" "$got"; fi
}
cleanup() { pkill -P $$ 2>/dev/null; rm -rf "$BASE"; }
trap cleanup EXIT

V=0.0.0-disttest
echo "=== 1. 打包(两次,验可复现) ==="
"$PY" packaging/homebrew/make_release.py "$V" --out-dir "$BASE/a" \
      --formula-out "$BASE/a/wchat.rb" >"$BASE/build1.log" 2>&1
rc1=$?
"$PY" packaging/homebrew/make_release.py "$V" --out-dir "$BASE/b" \
      --formula-out "$BASE/b/wchat.rb" >"$BASE/build2.log" 2>&1
T1="$BASE/a/wchat-$V.tar.gz"; T2="$BASE/b/wchat-$V.tar.gz"
ck "打包成功" "$rc1" 0
ck "产物存在" "$([[ -s $T1 ]] && echo y)" y
ck "两次打包 sha 相同(可复现)" "$(shasum -a 256 "$T1" | cut -d' ' -f1)" \
   "$(shasum -a 256 "$T2" | cut -d' ' -f1)"

echo "=== 2. 包内容 ==="
LIST=$BASE/list.txt
tar tzf "$T1" >"$LIST" 2>/dev/null
ck "不含 __pycache__/.pyc" "$(grep -cE '__pycache__|\.pyc$' "$LIST")" 0
for must in "wchat-$V/wchat" "wchat-$V/wchat_py/cli.py" "wchat-$V/wchat_py/session.py" \
            "wchat-$V/requirements.txt" "wchat-$V/configs/config.example" \
            "wchat-$V/docs/brew-package.md"; do
    ck "含 $must" "$(grep -cx "wchat-$V/$(echo "$must" | sed "s|^wchat-$V/||")" "$LIST")" 1
done

echo "=== 3. 按 Formula 布局安装 ==="
tar xzf "$T1" -C "$BASE" || echo "!! 解包失败"
SRC=$BASE/wchat-$V; PFX=$BASE/prefix
mkdir -p "$PFX/bin" "$PFX/libexec/configs"
cp -R "$SRC/wchat" "$SRC/wchat_py" "$SRC/input_helper.py" "$SRC/render_helper.py" \
      "$SRC/requirements.txt" "$PFX/libexec/"
cp "$SRC/configs/config.example" "$PFX/libexec/configs/"
cp "$SRC/configs/ctmodel.py" "$PFX/libexec/configs/"
printf '#!/bin/bash\nexec "%s" "%s/wchat" "$@"\n' "$PY" "$PFX/libexec" >"$PFX/bin/wchat"
printf '#!/bin/bash\nexec "%s" "%s/configs/ctmodel.py" "$@"\n' "$PY" "$PFX/libexec" >"$PFX/bin/ctmodel"
chmod 755 "$PFX/bin/wchat" "$PFX/bin/ctmodel"
mkdir -p "$BASE/home" "$BASE/proj" && echo "x" >"$BASE/proj/f.txt"

out=$(HOME="$BASE/home" CT_HOME="$BASE/home/.ct" WCHAT_NO_BOOTSTRAP=1 PYTHONPATH="$USERSITE" \
      "$PFX/bin/wchat" -h 2>&1 </dev/null)
ck "装好后 -h 打印使用方法" "$(grep -c '使用方法' <<<"$out")" 1
out=$(cd / && HOME="$BASE/home" CT_HOME="$BASE/home/.ct" WCHAT_NO_BOOTSTRAP=1 \
      PYTHONPATH="$USERSITE" "$PFX/bin/wchat" -h 2>&1 </dev/null)
ck "脱离源码树(cwd=/)也能跑" "$(grep -c '使用方法' <<<"$out")" 1

echo "=== 4. 真实一轮(桩网关 + 工具调用) ==="
printf '%s' '[{"type":"tool","name":"list_dir","args":{"path":"."}},{"type":"text","text":"DISTOK"}]' \
    >"$BASE/reply.json"
: >"$BASE/req.jsonl"
port=$("$PY" - <<'EOF'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
EOF
)
STUB_LOG="$BASE/req.jsonl" STUB_REPLY="$BASE/reply.json" STUB_AUTH_DENY_IF="" STUB_FINAL_DENY_IF="" \
    "$PY" tests/stub_gateway.py "$port" >"$BASE/stub.err" 2>&1 &
spid=$!
for _ in $(seq 1 40); do
    "$PY" -c "import socket,sys;socket.create_connection(('127.0.0.1',int(sys.argv[1])),0.3).close()" \
        "$port" 2>/dev/null && break
    sleep 0.15
done
out=$( cd "$BASE/proj" && env -u AUTO_MODE -u WCHAT_AUTO_MODE -u WORK_ROOT -u WCHAT_HISTORY_DIR \
        -u AUTO_AUDIT_LOG -u MAX_TOOL_ROUNDS \
        HOME="$BASE/home" CT_HOME="$BASE/home/.ct" WCHAT_NO_BOOTSTRAP=1 PYTHONPATH="$USERSITE" \
        BASE_URL="http://127.0.0.1:$port/v1/chat/completions" WCHAT_API_KEY=sk-stub \
        "$PFX/bin/wchat" -f -st "列个目录" 2>&1 </dev/null )
kill $spid 2>/dev/null; wait $spid 2>/dev/null
ck "正文到达(DISTOK)" "$([ "$(grep -c 'DISTOK' <<<"$out")" -ge 1 ] && echo y)" y
ck "请求数=2(工具 1 轮 + 收尾)" "$(grep -c . "$BASE/req.jsonl")" 2
ck "屏幕出现工具执行行" "$([ "$(grep -c 'list_dir' <<<"$out")" -ge 1 ] && echo y)" y
# 桩网关日志只记形状;用它验"工具结果确实回填进第二轮请求"且没有孤儿 tool 消息
ck "第二轮请求含 4 条消息(user/assistant+tool_calls/tool/…)" \
   "$(tail -1 "$BASE/req.jsonl" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["n_messages"])')" 4
ck "第二轮无孤儿 tool 消息" \
   "$(tail -1 "$BASE/req.jsonl" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["orphan_tools"])')" 0

echo "=== 5. ~/.ct/config 的加载语义(master 对齐) ==="
# ① 权限 0644 的文件里是明文 key,同机他人可读 → 必须拒绝加载(现场提示 600)
# ② 环境变量优先:显式给了 BASE_URL 时,文件里的值不许盖掉它
cfgdir=$BASE/home/.ct; mkdir -p "$cfgdir"
printf 'BASE_URL="https://fromfile.invalid/v1/chat/completions"\nWCHAT_API_KEY="fromfile"\n' >"$cfgdir/config"
chmod 644 "$cfgdir/config"
r=$("$PY" - "$PFX/libexec" "$cfgdir" 2>"$BASE/cfg.err" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ.pop("WCHAT_API_KEY", None); os.environ.pop("API_KEY", None)
os.environ["CT_HOME"] = sys.argv[2]; os.environ["BASE_URL"] = "https://fromenv.example/v1/chat/completions"
from wchat_py import cli
cli._load_ct_config()
print(os.environ.get("WCHAT_API_KEY", "<unset>"), os.environ.get("BASE_URL"))
PY
)
ck "0644 的 config 拒绝加载(不留 key)" "$(cut -d' ' -f1 <<<"$r")" "<unset>"
ck "拒绝时给出 chmod 600 提示" "$(grep -c 'chmod 600' "$BASE/cfg.err")" 1
ck "环境变量优先,文件不覆盖" "$(cut -d' ' -f2 <<<"$r")" "https://fromenv.example/v1/chat/completions"
chmod 600 "$cfgdir/config"
r=$("$PY" - "$PFX/libexec" "$cfgdir" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ.pop("WCHAT_API_KEY", None); os.environ.pop("API_KEY", None)
os.environ["CT_HOME"] = sys.argv[2]
from wchat_py import cli
cli._load_ct_config()
print(os.environ.get("WCHAT_API_KEY", "<unset>"))
PY
)
ck "0600 的 config 正常加载" "$r" "fromfile"
# ctmodel 随包装:模型档案切换是日常操作,不能只存在于源码树
out=$(HOME="$BASE/home" CT_HOME="$cfgdir" "$PFX/bin/ctmodel" 2>&1 </dev/null)
ck "随包 ctmodel 可执行" "$(grep -c '当前模型\|未找到\|没有' <<<"$out")" 1

echo "=== 6. Formula 与实际产物一致 ==="
FML=$BASE/a/wchat.rb
SHA=$(shasum -a 256 "$T1" | cut -d' ' -f1)
ck "Formula sha256 == tarball 实测" \
   "$([ "$(grep -c "sha256 \"$SHA\"" "$FML")" -ge 1 ] && echo y)" y
ck "Formula version 一致" "$(grep -c "version \"$V\"" "$FML")" 1
ck "Formula url 指向该 tarball" "$(grep -c "wchat-$V.tar.gz" "$FML")" 1
ck "Formula 安装清单含 wchat_py" "$(grep -c 'libexec.install "wchat", "wchat_py"' "$FML")" 1

echo "=== 7. bootstrap 语义(不联网) ==="
b=$("$PY" - <<EOF
import os, sys
sys.path.insert(0, "$PFX/libexec")
from wchat_py import bootstrap
os.environ["CT_HOME"] = "$BASE/home/.ct"
os.environ.pop("WCHAT_VENV", None)
a = bootstrap._venv_dir()
os.environ["WCHAT_VENV"] = "$BASE/customvenv"
b = bootstrap._venv_dir()
os.environ["WCHAT_NO_BOOTSTRAP"] = "1"
before = sorted(os.listdir("$BASE/home"))
bootstrap.ensure_runtime()
after = sorted(os.listdir("$BASE/home"))
print(a.endswith("/.ct/venv"), b == "$BASE/customvenv", before == after)
EOF
)
ck "venv 路径跟随 CT_HOME/WCHAT_VENV 且 NO_BOOTSTRAP 不落盘" "$b" "True True True"

echo "RESULT total=$((P+F)) pass=$P fail=$F"
[[ $F -eq 0 ]]