#!/usr/bin/env bash
# bash 5+ 解析器 —— **唯一出处**。任何测试都不要写死 /opt/homebrew/bin/bash。
#
# 为什么需要它(实测,不是假想):
#   Linux/容器里写死 `/opt/homebrew/bin/bash -c ...` **不会报"找不到命令"** ——
#   命令替换/子 shell 里失败,外层只拿到空字符串,于是断言拿到全空:
#     · write_fuse_test.sh 的 load_into 整套空转 → "写熔断 21 红"
#     · 权限状态 3 红、其他同形状的红
#   看着像产品坏了,实际是夹具路径不可移植。这类红最容易骗人,因为它不是"报错",是"沉默"。
#
# 用法(shell): . "$(dirname "$0")/lib_bash.sh"   → 之后用 "$WCHAT_BASH"
# 用法(python): from _bashbin import bash_bin     → bash_bin()
#
# 顺序:$WCHAT_BASH > PATH 里的 bash(版本够才算) > brew 常见位置。
# 版本必须 >= 5:被测脚本里有 declare -A / ${v,,} / mapfile,3.2 上测的不是同一套行为。

wchat_find_bash() {
    local b v
    for b in "${WCHAT_BASH:-}" "$(command -v bash 2>/dev/null || true)" \
             /opt/homebrew/bin/bash /usr/local/bin/bash "${HOME}/.linuxbrew/bin/bash" \
             /bin/bash /usr/bin/bash; do
        [[ -n "$b" && -x "$b" ]] || continue
        v=$("$b" -c 'echo ${BASH_VERSINFO[0]:-0}' 2>/dev/null || echo 0)
        [[ "$v" =~ ^[5-9] ]] || continue
        printf '%s\n' "$b"
        return 0
    done
    return 1
}

WCHAT_BASH=$(wchat_find_bash) || WCHAT_BASH=""
export WCHAT_BASH
if [[ -z "$WCHAT_BASH" ]]; then
    printf '需要 bash 5+(装一个 bash5,或设 WCHAT_BASH=/path/to/bash)\n' >&2
fi

