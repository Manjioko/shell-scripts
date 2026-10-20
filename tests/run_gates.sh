#!/usr/bin/env bash
# 一条命令跑完全部闸门,并把每道的结论行解析成统一的对账表。
#
# 为什么要这个脚本:闸门清单、各道期望值、"红了先怀疑什么"以前只存在于人(或模型)的记忆里,
# 于是每次改动都要重新 ls tests/ 再猜该跑哪几个 —— 重复劳动该进脚本,判断该留在技能里
# (见 ~/.ct/skills/wchat-gates-runbook/SKILL.md)。
#
# 用法:
#   bash tests/run_gates.sh                 # 全部
#   bash tests/run_gates.sh rulefail shape  # 只跑名字含这些子串的
#   bash tests/run_gates.sh --main          # 额外跑主套件 auto_test.sh unit(慢)
#   bash tests/run_gates.sh --list          # 只列会跑什么
#
# 三条硬规矩(都是这几天踩过之后写进来的):
#   1) **rc=0 但没有结论行 = 失败**。中途静默退出比报错红一条更危险:前者会让你以为跑过了。
#   2) 不在这里重复各道闸门的期望数字。断言总数由 test_hardwall.sh 的 EXPECT_TOTAL 守,
#      规则出口数由 rulefail 的 _seen_rules 守 —— 抄第二份必然漂移(本项目已有三处同类教训)。
#   3) 每道闸门都打印它自己的机器可读结论原文,便于比对上一次的 src= sha。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# --- 解释器:bash 5+(3.2 下 declare -A 静默退化成索引数组,覆盖类断言会假绿)------
# 与 mutate_call_site.sh 同一判断口径,只是这里多了"自动挑一个 bash5 重新执行"。
_b5() {
    local b
    [[ "${BASH_VERSINFO[0]:-0}" -ge 5 ]] && { printf '%s' "$BASH"; return 0; }
    for b in /opt/homebrew/bin/bash /usr/local/bin/bash "$HOME/.linuxbrew/bin/bash" bash5; do
        [[ -x "$b" ]] && [[ "$("$b" -c 'echo ${BASH_VERSINFO[0]}' 2>/dev/null || echo 0)" =~ ^[5-9] ]] && { printf '%s' "$b"; return 0; }
    done
    return 1
}
if [[ "${BASH_VERSINFO[0]:-0}" -lt 5 ]]; then
    if B5=$(_b5); then exec "$B5" "$0" "$@"; fi
    echo "⚠ 当前 bash ${BASH_VERSION:-?} < 5 且找不到 bash5:部分闸门可能假绿,结果只作参考" >&2
fi
BASHX="${BASH}"

RUN_MAIN=0; LIST_ONLY=0; FILTERS=()
for a in "$@"; do
    case "$a" in
        --main) RUN_MAIN=1 ;;
        --list) LIST_ONLY=1 ;;
        -h|--help) sed -n '1,24p' "$0"; exit 0 ;;
        *) FILTERS+=("$a") ;;
    esac
done

# --- 闸门表:名字 | 命令 | 结论行的匹配正则(pass) | 说明 ---------------------------
# pass 正则**必须**锚定到该闸门自己打印的结论行;匹配不到就判失败(见硬规矩 1)。
GATE_NAMES=(语法 全表断言 判据出口话术 压缩布局 审计字段 形状向量 技能目录 MCP通道 流量回放 主套件 内联图片 写熔断 py通道 硬墙双路 网络搜索 视觉转述 py工具 认证解析 硬墙谓词 技能解析 MCP客户端 图片逻辑 压缩py 消息构造 历史变换 文件格式 路径归一 配置打包 路径裁决 会话授权 LLM适配 请求建体 消息存储 权限状态 工具规格 工具运行 硬墙语料 硬墙接缝 终端地基 终端引擎)
GATE_CMDS=("bash -n wchat.sh && bash -n tests/test_hardwall.sh && bash -n tests/run_gates.sh && echo SYNTAX-ok-3-files" "bash tests/test_hardwall.sh" "bash tests/rulefail_check.sh"
           "bash tests/compaction_pin_probe.sh" "bash tests/audit_field_probe.sh"
           "bash tests/probe_shapes.sh" "bash tests/skills_dir_test.sh"
           "bash tests/mcp_channel_test.sh"
           "bash tests/traffic_replay.sh" "bash tests/auto_test.sh unit"
           "bash tests/inline_image_test.sh" "bash tests/write_fuse_test.sh" "bash tests/py_channel_test.sh"
           "bash tests/hardwall_py_test.sh" "bash tests/webs_py_test.sh" "bash tests/vision_py_test.sh"
           "bash tests/py_textutil_test.sh" "bash tests/py_authparse_test.sh" "bash tests/py_hd_test.sh"
           "bash tests/py_skill_test.sh" "bash tests/py_mcp_client_test.sh" "bash tests/py_img_test.sh"
           "bash tests/py_compaction_test.sh" "bash tests/py_msg_test.sh" "bash tests/py_history_test.sh"
           "bash tests/py_filefmt_test.sh" "bash tests/py_pathutil_test.sh" "bash tests/py_config_test.sh" "bash tests/py_pathrule_test.sh" "bash tests/py_authz_test.sh" "bash tests/py_llm_test.sh" "bash tests/py_request_test.sh" "bash tests/py_msgstore_test.sh" "bash tests/py_state_test.sh" "bash tests/py_tools_test.sh" "bash tests/py_tools_run_test.sh" "bash tests/hdcmd_corpus_test.sh" "bash tests/hardwall_seam_test.sh" "bash tests/term_test.sh" "bash tests/term_suite.sh")
GATE_MODE=(c x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x)
GATE_PAT=('^SYNTAX-ok-3-files$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0 ' '^PASS=[0-9]+ FAIL=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^结论: 通过$' '不符 0$'
         '闸门全部通过' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0 ' '^结论:无新增漂移' '^RESULT|全部通过|PASS=[0-9]+ FAIL=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$'
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '基线与当前一致' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE=('两个脚本语法' '569 条判据向量 + 总数守卫(默认走 Python 生产路)' '规则覆盖 + 出口话术 + 分支数锁'
           'compact_due/布局/接线锁' 'trunc/tlen + root + pol 字段' '判据形状向量'
           'SKILLS_DIR 越界与软链防护'
           'MCP 通道:开关/暴露面/审计/预算(全打桩,不依赖 npm 与 Chrome)'
           '离线语料回放(漂移需先复跑再 GEN=1 固化)'
           '主套件(--main 才跑,慢)'
           'inline 图片通道:引用展开/gone 形状/外来字面量防误伤(不联网)'
           '路径熔断:只判不记/写成功才记账/F2 来回改/F3 同内容/键同源(不联网)'
           'Python 迁移通道:开关/回退/parity/接线(W0–W1)'
           '硬墙判据 Python 路径:569 向量 + 负对照接线证明(W2b)'
           'web_search_build_body Python 路径 parity(W8)'
           'vision 请求体/响应归一 Python 路径(M 制拆分·已收口)'
           'textutil clip_line_width/truncate_output(M 制拆分)'
           'auth_parse_verdict T19(M 制拆分)'
           'hd_mask_*/fold/split/foreign_cred/h11/strip(M 制拆分·已收口)'
           'scan_skills/tool_read_skill(M 制拆分)'
           'mcp_extract_json(M 制拆分·已收口)'
           'img_reject_wording/img_inline_sides(M 制拆分)'
           'compact_due/compact_apply(M 制拆分)'
           'add_message/auth_add_message(M 制拆分)'
           'history pack/drop_orphan_tools(M 制拆分)'
           'filefmt.read(M 制拆分·已收口)' 'pathutil expand_tilde/norm_path/resolve_path/in_root_exempt(W3·已收口)' 'py_cfg_dump 配置导出 + schema 一致性(W12-1)' 'hard_deny_path 纯判定化(W12-2·已收口)' 'is_granted/auto_allow_hit 会话授权(W12-3·已收口)' 'llm.py Seam 编码/kwargs/依赖探测(W12-4a)' '请求建体 parity(Python 建体 vs jq 逐字节)' 'messages 文件 store(回滚=截断/原子写/损坏抛错)' '权限会话态形状(幂等/顺序/rw⊃r/空串)' '工具注册表(原样字节/两句独立变换/组合不丢 MCP)' 'tools_run 五工具 parity(content/状态行/落盘字节)' '硬墙差分语料 44 条(bash 基线稳定; Python 版 parity 的 oracle)' '硬墙接缝(前端 WCHAT_PY_HARDWALL=1 vs 0 判定等价)' '终端层地基: raw_mode 三路退出恢复 termios)' '终端引擎: raw/pty/信号/run_shell_command 四用例')
# ⚠ 新闸门一律**追加在末尾**,别插在 主套件 之前:上面两处 `i == 9` 是按索引认主套件的
#   (LIST_ONLY 与执行循环各一处),插中间会让"跳过慢闸门"的特例指到别的闸门上 ——
#   这种"索引当身份证"的写法本就脆,但一次改动只治一个病,这里先不顺手重构。

want() {  # 有没有被参数选中
    (( ${#FILTERS[@]} == 0 )) && return 0
    local f n; for f in "${FILTERS[@]}"; do [[ "$1" == *"$f"* ]] && return 0; done; return 1
}

if (( LIST_ONLY )); then
    for i in "${!GATE_NAMES[@]}"; do
        (( i == 9 && ! RUN_MAIN )) && printf '  %-10s %-34s (跳过,--main 才跑)\n' "${GATE_NAMES[$i]}" "${GATE_CMDS[$i]}" && continue
        printf '  %-10s %-34s %s\n' "${GATE_NAMES[$i]}" "${GATE_CMDS[$i]}" "${GATE_NOTE[$i]}"
    done
    exit 0
fi

LOG=$(mktemp -d "${TMPDIR:-/tmp}/gates.XXXXXX"); trap 'rm -rf "$LOG"' EXIT
SRC_SHA=$(sha256sum wchat.sh 2>/dev/null | cut -c1-12); [[ -n "$SRC_SHA" ]] || SRC_SHA='<无sha256sum>'
pass=0 fail=0 skip=0 rows=""
printf '\n\033[1m══ 闸门对账(工作目录 %s · 源码 %s)══\033[0m\n' "$PWD" "$SRC_SHA"

for i in "${!GATE_NAMES[@]}"; do
    name="${GATE_NAMES[$i]}"; cmd="${GATE_CMDS[$i]}"; pat="${GATE_PAT[$i]}"
    if (( i == 9 && ! RUN_MAIN )); then
        want "$name" && printf '  \033[2m-- %-8s 主套件已跳过(--main 才跑)\033[0m\n' "$name"
        skip=$((skip + 1)); continue
    fi
    want "$name" || { skip=$((skip + 1)); continue; }
    out="$LOG/g$i.log"
    # 直接 exec 脚本,不套一层 bash -c:实测回放套 -c 会 rc=1 且**一行输出都没有**
    # (原因未定位:已排除 SHELLOPTS/pipefail 继承与嵌套层数两个假设,不能再猜)。
    # 只有第一条(语法闸门)是复合命令,必须走 -c。
    if [[ "${GATE_MODE[$i]}" == c ]]; then
        "$BASHX" -c "$cmd" > "$out" 2>&1; rc=$?          # 复合命令(语法闸门)才走 -c
    else
        "$BASHX" ${cmd#bash } > "$out" 2>&1; rc=$?       # 其余直接 exec,不套 bash -c
    fi
    concl=$(grep -aE "$pat" "$out" | tail -1)
    if [[ -n "$concl" && $rc -eq 0 ]]; then
        pass=$((pass + 1)); printf '  \033[32m✅ %-8s\033[0m %s\n' "$name" "$concl"
    else
        fail=$((fail + 1))
        printf '  \033[31m⚠  %-8s\033[0m rc=%s ' "$name" "$rc"
        if [[ -z "$concl" ]]; then
            # 这一句是本脚本存在的理由:没有结论行的"成功"不可信
            printf '\033[31m匹配不到结论行 /%s/\033[0m\n' "$pat"
        else
            printf '结论行与期望不符: %s\n' "$concl"
        fi
        # 失败时必须把日志尾巴交出来。只 grep "FAIL" 会漏掉一类形状:套件换了结论文案
        # (语料增长后回放就是这个样子:rc=1、结论行不再是"无新增漂移"),现象是"没跑完",
        # 实际是"该重固化基线"。两种处置完全不同,不能靠猜。
        tail -3 "$out" | sed 's/^/      │ /'
    fi
done

printf '\n\033[1m── 合计 pass=%d fail=%d skip=%d · 源码 %s ──\033[0m\n' "$pass" "$fail" "$skip" "$SRC_SHA"
printf 'GATES run=%d pass=%d fail=%d skip=%d sha=%s\n' "$((pass + fail))" "$pass" "$fail" "$skip" "$SRC_SHA"
(( fail == 0 )) || exit 1