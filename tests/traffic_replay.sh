#!/usr/bin/env bash
# 真实流量回放:把审计日志(AUTO_AUDIT_LOG=~/.ct/auto/audit.log)里**当时被门看过**的
# 每一条 shell 命令,用当前版本的判据重判一遍,和历史判定对账。
#
# 为什么需要它:硬墙表里的向量是我手写的,只覆盖"我想得到的形状";审计日志是模型
# 真写出来的命令,覆盖我想不到的形状。放宽/收紧类改动没有这份语料就只能凭感觉。
#
# 口径(第十五轮的教训):输入不可用一律红着退出,绝不"0 条也全绿"。
#   用法: bash tests/traffic_replay.sh [审计日志路径]
#         可选 SRC=/path/to/wchat.sh 指定被测版本(默认 $PWD/wchat.sh)
# 退出码: 0 语料可用且无"放宽方向"的历史判定漂移;1 语料/装载不可用;2 出现放行漂移
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
# 仓库根必须在这里就存下来:下面要 source 硬墙表的装载区,而它自己会 cd(实测事后
# 再反推 $(dirname $0)/.. 会失败 —— CWD 已经不在原地,相对 cd 直接不成立,REPO_DIR
# 成空串,基线路径变成 "/tests/traffic_baseline.txt",写入失败、整轮对账无从对照)。
REPO_DIR="$PWD"
LOG="${1:-$HOME/.ct/auto/audit.log}"
SRC="${SRC:-$PWD/wchat.sh}"

fail_hard() { printf '  BAD  %s\n' "$1"; exit "${2:-1}"; }
[[ -r "$LOG" ]] || fail_hard "语料不可读:$LOG(没有真实流量就不许报'回放全绿')" 1
[[ -r "$SRC" ]] || fail_hard "被测版本不可读:$SRC" 1

# 装载:复用硬墙表开头的装载区(它负责从 SRC 抽常量+函数,含 HD_* 族)。
# ⚠ 范围**不能写死行号**:判定链依赖的常量铺在 harness 前 160 行里,而 WORK_ROOT="$PWD"
#   在第 162 行 —— 上一版只取 1,95p,于是任何碰"工作目录边界"的命令都会在 set -u 下
#   当场打死判定器(实测 `cd "$PWD" && python3 x.py <会话历史目录> 2>&1 | head -1` 报
#   "WORK_ROOT: unbound variable")。我那 3 条"判定崩溃"全是这个装载残缺造成的,
#   不是产品漏洞 —— 报"崩溃"没错,但根因在自己身上。
# 改成按标记行推:取到 harness 自己给 WORK_ROOT 赋值的那一行为止;标记不见了就红着退出。
LOAD_END=$(grep -n '^WORK_ROOT="\$PWD"' test_hardwall.sh | head -1 | cut -d: -f1)
[[ -n "$LOAD_END" ]] || fail_hard "harness 里找不到 WORK_ROOT 赋值行,装载范围无从推定(别退回写死行号)" 1
LOADER=$(mktemp "${TMPDIR:-/tmp}/tr-loader.XXXXXX") || fail_hard "mktemp 失败" 1
trap 'rm -f "$LOADER"' EXIT
sed -n "1,${LOAD_END}p" test_hardwall.sh > "$LOADER" || fail_hard "装载片段抽取失败" 1

ROWS=$(mktemp "${TMPDIR:-/tmp}/tr-rows.XXXXXX") || fail_hard "mktemp 失败" 1
trap 'rm -f "$LOADER" "$ROWS"' EXIT
# 抽出 tool/rule/verdict/target 四元组,用 \x1f 分隔(命令里不会出现)。
# 只取 shell 类工具且 target 非空的行 —— 文件类工具走的是 hard_deny_path,不在这条链上。
python3 - "$LOG" > "$ROWS" <<'PY'
import json, sys
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    t = o.get("tool") or ""
    tgt = o.get("target") or ""
    if "shell" not in t or not tgt.strip():
        continue
    u = "\x1f"
    # 可信性筛查(本轮实测出来的):审计日志写的是 ${cmd:0:200} 的**摘要**,而且多行命令
    # 落盘后换行被抹平 —— 于是"被拦的那一段"可能压根没进日志,或被第一个 # 当成注释吞掉。
    # 拿这种行去对账会把语料失真报成"判据放宽"(首跑就报了 60 条,全是这个形状)。
    # 所以:截断到 200 的、含换行的、含 # 的一律标 LOSSY,只做统计不参与对账。
    # 原因要分开记:只有知道"截断占几条、换行占几条",才谈得上改不改日志格式(第 3 件事)。
    rs = []
    if len(tgt) >= 200:
        rs.append("trunc")
    if "\n" in tgt:
        rs.append("nl")
    if "#" in tgt:
        rs.append("hash")
    flag = "OK" if not rs else "LOSSY:" + ",".join(rs)
    print(u.join([t, o.get("rule") or "", o.get("verdict") or "",
                  o.get("tier") or "full", flag, tgt.replace(u, " ").replace("\n", " ")]))
PY
NROWS=$(wc -l < "$ROWS" | tr -d ' ')
# fail-closed:python 缺席时上面那条重定向会造出一个空文件,单看 NROWS 分不出
# "没有语料"和"解析器根本没跑" —— 这是第十五轮"假绿口径"的同一个形状。
command -v python3 >/dev/null 2>&1 || fail_hard "缺 python3,无法解析 JSONL 语料" 1
(( NROWS > 0 )) || fail_hard "语料里没有任何 shell 命令($LOG 存在但无可用条目)" 1

# 判据装载
if ! . "$LOADER" "$SRC" >/dev/null 2>&1; then
    fail_hard "装载被测判据失败(缺 hard_deny_cmd?)" 1
fi
declare -F hard_deny_cmd >/dev/null 2>&1 || fail_hard "hard_deny_cmd 未装载,回放无意义" 1

pass=0; drift_relax=0; drift_tighten=0; same=0; idx=0
declare -A LOSSY_H=()   # 有损原因直方图(set -u 下必须先声明,否则计数踩空)
# 基线:漂移本身不可怕(历史日志是**旧版本**判的,必然有差),可怕的是"新引入的漂移"。
# 所以把当前漂移集合固化成基线文件,只有基线之外的新漂移才让闸门红。
# 生成/更新:GEN=1 bash tests/traffic_replay.sh   对比:直接跑(无基线则红着提示)
# 基线用绝对路径,且取自上面在 cd 之后立刻存下的 REPO_DIR(不能用 $PWD、也不能事后
# 反推 $0:装载区会 cd 走,基线就被写到别处甚至根目录下,上一轮实测踩过)。
BASE="${BASE:-$REPO_DIR/tests/traffic_baseline.txt}"
GEN="${GEN:-}"
NEW_DRIFT=0
NEW_CMD=0
NOBASE=0
CUR_SIGS=""
declare -A GOT_HIST=() WAS_HIST=()
printf '回放语料 %s 条 ← %s\n' "$NROWS" "$LOG"
printf '被测版本 src=%s\n\n' "$(git hash-object "$SRC" 2>/dev/null | cut -c1-12)"
while IFS=$'\037' read -r tool rule0 verdict0 tier0 flag0 cmd; do
    [[ -n "${cmd:-}" ]] || continue
    idx=$((idx + 1))
    # 有损行不参与对账,但必须报数:否则"全部一致"是筛出来的假绿。
    if [[ "$flag0" == LOSSY* ]]; then
        # 只按"有没有 LOSSY 前缀"判,原因逗号列表原样进直方图:截断/换行/# 三类各自的条数
        # 决定了改日志格式能换回多少覆盖率(不分类就没法判断值不值)。
        lossy=$(( ${lossy:-0} + 1 ))
        LOSSY_H["${flag0#LOSSY:}"]=$(( ${LOSSY_H["${flag0#LOSSY:}"]:-0} + 1 ))
        continue
    fi
    hd_rule=""; hd_msg=""
    # 判定必须关在子 shell 里:hard_deny_cmd 内部有不受 if 保护的失败命令,一旦在
    # set -u 下踩到未定义变量就把 shell 当场打死(实测装载残缺时一条 `cd "$PWD" && …`
    # 就终止了主 shell,汇总全丢)。表只开 `set -uo pipefail`(第 21 行)——
    # 我上一轮注释写的"带着 set -e"是错归因,顺手改回来,别让它继续误导下一个人。
    # 表里的 deny_rule() 一直是这么做的 —— 照它抄,不另创口径。
    # 口径:hard_deny_cmd **rc=0 表示拦住**;rc!=0 是放行。
    got=$( auto_mode="${tier0:-full}"
           if hard_deny_cmd "$cmd" >/dev/null 2>&1; then printf '%s' "${hd_rule:-HIT}"
           else printf 'ALLOW'; fi )
    if [[ -z "$got" ]]; then
        # 判定器崩溃 ≠ 放行。实测:真实语料里 `cd "$PWD" && python3 .real_check.py …`
        # 这一条会让判定链中途退出(装载区带着 set -e,函数内有不受 if 保护的失败命令),
        # 子 shell 死掉 → got 为空 → 上一版把空值当成 ALLOW,于是"崩溃"被记成"放宽方向漂移"。
        # 这是最危险方向的假绿:判不出来的东西必须单独计数、不参与对账。
        jerr=$(( ${jerr:-0} + 1 ))
        printf '  [判定崩溃] %s\n' "${cmd:0:70}"
        continue
    fi
    # 历史侧只认形如 H*/P* 的规则号;`final` 之类是事件标记,不是判定。
    if [[ "$rule0" =~ ^[HP][0-9]+$ ]]; then was="$rule0"; else was="ALLOW"; fi
    WAS_HIST["$was"]=$(( ${WAS_HIST["$was"]:-0} + 1 ))
    GOT_HIST["$got"]=$(( ${GOT_HIST["$got"]:-0} + 1 ))
    if [[ "$got" == "$was" ]]; then
        same=$((same + 1))
        continue
    fi
    # 历史"被拦"→ 现在"放行" = 放宽方向,必须逐条人审;反之是收紧/换号,记数报警。
    if [[ "$was" != ALLOW && "$got" == ALLOW ]]; then
        drift_relax=$((drift_relax + 1))
        printf '  放宽? 第%d条 历史[%s] → 现在[放行] | %s\n' "$idx" "$was" "${cmd:0:78}"
    elif [[ "$was" == ALLOW && "$got" != ALLOW ]]; then
        drift_tighten=$((drift_tighten + 1))
        printf '  收紧  第%d条 历史[放行] → 现在[%s] | %s\n' "$idx" "$got" "${cmd:0:78}"
    else
        drift_tighten=$((drift_tighten + 1))
        printf '  换号  第%d条 历史[%s] → 现在[%s] | %s\n' "$idx" "$was" "$got" "${cmd:0:78}"
    fi
    # 基线语义(第十七轮改):按"命令校验和"索引,只有**同一条命令的判定变了**才计罚。
    # 上一版把 `旧>新` 整体写进签名再 grep -x 整行,于是审计日志每追加一条新命令都会
    # 冒出一条"基线之外的新漂移",闸门必红(实测:语料没变,第二次跑就 rc=2)——
    # 那等于工具作废。新命令只是新语料,报数不报警;日志增长是常态,不是回归。
    key=$(printf '%s' "$cmd" | cksum | cut -d' ' -f1)
    CUR_SIGS="${CUR_SIGS:-}${key}"$'\t'"${was}>${got}"$'\n'
    if [[ -r "$BASE" ]]; then
        bl=$(grep -F "${key}"$'\t' "$BASE" 2>/dev/null | head -1 | cut -f2)
        if [[ -n "$bl" && "$bl" != "${was}>${got}" ]]; then
            NEW_DRIFT=$((NEW_DRIFT + 1))
            printf '  [判定漂移] 基线[%s] 现在[%s] | %s\n' "$bl" "${was}>${got}" "${cmd:0:70}"
        elif [[ -z "$bl" ]]; then
            NEW_CMD=$((NEW_CMD + 1))
        fi
    fi
done < "$ROWS"

if [[ -n "$GEN" ]]; then
    printf '%s' "${CUR_SIGS:-}" | sort -u > "$BASE" || fail_hard "基线写入失败:$BASE" 1
    printf '基线已写入 %s(%d 条)\n' "$BASE" "$(grep -c . "$BASE" 2>/dev/null || echo 0)"
elif [[ ! -r "$BASE" ]]; then
    # 缺基线时**红着退出**,不许报"全绿":没有对照物就说"没有回归"是第十五轮已经
    # 批评过的假绿口径。
    printf '无基线文件 %s:先 GEN=1 跑一次固化当前状态。缺基线一律红,不报全绿。\n' "$BASE"
    NOBASE=1
fi

echo "── 历史认领分布:"; for k in "${!WAS_HIST[@]}"; do printf '   %-6s %s\n' "$k" "${WAS_HIST[$k]}"; done | sort
echo "── 当前判定分布:"; for k in "${!GOT_HIST[@]}"; do printf '   %-6s %s\n' "$k" "${GOT_HIST[$k]}"; done | sort

printf '\n一致 %d / 放宽可疑 %d / 收紧或换号 %d / 总计 %d / 有损未参与 %d / 基线外新语料 %d / 判定崩溃 %d\n' \
    "$same" "$drift_relax" "$drift_tighten" "$idx" "${lossy:-0}" "$NEW_CMD" "${jerr:-0}"
if (( ${#LOSSY_H[@]} )); then
    echo "── 有损原因(一条行可同时命中多类):"
    for k in "${!LOSSY_H[@]}"; do printf '   %-14s %s\n' "$k" "${LOSSY_H[$k]}"; done | sort -k2 -rn
fi
if (( ${jerr:-0} )); then
    printf '⚠ 有 %d 条命令让判定器崩溃(未参与对账)。崩溃不是放行,必须单独查根因。\n' "${jerr:-0}"
fi
if (( NOBASE )); then
    printf '结论:无基线可比,按 fail-closed 退出 1。\n'
    exit 1
fi
if (( NEW_DRIFT )); then
    printf '结论:同一条命令的判定发生变化,基线之外 %d 条,必须逐条确认(尤其"历史拦→现在放行")。\n' "$NEW_DRIFT"
    exit 2
fi
printf '结论:无新增漂移(语料 %d 条一致,历史漂移已固化进基线)。日志增长后先复跑再 GEN=1 固化。\n' "$same"
exit 0