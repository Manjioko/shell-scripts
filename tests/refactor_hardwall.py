#!/usr/bin/env python3
"""B 阶段收口(2/2)：删除 wchat.sh 里 hard_deny_cmd 的 bash 判据体，替换为 Python seam 薄壳。

安全性：
- 先备份 wchat.sh -> .scratch/wchat.pre_refactor.bak
- 用"从 `hard_deny_cmd() {` 到其后第一行顶格 `}`"定位函数边界（嵌套 helper 的 `}` 都是缩进，不会误伤）
- 打印被删行数与首尾行，供人工核对
- 不执行 bash -n（由调用方另跑，便于失败时保留 .bak）
"""
import os
import shutil

REPO = os.getcwd()
SRC = os.path.join(REPO, "wchat.sh")
BAK = os.path.join(REPO, ".scratch/wchat.pre_refactor.bak")

NEW = r'''hard_deny_cmd() {
    hd_rule=""; hd_msg=""; HD_SOFT=""
    # ── B 阶段收口(2/2)：判定唯一活实现在 Python hardwall（见 wchat_py/hardwall.py）。──
    # bash 前端只做出口：取 JSON 回填 hd_rule/hd_msg/HD_SOFT。组件不可用 -> fail-closed 拒，
    # 绝不静默放行（与全仓其它模块"组件缺席即拒"的降级方向一致）。
    if declare -F py_run >/dev/null 2>&1; then
        local _hwj
        _hwj=$(printf '%s' "$1" | py_run hardwall json ${auto_mode:+--auto} 2>/dev/null) || _hwj=""
        if [[ -n "$_hwj" && "$_hwj" == \{* ]]; then
            hd_rule=$(printf '%s' "$_hwj" | jq -r '.rule // ""')
            hd_msg=$(printf '%s' "$_hwj" | jq -r '.msg // ""')
            HD_SOFT=$(printf '%s' "$_hwj" | jq -r '.soft // ""')
            [[ -n "$hd_rule" ]] && return 0
            return 1
        fi
    fi
    hd_rule="H0"
    hd_msg="硬墙判据组件(hardwall)不可用, 出于安全一律拒绝; 请检查 python 依赖与 PYTHONPATH"
    return 0
}'''


def main():
    lines = open(SRC, encoding="utf-8").read().split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith("hard_deny_cmd() {")), None)
    if start is None:
        raise SystemExit("未找到 hard_deny_cmd() {")
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), None)
    if end is None:
        raise SystemExit("未找到函数收尾 }")
    shutil.copyfile(SRC, BAK)
    removed = end - start + 1
    out = lines[:start] + NEW.split("\n") + lines[end + 1:]
    open(SRC, "w", encoding="utf-8").write("\n".join(out))
    print("边界: 行 %d..%d（原函数 %d 行）" % (start + 1, end + 1, removed))
    print("首行: %r" % lines[start])
    print("末行: %r" % lines[end])
    print("备份: %s" % BAK)
    print("已替换为 Python seam 薄壳（%d 行）" % len(NEW.split("\n")))


if __name__ == "__main__":
    main()

