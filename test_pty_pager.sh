#!/bin/bash
# 工具窗口「关分页 env」回归测试 —— 覆盖 wchat.sh run_with_window 的 pty 分页器挂死修复。
#   背景:子命令在 pty 里 isatty(stdout)=true → git 自动起 less;模型没有"发按键"的
#   通道,只能等 CMD_TIMEOUT(120s) 到点,期间只拿到首屏输出。wrapped_cmd 必须注入
#   GIT_PAGER=cat PAGER=cat GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true GIT_TERMINAL_PROMPT=0。
# 断言:
#   静态 —— 三个 pty 分支共用的 wrapped_cmd 与无 pty 兜底 eval 都带 pager_off;
#           超时提示里有分页器方向(--no-pager/-n)
#   行为 —— 自建 61 条提交的临时仓库:不注入 → 输出行数 < 总数(被首屏截住);
#           注入 → 输出行数 = 总数(全量返回)
# 行为实测走 script 的 bsd / util-linux 两族;python3 兜底族与无 pty 分支由静态断言
# 保证同一串 env,不再重复起 pty。
# 用法: bash test_pty_pager.sh [-v]
set -u
cd "$(dirname "$0")" || exit 1
TARGET=./wchat.sh
[[ -f "$TARGET" ]] || { echo "找不到 $TARGET"; exit 1; }
command -v git >/dev/null 2>&1 || { echo "缺少 git"; exit 1; }

VERBOSE=0
[[ "${1:-}" == "-v" ]] && VERBOSE=1
pass=0; fail=0; skip=0
ok() { pass=$((pass + 1)); [[ $VERBOSE -eq 1 ]] && printf '  \033[32mPASS\033[0m %s\n' "$1"; return 0; }
ng() { fail=$((fail + 1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; [[ -n "${2:-}" ]] && printf '       %s\n' "$2"; return 0; }
sk() { skip=$((skip + 1)); printf '  \033[33mSKIP\033[0m %s\n' "$1"; return 0; }

bash -n "$TARGET" 2>/dev/null && ok "wchat.sh 语法检查" || ng "wchat.sh 语法检查" "bash -n 报错"

PAGER_ENV='GIT_PAGER=cat PAGER=cat GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true GIT_TERMINAL_PROMPT=0'

# ---------- 静态断言 ----------
grep -qF "local pager_off='export ${PAGER_ENV};" "$TARGET" \
    && ok "pager_off 定义含全部 5 个变量" \
    || ng "pager_off 定义不完整" "应含: ${PAGER_ENV}"
grep -qF 'wrapped_cmd="${pager_off}stty' "$TARGET" \
    && ok "wrapped_cmd 带 pager_off(覆盖 bsd/util-linux/python 三分支)" \
    || ng "wrapped_cmd 未带 pager_off" "pty 分支会退回挂死行为"
grep -qF 'eval "${pager_off}$cmd"' "$TARGET" \
    && ok "无 pty 兜底分支也带 pager_off" \
    || ng "无 pty 兜底分支漏注入" "editor/credential 提示仍会挂"
grep -qF -- '--no-pager' "$TARGET" && grep -qF '不认这些变量' "$TARGET" \
    && ok "超时提示给出分页器方向" \
    || ng "超时提示未提分页器" "模型会原样重试再烧一次超时"

# ---------- 行为实测 ----------
d=$(mktemp -d) || { echo "mktemp 失败"; exit 1; }
G="git -C $d -c user.email=t@t -c user.name=t -c commit.gpgsign=false"
$G init -q 2>/dev/null || { echo "git init 失败,跳过行为实测"; skip=$((skip + 3)); }
if [[ -d "$d/.git" ]]; then
    for i in $(seq 1 60); do $G commit -q --allow-empty --no-verify -m "c$i" 2>/dev/null; done
    total=$(git -C "$d" rev-list --count HEAD 2>/dev/null)
    [[ -z "$total" ]] && total=0
    # pty 语法家族(与产品同一探测顺序)
    fam=""
    if script -q /dev/null true >/dev/null 2>&1; then fam=bsd
    elif script -q -e /dev/null true >/dev/null 2>&1; then fam=util-linux; fi
    # 全局 core.pager 被配成 cat 之类时,"未注入"这半边失去意义,只报不判
    cfg_pager=$(git config --get core.pager 2>/dev/null)

    # run_pty <env 前缀> <输出文件>  → stdout 打印捕获到的行数
    # 结构照产品:输入 FIFO 用 <> 保持写端常开(子命令读不到 EOF),输出落文件
    run_pty() {
        local pfx="$1" outf="$2" mk_p="" p="" n=0 prev=-1 i
        mkfifo "$d/in" 2>/dev/null
        exec 9<>"$d/in"
        # 非交互脚本默认关 job control,后台任务与脚本同组;开一下让子命令自成进程组
        set -m
        case "$fam" in
            bsd)        ( cd "$d" && cat "$d/in" | script -q /dev/null /bin/bash -c "$pfx stty cols 80 rows 20; git log --oneline -n 999" >"$outf" 2>&1 ) & ;;
            util-linux) ( cd "$d" && SHELL=/bin/bash script -qec "$pfx stty cols 80 rows 20; git log --oneline -n 999" /dev/null <"$d/in" >"$outf" 2>&1 ) & ;;
        esac
        p=$!
        # 等到输出稳定(分页器场景只会稳定在首屏),最多 6s
        for i in 1 2 3 4 5 6; do
            sleep 1
            n=$(wc -l <"$outf" 2>/dev/null | tr -d ' ')
            [[ -z "$n" ]] && n=0
            [[ "$n" == "$prev" && $n -gt 0 ]] && break
            prev=$n
        done
        # 管道里的 cat/script/less 与子 shell 同属一个进程组(上面 set -m),整组终止;
        # 否则只杀子 shell 会留下 cat + less 僵尸
        kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null
        wait "$p" 2>/dev/null
        exec 9>&-
        set +m
        printf '%s' "$n"
    }

    if [[ -z "$fam" ]]; then
        sk "无可用 script 家族,跳过行为实测(静态断言已覆盖注入点)"
    elif [[ $total -le 25 ]]; then
        sk "临时仓库提交数太少($total),不足以超过一屏"
    else
        raw=$(run_pty 'unset GIT_PAGER PAGER GIT_EDITOR GIT_SEQUENCE_EDITOR GIT_TERMINAL_PROMPT;' "$d/out.raw")
        fix=$(run_pty "export ${PAGER_ENV};" "$d/out.fix")
        [[ $VERBOSE -eq 1 ]] && printf '       家族=%s 提交总数=%s 未注入=%s 注入后=%s\n' "$fam" "$total" "$raw" "$fix"
        if [[ -n "$cfg_pager" && "$cfg_pager" != *less* ]]; then
            sk "全局 core.pager=$cfg_pager 不分页,不判「未注入会被截住」(实测 $raw/$total)"
        else
            (( raw < total )) && ok "未注入时被首屏截住(${raw}/${total} 行)" \
                || ng "未注入却拿到全量(${raw}/${total})" "pty 未按预期分配,或分页 env 从别处生效"
        fi
        (( fix >= total )) && ok "注入后全量输出(${fix}/${total} 行)" \
            || ng "注入后仍缺行(${fix}/${total})" "关分页 env 未覆盖该程序"
    fi
fi
rm -rf "$d"

printf '\n\033[1mPASS=%d FAIL=%d SKIP=%d\033[0m\n' "$pass" "$fail" "$skip"
[[ $fail -eq 0 ]] || exit 1
exit 0