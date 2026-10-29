#!/usr/bin/env bash
# 在 Linux 容器里跑 wchat 的"能不能用"探针。
#
# 为什么先写这个而不是直接做 deb:打包前必须知道"源码在这套系统上能不能跑"。
# 这一步零业务代码改动,却能把大部分问题提前暴露(解释器版本、缺系统命令、
# 终端相关断言、路径判据对 $HOME 的假设)。
#
# 用法:  bash packaging/linux/probe.sh ubuntu:24.04
#
# ⚠ 第一版有个自伤:写成 `cmd | head -4` 然后 `echo "rc=$?"` —— 拿到的是 head 的
#   rc(恒 0),于是 python3 根本没装、每条命令都 "command not found" 的情况下,
#   探针照样打印 rc=0 报"通过"。这正是本项目一直在防的静默失败:结论行与真实结果
#   无关。现在改成 run() 显式取 rc,并且**任一必需步骤失败就整体非 0**。
set -uo pipefail
IMG="${1:-ubuntu:24.04}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIP_PKGS="${PIP_PKGS:-}"

echo "══ 镜像 $IMG · 源码 $REPO ══"
docker run --rm \
    -v "$REPO":/src:ro \
    -e WCHAT_API_LOG=0 \
    -e WCHAT_NO_SETUP=1 \
    -e PIP_PKGS="$PIP_PKGS" \
    -w /work \
    "$IMG" bash -lc '
set -uo pipefail
FAILED=0
step() { printf "\n--- %s ---\n" "$1"; }
# run <说明> <命令...>:先打印输出尾巴,再打印**真实** rc,失败则记账
run() {
    local label="$1"; shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    printf "%s\n" "$out" | tail -12 | sed "s/^/    /"
    if [[ $rc -eq 0 ]]; then
        printf "  ✅ %s (rc=0)\n" "$label"
    else
        printf "  ⛔ %s (rc=%s)\n" "$label" "$rc"
        FAILED=$((FAILED + 1))
    fi
    return 0
}

cp -R /src /work 2>/dev/null
cd /work || { echo "⛔ 复制源码失败"; exit 2; }

step "系统"
. /etc/os-release 2>/dev/null && echo "  $PRETTY_NAME"
echo "  arch:   $(uname -m)"
echo "  bash:   $(bash --version | head -1)"

step "Python 解释器"
if command -v python3 >/dev/null 2>&1; then
    echo "  python3: $(python3 -V 2>&1) @ $(command -v python3)"
    python3 -c "import pip" 2>/dev/null && echo "  pip: 可用" || echo "  pip: 缺(需 python3-pip)"
else
    echo "  ⛔ 没有 python3"
    FAILED=$((FAILED + 1))
fi

if command -v python3 >/dev/null 2>&1; then
    step "1. 语法(compileall 全包)"
    python3 -m compileall -q wchat_py > /tmp/cc.log 2>&1
    rc=$?; tail -5 /tmp/cc.log | sed "s/^/    /"
    if [[ $rc -eq 0 ]]; then echo "  ✅ compileall (rc=0)"; else echo "  ⛔ compileall (rc=$rc)"; FAILED=$((FAILED+1)); fi

    step "2. 导入入口模块"
    run "import wchat_py.cli" env WCHAT_NO_BOOTSTRAP=1 \
        python3 -c "import sys; sys.path.insert(0,\".\"); import wchat_py.cli"

    step "3. 入口 -h(纯 stdlib 路径,不需要第三方依赖)"
    run "wchat -h" env WCHAT_NO_BOOTSTRAP=1 python3 wchat -h

    step "4. 版本判据(不联网)"
    run "MIN_PYTHON / _py_ok / _base_python" env WCHAT_NO_BOOTSTRAP=1 python3 -c "
import sys; sys.path.insert(0, \".\")
from wchat_py import version, bootstrap
print(\"MIN_PYTHON =\", version.MIN_PYTHON)
print(\"current    =\", sys.version_info[:2])
print(\"_py_ok     =\", bootstrap._py_ok(sys.version_info[:2]))
print(\"_base_python() =\", bootstrap._base_python())
"

    step "5. 第三方依赖是否齐(影响 -st / 真实会话)"
    python3 - <<"PY"
import importlib.util, sys
need = ["openai", "httpx", "prompt_toolkit", "rich"]
miss = [n for n in need if importlib.util.find_spec(n) is None]
print("  缺:", miss or "无")
sys.exit(1 if miss else 0)
PY
    if [[ $? -eq 0 ]]; then echo "  ✅ 依赖齐全"; else echo "  ⚠️  依赖缺失(正常:基础镜像没装,pip 装完再验)"; fi
fi

printf "\n══ 探针结论: %s ══\n" "$([[ $FAILED -eq 0 ]] && echo "PASS" || echo "FAIL($FAILED)")"
exit $(( FAILED > 0 ? 1 : 0 ))
'
rc=$?
echo "══ docker rc=$rc ══"
exit $rc

