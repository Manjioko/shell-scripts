#!/bin/bash
# 把回归表的**某一段**摘出来,在同一套装载器下单独跑。
#
# 为什么要有这个工具:本轮全表跑出"cd - && cat .npmrc 判放行"(应 H6),看起来像判据
# 出了个新洞。三种可能必须区分开:①这段向量自己写错;②前面某段留了全局态污染它;
# ③被测源码根本不是我以为的那份。摘出来单跑一次就能三选一定死 —— 实测结论是 ③:
# 变异脚本被 120s 墙钟打断、finally 的还原没执行,源码停在突变态。
# 没有这个手法,我会去"修"一个不存在的判据 bug,顺手把真的那次误报写进提交说明。
#
# 用法:
#   tests/isolate_section.sh T26 [被测源码,默认 wchat_ref]
#   退出码:0 = 这段独立跑全绿;1 = 这段自己有问题(或它的依赖没进装载器)
#
# 读结果的正确姿势:
#   单跑绿 + 全表红  → 污染来自前置某段(去二分前面哪段留了全局态)
#   单跑红 + 全表红  → 这段向量或判据本身的问题,与顺序无关
#   两边数字不同时   → 先比对 RESULT 行的 src= sha,确认被测的是同一份代码
set -u
cd "$(dirname "$0")/.." || exit 1          # 仓库根:表里所有相对路径都以它为基准

TAG="${1:-}"
SRC_ARG="${2:-wchat_ref}"
[[ -n "$TAG" ]] || { echo "用法: $0 <段标签,如 T26> [被测源码]"; exit 2; }
TABLE="test_hardwall.sh"
[[ -r "$TABLE" ]] || { echo "找不到 $TABLE"; exit 2; }
[[ -r "$SRC_ARG" ]] || { echo "找不到被测源码 $SRC_ARG"; exit 2; }

# ── 找段的边界:段起点是它的 banner(`echo 'T26 …'` 或 `echo "T26 …"`),
#    终点是下一个 banner 或总数守卫;装载器+助手 = 第一个 banner 之前的全部行。
START=$(grep -nE "^echo [\"']${TAG}([[:space:]]|$)" "$TABLE" | head -1 | cut -d: -f1)
[[ -n "$START" ]] || { echo "表里找不到 $TAG 段的 banner(grep 的是行首 echo 'T${TAG} …')"; exit 2; }
HEAD_END=$(( $(grep -nE "^echo [\"']T[0-9]+([[:space:]]|$)" "$TABLE" | head -1 | cut -d: -f1) - 1 ))
END=$(awk -v s="$START" -F: 'NR>s && /^echo ["'\'']T[0-9]+([[:space:]]|$)/{print NR-1; exit}' "$TABLE")
[[ -n "$END" ]] || END=$(grep -n '^total=\$((pass + fail))' "$TABLE" | head -1 | cut -d: -f1)
[[ -n "$END" ]] && (( END > START )) || { echo "$TAG 段没找到结尾"; exit 2; }

TMP=".isolate.${TAG}.$$.sh"
trap 'rm -f "$TMP"' EXIT
{
    sed -n "1,${HEAD_END}p" "$TABLE"
    # ③ 曾经在这里加过"把全表所有顶层函数再抽一遍"(想把用 pd/pdn 的段也支持掉),
    #    结果装置自己变成了假红源:某个函数的收尾不是行首 `}`,awk 的 p 一直是 1,
    #    把后面区段的**内联代码当函数体吞进去并当场执行**,跑出 `_tw_tmp: unbound variable`,
    #    连本来 25/25 绿的 T26 都被它报成红。所以这里不做任何"补齐依赖"的聪明事:
    #    装载器前缀(含 expect/deny_rule)+ 本段自身,就是本装置的能力边界。
    #    段里若用了前缀之外的助手,明确拒绝并说明,绝不给一个看起来像结论的红。
    if grep -qE '^[[:space:]]*(pd|pdn)[[:space:]]' <<< "$(sed -n "${START},${END}p" "$TABLE")"; then
        printf 'isolate 拒绝:%s 段用了定义在测试区里的助手(pd/pdn 等),本装置只带装载器前缀,\n' "$TAG"
        printf '  继续跑只会给出"看不出所以然的红"而不是结论。要隔离这种段,请手工把该助手的\n'
        printf '  定义行一并带上,或直接在整表里加/减用例做二分。\n'
        exit 3
    fi
    printf '# ── 以下是 isolate 注入的三行修补(缺任何一条都会跑出看不出所以然的红)──\n'
    # ① EXPECT_TOTAL 的基准赋值在测试区里(被我们跳过了),不补就 set -u 当场炸,
    #    现象是"一行输出都没有" —— 与装载器坏了的表现一模一样,极易误判。
    printf 'EXPECT_TOTAL=0   # 隔离副本只跑本段\n'
    # ② 表里自我检查用的是 $0 的路径(见 T25 的注释),副本会指向自己 → 结构守卫
    #    去 grep 一个内容不同的文件。指回真表,守卫测的才是真东西。
    printf 'HW_SELF="$PWD/test_hardwall.sh"\n'
    sed -n "${START},${END}p" "$TABLE"
    printf 'printf "ISO %s pass=%%s fail=%%s\\n" "$pass" "$fail"\n' "$TAG"
} > "$TMP" || { echo "生成副本失败"; exit 2; }

echo "isolate: $TAG 段 = 表第 ${START}-${END} 行(装载器取 1-${HEAD_END})"
OUT="$(bash "$TMP" "$SRC_ARG" 2>&1)"; rc=$?
printf '%s\n' "$OUT"
rc=$?
# 三档结论。第二档是今天挣出来的:我把副本改放进 $TMPDIR 后,装载器按 dirname $0 去找
# wchat_ref 找不到,段里一条判定都没跑出来 —— 而装置当时说的是"本段单独跑就有问题"。
# "跑不出汇总行"是**不可判定**,不是红。把工具自己的故障报成被测对象的结论,正是本文件
# 开头警告的那件事,所以这一档必须由代码保证而不是靠我记得。
if grep -qF "ISO $TAG " <<< "$OUT"; then
    [ $rc -eq 0 ] && echo "isolate: 本段独立绿(与用例顺序无关)" \
                  || echo "isolate: 本段有 fail,是真结论(与用例顺序无关)"
else
    echo "isolate: 不可判定 —— 没跑到汇总行(rc=$rc),副本没能执行到本段末尾,别拿它下结论"
    rc=4
fi
exit $rc