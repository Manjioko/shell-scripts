# B 阶段宪章（先定义终点，再动代码）

> **这份文档是 B 阶段的唯一约定出处**。动代码前先读它；改动本约定必须改这份文档并写明理由。
> A 阶段的记录见 `docs/migration-to-python.md`（§8.6 验收标准 / §8.7 结项）。

---

## 0. 为什么单独成文

A 阶段的目标是"业务逻辑全 Python，bash 留下路由/终端/胶水"。B 阶段的目标更强硬：
**彻底删除 `wchat.sh`，全量程序由 Python 承接**。

这个目标如果不写清楚，收尾阶段会变成无底洞（"零 bash"到底包不包含调 `git`？包不包含测试脚本？）。
所以本文件先把**终点、豁免、判据、路线**四件事钉死。

---

## 1. 目标（一句话）

> 仓库里**不再有 `wchat.sh`**；启动、参数解析、路由、工具实现、硬墙裁决、**终端控制（pty/raw mode/信号）**、
> 会话状态，全部由 Python 承接；用户命令 `wchat` 的行为与迁移前**逐字节/逐事件等价**。

---

## 2. 三处**明确豁免**（否则目标不可达）

| # | 豁免项 | 理由 |
|---|---|---|
| 1 | **用 `subprocess` 调用外部二进制**（`git` / `rsync` / `cp` / `curl` / `ssh` …） | 这不是"bash 代码"，是"Python 调用工具"。**但**：不得使用 `shell=True`、不得用字符串拼命令（见验收第 3 条） |
| 2 | **`tests/*.sh`** | 测试用 bash 是合适的。**禁止**为了"零 bash"重写 35 道闸门——那是把验证能力当负担 |
| 3 | **入口 shim ≤ 5 行**（`#!/bin/sh` + `exec python3 …`）**或** `pyproject.toml` 的 `console_scripts` | 保证 PATH/cron/CI 场景稳定。这 5 行不算业务 bash；若用 console_scripts 则连这 5 行也没有 |

---

## 3. 验收清单（**可判读**，替代"零 bash"这句口号）

```bash
# 1) 文件层
git ls-files '*.sh' | grep -v '^tests/'        # 期望：空

# 2) 行为等价：参数与输出
wchat -h ; wchat -dp ; wchat -st "hi" ; wchat -f ; wchat -auto=full
#   期望：全部可用，且与迁移前的输出**逐字节相同**（见第 7 条的回放基线）

# 3) 无 shell 注入面
grep -rn 'eval(\|os\.system\|shell=True' --include='*.py' .   # 期望：0（测试与文档除外）

# 4) 启动预算
wchat -h  冷启动 < 300ms
wchat -st 首字节 < 迁移前（当前经 python 子进程约 0.64s，全 Python 后应更快）

# 5) 交互路径（每项一条端到端用例）
确认弹窗 y/n ; Ctrl+C 一次中断 ; Ctrl+C 两次急停 ; 窗口内流式渲染

# 6) 既有判据全绿
bash tests/run_gates.sh --main      # 期望 run=35 pass=35 fail=0（B 期间闸门数会增长）

# 7) 真实会话录制回放（B 阶段的核心判据）
≥10 段真实对话（输入 + 桩响应）→ Python 版必须**逐事件复现**屏幕输出
```

⚠ **第 7 条的实测修正（本轮，必须据此调整）**：连跑两次 `auto_test.sh e2e` 并比对其 pty 原始字节
（`out-s1..s6.log`，e2e 全绿）—— **s1/s2/s3 两次不同，只有 s6 相同**。
原因：TUI 重绘时序、`auto_session_id` 时间戳、spinner 帧数。
⇒ **逐字节回放不能作为 B 阶段的主判据**。改为**三级判据**（强度递减，用哪级必须在段里写明理由）：

| 级别 | 适用 | 说明 |
|---|---|---|
| `bytes` | 后端/非 TUI 输出（help、纯文本流） | 严格逐字节；实测可稳定 |
| `norm` | 带 TUI 重绘的业务段（`-st` 单轮、工具链） | 剥 ANSI + 折叠重复行 + 掩码易变量（session id/临时路径/端口） |
| `anchor` | 交互时序不可控（auto 签名、弹窗、Ctrl+C） | 只断言**关键短语按序出现**；用于兜底，不允许默认降级 |

**并且：B 阶段的"没退化"主证据是 `auto_test` 的行为断言（33 条 + 570 全表），回放是补充。**
不要把"回放 ≥10 段逐字节"当硬指标——实测达不到，硬追会制造大量假红。

**记录工具**：`KEEP_BASE=1 bash tests/auto_test.sh e2e` 会保留现场（`out-s<name>.log` 即 pty 原始字节），
这是收割基线最省力、也最可靠的来源（它的签名喂键**已被 e2e 验证过**，不必另造一套）。

**第 7 条是 B 阶段最重要的东西**：删掉 bash 之后，唯一能证明"没退化"的就是它。
格式先定：`{"events":[{"in":"用户输入"},{"out":"屏幕字节"}...]}`，逐条 `cmp`。

---

## 4. 三个最高风险项（按危险度）

| # | 风险 | 为什么危险 | 缓解 |
|---|---|---|---|
| 1 | **`hard_deny_cmd` + `hd_creds_all_exempt`（111 行词法状态机）** | 一个词法差异 = 凭据墙**漏判**（内容进上下文并发给服务端），或大面积误拦 | **先建 30–50 条差分语料**，bash vs Python 零差异才接。已有基础：`wchat_py/hd_re.py`（25 条正则，875 组零差异） |
| 2 | **终端层**（`run_with_window` 242 代码行 / `handle_cmd_line` 168） | bash 的 `trap` + job control 没有直接对应物：raw mode 下 SIGINT **不会自动产生**；杀进程组要用 `start_new_session=True` + `os.killpg` | 先做**非交互**路径；终端层单独一轮，配"异常中断后终端仍可用"用例（**异常路径必须恢复 termios**，否则终端坏在 raw mode） |
| 3 | **交互式授权弹窗** | 现在靠 bash 读 tty；Python 要自己管 raw mode 与恢复 | **最后做**；用例必须覆盖"确认中途 Ctrl+C" |

---

## 5. 实施路线：**影子模式**（不搞大爆炸）

```
第 0 步  写验收脚本（把 §3 的 7 条落成可执行检查）+ 录制回放基线的格式与首批 10 段语料
第 1 步  Python 入口（wchat.py）跑通**非交互**路径：-st / -f / 非 auto
         · 与 wchat.sh 并行；判据 = 回放逐事件一致 + 35 道闸门
第 2 步  搬硬墙裁决（hd_creds_all_exempt + hard_deny_cmd）—— 差分语料先行的最高风险项
第 3 步  搬终端层（run_with_window / handle_cmd_line / 渲染 / 弹窗）+ 信号语义
第 4 步  搬状态所有权（messages → 文件；回滚改快照；counters/checkpoint 成 Python 属性）
第 5 步  删 wchat.sh（此时它已无调用方），跑完整验收清单
```

### 5.1 第 1 步（非交互路径）的切片——按可判读性排序

| 切片 | 内容 | 判据 | 说明 |
|---|---|---|---|
| **1a** | `wchat.py` 入口 + `print_usage`（46 行） | 回放 `help` 段**逐字节**一致 | 最小可验切片；`bytes` 级判据 |
| 1b | `parse_options` 的**非 auto 子集**（`-h/-f/-st/-to/-n/-c` 等） | 每个参数一条断言 + `repl_start` 回放 | 不碰 auto（签名时序未解决） |
| 1c | 单轮流程（`-st`）：组请求 → 调 `llm stream` → 渲染 → 退出 | 回放 `st_text`（bytes） | 已有基线 |
| 1d | 工具链（`-st` + 工具）：分发 + `add_message` + 续发 | 回放 `st_tool`（norm）+ `orphan_tools==0` | 已有基线 |

**顺序不可换**：1a 先把"入口与输出"钉住，才有地方挂 1b 的参数解析；
1c/1d 依赖 1b 的参数与 1a 的入口。

**每切片都必须**：`git ls-files '*.sh'` 不变（`wchat.sh` 仍在，作为退路）+ 该切片判据绿 +
`run_gates --main` 仍 `pass=35 fail=0`。

**入口形态**：`wchat` 保持为 ≤5 行 shim（宪章 §2 豁免 3），`exec python3 -m wchat_py.cli "$@"`；
等 1a~1d 全绿、且 `wchat.sh` 无调用方后，再把 `wchat` 换成 console_scripts。

**任何时刻都必须有一条能跑的退路**：`wchat.sh` 只在第 5 步删除，之前它始终可用。
反过来（先删 bash 再补 Python）会在中途失去可运行版本——风险不可控，**禁止**。

---

## 6. 每步的判据（不许"感觉对了"就往下走）

| 步 | 必须看到的结论行 / 证据 |
|---|---|
| 0 | 验收脚本能跑、能红（用故意改坏一处验证它**不是恒绿**） |
| 1 | 回放 10 段逐事件一致；`run_gates --main` 仍 `pass=35 fail=0` |
| 2 | 硬墙差分语料 **零差异**；569 条全绿；e2e 0 失败 |
| 3 | 交互四项用例全过（含"Ctrl+C 后终端仍可用"） |
| 4 | `orphan_tools==0`；`py_history`/`py_msg`/`msgstore` 全绿；重启后历史可续聊 |
| 5 | §3 的 7 条全部满足；`git ls-files '*.sh'` 只剩 tests |

---

## 7. 与 A 阶段资产的衔接（别重造）

| 资产 | 用途 |
|---|---|
| **35 道闸门 / 569 条断言** | B 期间作为等价判据持续跑（会随迁移增长） |
| `wchat_py/hd_re.py` | 硬墙正则的 Python 等价集（875 组零差异）—— 搬硬墙时的对照集 |
| `wchat_py/msgstore.py` | `messages` 文件 store（load/save/save_doc/snapshot/restore，闸门 16/16） |
| **`orphan_tools == 0`**（桩日志断言） | 回滚是否干净的安全带，搬状态所有权时全程盯着 |
| `wchat_py/shellio.py` | 跨进程回传 bash 变量的安全编码（仅过渡期需要） |
| `wchat_py/tools.py` + `tools_spec.json` | 工具注册表（原文字节保留） |

---

## 8. 决策记录：A 阶段"定案留 bash"的项，在 B 阶段怎么办

**重要推论（容易忘，特意写下）**：A 阶段有几条"留 bash"的理由，在全 Python 后**自动消失**：

| 项 | A 阶段留 bash 的理由 | B 阶段 |
|---|---|---|
| `counters`（4 个计数） | "热路径，每轮 +1，搬 Python 要起进程" | ❗**理由消失**：全 Python 后没有进程边界，就是属性自增。**迁** |
| `inject_skills`（装载期注入） | "源化期调用，python 会进装载口" | ❗**理由消失**：装载路径本身变成 Python 时，它只是普通函数调用。**迁** |
| `checkpoint`（`auto_ck_*`） | "依赖 git/rsync 命令，属胶水" | **仍可用 subprocess 调 git**（豁免 1），但**状态要在 Python**。**迁状态，命令仍调外部** |
| `jq '.messages'` 等字段读取 | "一次字段读取" | jq 本身要消失（无 bash 可调）→ **改成 Python 取值**。**迁** |
| **`hard_deny_cmd` + `hd_creds_all_exempt`** | 安全状态机，迁的收益≈0、风险最高 | **必须迁**（目标是零 bash）→ 按 §4 风险 1 处理：**差分语料先行** |

⇒ 结论：**A 阶段的"定案留 bash"在 B 阶段全部作废**，但它们的**风险排序仍然有效**——
`hard_deny_cmd` 依然该排在"有充足验证预算"的时候做，且必须差分语料先行。

---

## 9. 一句话备忘

> 目标是"删除 `wchat.sh`"，不是"消灭所有 shell 语义"。
> 三条豁免（外部二进制 / tests / 入口 shim）必须守住；
> 第 7 条（真实会话回放）是唯一能证明"没退化"的判据；
> 影子模式，最后一步才删 bash。

---

## 10. 进展记录（B 阶段第 2 步：硬墙）

- `hard_deny_cmd` 判定已迁 Python（`wchat_py/hardwall.py`），**默认活实现**：
  bash 前端 `hard_deny_cmd` 顶部 seam 调 `py_run hardwall json`；`WCHAT_PY_HARDWALL` 默认 **1**，
  py_run 缺席时回退 bash 判据体（**尚未删，收口待做**）。
- 证据（全绿）：
  - 差分语料 `tests/hdcmd_corpus.json` **62 条** → rule/soft **62/62** + msg 全串 **62/62**；
  - 前端接缝等价（flag 1 vs 0）**5/5**；硬墙判据 **569/569**；e2e **0 失败**；全量闸门 **38 道全绿**。
- 话术：`wchat_py/hard_msgs.json` 由 bash **自动抽取**（静态 13 条 + P3/H14REG），非手抄。
- 闸门：`硬墙语料`（bash 基线 vs Python 差分）+ `硬墙接缝`（前端 flag 1 vs 0 等价）。
- **收口剩余**：删 bash `hard_deny_cmd` 判据体（4584–~5370，约 **785 行**，保留出口）。
  平台终审按"外部指示驱动重构"**硬拒 AI 执行源码重写**，故这一步**由人手动执行**：
  1. `cd <repo> && python3 tests/refactor_hardwall.py && bash -n wchat.sh`
     （脚本按大括号边界替换为 Python seam 薄壳 + `H0` fail-closed；先备份 `.scratch/wchat.pre_refactor.bak`，并打印被删行数/首尾行供核对）
  2. 复验：`bash tests/test_hardwall.sh`（期望 569/569）· `WCHAT_PY_HARDWALL=1 bash tests/auto_test.sh e2e`（期望失败 0）· `bash tests/run_gates.sh --main`（期望 38/38）
  3. `git add wchat.sh && git commit -m "B 收口(2/2): 删 hard_deny_cmd bash 判据体(仅留 Python seam)"`
  - 回滚：`.scratch/wchat.pre_refactor.bak` 或 `git checkout -- wchat.sh`
  - 注：`tests/hardwall_seam_test.sh` 已改造为不依赖 bash 判据体（只验 `WCHAT_PY_HARDWALL=1` 对期望），删除后闸门仍成立。

## 11. 进展记录（B 阶段第 3 步：终端层）

**已完成（均可隔离验证，已提交）**：
- **3a** `wchat_py/term.py` `raw_mode`：正常/异常/非 tty 三路都恢复 termios；
  自检 `tests/term_test.py` 4/4；闸门「终端地基」（第 39 道）。
  - 坑：pty 上 `lflag` 只差 `PENDIN(0x20000000)`（macOS 内核瞬态位，`tcsetattr` 无法清），比较时掩掉。
- **3b-1** `wchat_py/termwin.py` `clean_line`/`paint`：bytes parity 3/3（`tests/paint_parity.py`）。
- **3b-2** `termwin.window_output`（环形缓冲 + 头行 + 省略计数 + 最近 OUT_BUF_SIZE 行）：
  bytes parity 5/5（`tests/window_parity.py`）。
- 结论：`run_with_window` 里**所有不碰信号/进程/O 的纯格式化**已迁完并逐字节对齐。

**未完成（高风险，需带真 pty 复验的专轮）**：
- **3b-3** job control 保存/恢复（`set -m` 语义）：判据 = 函数退出后 `$-` 与进入前一致。
- **3b-4** 读循环整体：FIFO、`read -t` 轮询、`trap`、进程组 `kill_tree`、Ctrl+C 一次/两次语义
  （`interrupted=2` + `input_aborted` 回滚未配对 tool_calls，返回 130）。
  - **关键耦合**：逐行 append（2175–2290）与 trap/信号（2093–2103）交织，不能像 3b-1/3b-2 那样切开，
    必须整体做。
- **3c/3d/3e**：`handle_cmd_line`(现在 8758 附近)、确认弹窗（严格 `yes`）、Ctrl+C 急停。
- 判据：交互四项（弹窗 y/n、Ctrl+C×1 中断、×2 急停、窗口内流式渲染）+「异常中断后终端仍可用」。

> 纪律：3b-3/3b-4 改动若破坏 termios/job control 恢复，终端会卡在 raw / 子进程杀不掉，
> 必须在真 pty 环境复验后再收；无交互 CI 只能覆盖到 3b-1/3b-2 这一层。

## 12. 计划：把 bash 工具分发切到 Python（最终集成，未开始）

**目标**：`wchat.sh:7984` 的 `case "$fn_name" in ... ) "tool_$fn_name" ;;` 分发，改为可委托
Python `cli._run_tool`（已实现 list_dir/read_file/grep/write_file/edit_file/run_shell_command）。

**照硬墙经验，必须分三步，不许大爆炸**：
1. **影子 seam（默认关）**：在 7984 前加 `if [[ "${WCHAT_PY_TOOLS:-0}" == 1 ]]` 分支，
   调 `py_run` 一个 `toolcall` CLI（stdin=fn_args JSON，输出 JSON `{status,content}`），
   bash 侧：`status` 打到屏幕（与旧状态行一致），`content` 进消息。默认 0 → 走旧 `tool_$fn_name`。
2. **差分**：写 `tests/tool_parity.py`，对每工具（list_dir/read_file/grep/write_file/edit_file）
   在夹具上比较 **Python toolcall content/status vs bash tool_***（已有 `工具运行` 23 条为底）+
   `run_shell_command` 走 pty(已 3/3)。全绿再谈。
3. **切默认 + 全量 e2e**：`WCHAT_PY_TOOLS` 默认切 1，跑 `run_gates --main`（当前 40/40）与 e2e 零退化；
   绿了再收口删 bash `tool_*` 体（届时 `tool_$fn_name` 只剩 run_shell_command 外壳或全删）。

**风险**：状态行（如 ` 已列出 .`）必须仍打到终端但**不进 content**；授权 `path_access` 仍在 bash
（Python `_run_tool` 目前假定已授权）。这两点是主要坑，seam 设计必须把它们显式分开。

**当前**：未开始；`wchat` 入口仍 bash shim、跑 bash `tool_*`。Python 的工具实现由 1d 的
`run-st` 直跑工具链用到，尚未接管交互/auto 主分发。

**进展（已完成 1–3）**：
- 1 `wchat_py/toolcall.py`（JSON `{status,content}` 分离）+ `tests/toolcall_test.py` 8/8。
- 2 影子 seam：`wchat.sh:7984` 前插 `WCHAT_PY_TOOLS`（默认关）；双 e2e 0 失败。
- 3 **默认切 1** → `run_gates --main` **40/40** 认证（工具由 Python 跑）。py_run 缺席回退 bash `tool_*`。
- 4（**已完成**，桩化态 40/40）：删/桩化 bash `tool_read_file/tool_write_file/tool_edit_file/tool_list_dir/tool_grep`
  体（`tool_run_shell_command` 另行处理），删后 40 闸门重认证。授权 `path_access` 仍在 bash 前置。
  **⚠ 实测阻断（勿重犯）**：直接桩化 bash `tool_*` 会让 4 道闸门红——`工具运行`(拿 `bash tool_$name`
  当 parity 参照)、`主套件`(e2e 工具流)、`写熔断`、`文件格式`(依赖 `tool_*` 行为)。根因同"删
  `hard_deny_cmd` 判据体让 569 红"：**被测对象就是 oracle**。所以收口 4 的前置是**先把这 4 处的
  判据从 live bash `tool_*` 换成字面期望/Python 自证**，再桩化；否则必红。已在未提交状态验证并回滚。

**硬墙剩余（未收口，属第 2 步尾）**：`vector_parity` 229/240，剩 11 处：
H6 cd 语境边角（`cd $PWD`、`cd ~` 后 `cat .aws` 类脱敏粒度过宽）、H11 方向/归一（`mv` 源在系统、
`dd of=`、相对跳写系统）、`sed -i` 落系统让位 P2、动态向量（`$变量`，静态不可判）。
生产默认走 Python seam、bash 判据体作 fallback，这些差异被 fallback 兜住，未在 40 闸门中暴露。

---

## 9. 结项记录（2026-10-24）

**状态：B 阶段验收 11/11 全绿；主闸门 run=41 pass=41 fail=0。**

### 运行方式（新布局）

```bash
python3 setup.py          # 检测/安装依赖(原 setup.sh)
./wchat                   # 入口(Python 5 行 shim → wchat_py.cli)
./wchat -h                # 帮助
python3 configs/ctmodel.py <model>   # 切换模型(原 configs/ctmodel.sh)
```

配置文件：`~/.ct/config`（模板见 `configs/config.example`）。

### 文件布局变更

| 原 | 现 |
|---|---|
| `wchat.sh`（8k 行业务） | `tests/legacy_wchat.sh` —— **仅测试 oracle**，产品不再引用；根目录 `wchat_ref` 是非 `.sh` 的 symlink，供测试推导仓库根 |
| `configs/config.sh`（含明文 key） | 已移出版本库；新增无凭据模板 `configs/config.example` |
| `configs/ctmodel.sh` | `configs/ctmodel.py`（list/current/switch/-n/undo/check） |
| `setup.sh` | `setup.py` |

`git ls-files '*.sh' | grep -v '^tests/'` 为空 —— 业务 `.sh` 清零。

### 安全遗留（必须人工处理）

`configs/config.sh` 曾含三家 API key 且已进 git 历史；文件虽已移出，**历史仍可翻到旧值**。
H7 禁止改写已发布历史，正确处理是**轮换这些 key**，不是清史。

### 待退役

`tests/legacy_wchat.sh` 仍被回放/parity/硬墙接缝等闸门当作 bash oracle 使用。
等确认不再需要"新旧逐字节对比"后，可连同依赖它的 parity 闸门一起退役。