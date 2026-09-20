# 回归闸门清单(名字与命令以此为准,别再凭印象编)

这轮踩的坑值得钉在纸上:上下文压缩后我凭印象造出一个从未存在的
`tests/hardwall_table.sh`,认定"517 条断言的表消失了、且从未入库",连着查了
好几轮(找文件、查 git 历史、翻备份目录、怀疑被哪个测试越界 rm),最后发现
**517 那张表就是仓库根的 `test_hardwall.sh` 本体** —— 它的 `expect H5 '...'`
表体一直在,还有 `EXPECT_TOTAL` 专门守住"断言总数对不上就 FAIL",结论打在
`RESULT total=… pass=… fail=… expect=… src=<sha>` 这一行里。文件 tracked,
什么都没丢。所以以后:报"资产丢失"之前,先按**输出行的指纹**去 grep,不要按
记忆里的文件名去找。

| 闸门 | 命令(都用 `/opt/homebrew/bin/bash`,产品要求 bash 5+) | 我看过的真实结论 |
| --- | --- | --- |
> 基线数于 2026-08-07 从 **517 重固化为 527**:新增第十八轮 H13(写目标不可解析)6 条拦截 + 3 条反锁 + 1 条"上下文三数关系"结构锁。
> 那次全表跑不出 RESULT 的根因不是判据坏了,是 harness 变量抽取 `grep -vF '$('` 把 `$((…))` 算术派生
> 一起排除在外,夹具里 TOKEN_THRESHOLD/CTX_OVERFLOW_GATE 根本不存在 —— 详见该文件第 74 行注释。

| 全表断言(即"517 那张表") | `bash test_hardwall.sh` | `RESULT total=… pass=… fail=0` + `PASS=… FAIL=0`;**慢**,几百条各起一次判定,请用后台通道跑,别在 3 秒无输出的前台管道里等 |
| 判据规则失败核查 | `bash tests/rulefail_check.sh` | `PASS=111 FAIL=0` |
| 形状探针(36 行表) | `bash tests/probe_shapes.sh` | 逐条 `ok H3/NP …` |
| 真实流量回放对账 | `bash tests/traffic_replay.sh` | `一致 25 / 有损未参与 74 / 判定崩溃 0`;更新基线:`GEN=1 bash tests/traffic_replay.sh` |
| 审计字段探针 | `bash tests/audit_field_probe.sh` | 3/3 通过(钉住 `auto_audit` 的 1200 上限、`trunc`/`tlen`、`root` 工作根、`pol` 生效判据版本) |
| 压缩布局探针 | `bash tests/compaction_pin_probe.sh` | 16/16 通过(**不联网络**钉住旧摘要锁定:`summary_pin_count` / `compact_apply`;含"失败硬截断也不许丢掉摘要"这条) |
| 审批链单测 | `bash tests/auto_test.sh` | `ck` 系列:缺 reason 直接 DENY 等 |
| 技能目录外置闸门 | `bash tests/skills_dir_test.sh` | SKILLS_DIR 覆盖/退回、三条目录校验、链接守卫三侧配对、`-skill` 的 name 清洗。负对照:`--source .scratch/base/wchat.sh`(未打补丁版应**拒跑**并报"沙箱漏抽") |
| 终端应答泄漏闸门 | `bash tests/tty_osc_input_test.sh` | 46 条:五类应答丢弃、真按键/粘贴正文一个不吞、DSR 按 CPR 配额放行、`input=` 接线、pty 里真起一次输入助手看交回文本。负对照:`HELP=.scratch/mut/pristine.py`(`git show HEAD:input_helper.py`)→ 报"沙箱漏抽";变量名是 `HELP` 不是 `HELPER`,传错会一路假绿(实测踩过) |

`tty_e2e_check.sh`(仓库根)是**要真人终端**的手动探针(测 bash 侧 `tty_*` 函数,判"不漏也不吞")。
`tests/tty_osc_input_test.sh` 与它不重复:后者自己开 pty、自己当假终端,无头环境也能跑,
测的是 python 输入助手这一侧。两个都要绿才算这条链闭合。

### 跑闸门前先看这条(本轮实测)

`tests/traffic_replay.sh` 用 PATH 上的 `bash` 起会**静默 exit 1、零输出**：本机
`command -v bash` = `/bin/bash` 3.2，装载区的 bash5 re-exec 守卫在"被 source"这个位置
`exec` 出去的子进程丢了父进程的管道状态，看着就像脚本自己死了。正确起法与本表所有条目
一致，写全解释器:

```
/opt/homebrew/bin/bash tests/traffic_replay.sh   # 一致 25 / 判定崩溃 0 → exit 0
```

辅助工具(不是闸门,别拿它们的绿当回归):`tests/mutate_call_site.sh`(变异检验,
看断言是否真的在看那行)、`tests/isolate_section.sh`(按标记抽函数)、
`tests/replay_audit.sh`、`tests/stub_gateway.py`(离线桩)、`tests/vision_*`。

## 三条硬约定

1. **新增回归脚本当轮就 `git add`**。只留在工作区的回归资产等于没有资产 ——
   这条本来是我准备用来解释"表丢了"的理由,结果发现自己连"丢"都是错的:
   真正丢的那类风险,是靠入库而不是靠记性来消的。
2. **总结里报数字,必须同时记下产生它的命令原文与文件名**。压缩之后残留的
   数字会挂在错的名字上,然后就会发生这轮这种事。
3. **别用 `grep '^PASS='` 判断套件跑完**(harness 1955 行已解释:那行可能带
   ANSI 前缀,行首不是 P)。判"跑完"要看 `RESULT …` 行里 `src=` 的 sha ——
   它同时能证明你测的是哪份源码(变异脚本被打断导致源码停在突变态,坑过一次)。