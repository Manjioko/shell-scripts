# wchat 从 bash 迁移到 Python3 —— 方案与执行台账

> 状态：**执行中**。本文件是唯一权威方案，迁移进度以文末「执行台账」为准。
> 目标读者：改这份代码的 AI 与人。动手前先读完「一、铁律」与「三、接缝契约」。

---

## 〇、背景与判断依据

`wchat.sh` 现状：**10183 行 / 186 个函数**，单体 bash 5.x 脚本；已有两个 Python 组件
（`input_helper.py` 651 行、`render_helper.py` 92 行），接缝是「子进程 + stdout 回传 +
退出码语义」。当前分支 `feature/move_to_py`。

bash 力不从心的三处病根，**全在主干上**：
1. **共享全局**：`messages`(出现 123 次)、`content`(86)、`fn_args`(35)、`inp_msg`(141)
   构成事实上的进程内协议，靠全局变量传递；无法做单元隔离。
2. **jq 拼装**：消息、注入段、审计字段全靠 jq 单行程序，改一处要对着引号数三遍
   （文件里到处是这类事故记录）。
3. **流式/终端状态**：`ask_ai` 的 SSE 循环与 `run_with_window` 的 pty、`stream_box_*`
   的转义序列、`stty` 状态交织在一起。

**由此得出一条重要预期**：按「由简到繁」迁移，前几波能立起**接缝规范与工具链**，
但**短期内不会缓解上述痛感**——真正的痛要等迁移推进到「核心循环」才解除。
价值在「先把地基铺平」，不在「立刻变快」。这一点必须先接受，否则中途会觉得没进展。

**环境硬约束（本机实测）**：`bash 5.3.15`、`python3 = /usr/bin/python3 → 3.9.6`。
迁移代码必须兼容 **Python 3.9**（禁 `match`、禁 `X | Y` 注解、禁运行时求值的
`dict[str, ...]`；需要时加 `from __future__ import annotations`）。

### 迁移终局口径（2026-10-14 用户拍板：**目标 A**）

用户确认：**按阻力最小、风险最小的方式先把「业务逻辑」全数迁到 Python**；终端层（pty/REPL/trap/入口）等**迁移完整落地后**再考虑进一步全面 python 化（即"目标 B"，把壳也 python 化，追求 bash 行数≈0）。据此：

- **近期（A）**：`wchat_py/` 是全部**逻辑**的权威实现；`wchat.sh` 逐步退化为「参数解析 + 路由 + 最小启动垫片 + 终端/进程胶水」。所有"纯逻辑/纯谓词"都要迁并收口。
- **`hard_deny_path` / `path_access` / `in_root_exempt` 之外的读全局态者**不是"永久留 bash"，而是**待迁**：它们依赖的全局态（审计、授权名单、auto 档位、熔断）与**核心循环（W12）**同批进 Python，届时随 W12 一起搬（铁律 4）。
- **终端/进程层**（`run_with_window`、`stty`/`stream_box_*`、`EXIT` trap/信号、tty 交互、顶层入口）：**A 阶段保留在 bash**（作为薄壳），**B 阶段**再用 `pty`/`termios`/`readline` 重写。
- 因此台账里凡写「留 bash」，一律应读作**「A 阶段暂缓 / 随 W12 或 B 阶段处理」**，不是永久排除。

---

## 一、铁律（本次立的规矩，优先级高于一切实现细节）

> 立项理由（用户 2026-xx 拍板）：迁移期最危险的状态不是「迁错了」，而是
> **bash 旧实现与 Python 新实现各自被调用、各自漂移、且都没人测** —— 这正是
> 本仓库反复出事的「同一条规矩抄三遍」同族。

### 铁律 1：一个功能，同一时刻只有一条「活的」实现

- 迁移完成的功能，**权威实现是 `wchat_py/` 里的 Python 模块**。
- 旧 bash 实现降级为**应急回退路径**，只在开关关闭时生效；它**不参与**正常调用。
- **禁止**出现「Python 与 bash 都执行」或「靠调用点各走一套、互不知情」的写法。
  一个函数体里，要么走 Python、要么走 bash，由**同一个开关**在同一处判断。

### 铁律 2：回退开关是「应急降级」，不是「并行实现」

- 总开关：`WCHAT_PY=1`（默认）→ Python 活实现；`WCHAT_PY=0` → 全量退回 bash。
- 分功能开关：`WCHAT_PY_<MODULE>=0`（如 `WCHAT_PY_SYSUTIL=0`）→ 只退这一个模块。
- 开关的取值判据**只写一份**（见「三、接缝契约」的 `_py_on`），任何函数不得自己再判一遍。
- 回退路径**必须**有：Python 不可用 / 模块导入失败 / 子进程异常退出时，不得让功能
  静默失效，也不得把半截输出抛给调用方 —— 要么落回 bash 旧实现，要么明确报错。
- **例外（用户拍板，2026-xx）**：**HTTP/SSE 层不建 bash 回退** —— 迁 Python(SDK) 即收口，
  `openai` 是必需依赖（缺失→明确报错，**不回退 curl**）。理由见 §8.5：迁移本意是"用更强的语言
  替代 bash"，长期留 curl 回退只是无谓的复杂度。其余模块仍按"并存期 parity → 收口"走。

### 铁律 3：收口 = 删旧路径（两阶段）

- **并存期**：Python 活实现 + bash 回退路径并存，靠开关切换；闸门必须断言
  「默认走 Python、关闭走 bash、两路输出一致」。
- **收口期**：模块稳定（连续若干轮闸门全绿、真实任务跑过）后，**删除 bash 旧实现**；
  此时 `WCHAT_PY=0` 的语义变为「该功能不可用并明示」，而不是「跑旧代码」。
- 收口本身也要过一次闸门（删了旧路径，断言须同步改成「旧路径不存在」的结构锁）。

### 铁律 4：迁移单元是「模块/接缝」，不是「单个函数」

- 不为了「从最简单的函数开始」而把 `stat_num` 这类 6 行热路径函数单独拆成一个进程 ——
  **每次跨进程约 40–60ms**，拆太碎是净亏损。
- 按**内聚模块**成组迁移：同一模块的函数进同一个 `wchat_py/<module>.py`，
  以子命令区分（`python3 -m wchat_py.sysutil stat_num …`）。

### 铁律 5：每迁移一个模块，同一轮就改它的闸门

- `tests/` 下 12 道闸门中 **9 道是 `_WCHAT_LIB_ONLY=1 source wchat.sh` 后按 bash 函数名
  断言的**。搬走函数 = 对应闸门变红或**变假绿**（后者更坏）。
- 迁移代码**未 push、未入库即视为未完成**（沿用 `tests/README.md` 的「四条硬约定」）。

---

## 二、全功能清单（按模块，含耦合度与波次）

行号为撰写时（方案 v1）坐标，仅作导航，不作为断言锚点。

| # | 模块 | 代表函数（行号） | 规模 | 状态耦合 | 复杂度 | 波次 |
|---|---|---|---|---|---|---|
| 1 | 系统/文本工具 | `stat_num`(33)、`clip_line_width`(524)、`truncate_output`(3316) | ~60 | 无（纯入参→输出） | 低 | **W1** |
| 2 | 平台探测 | `detect_os`(9593)、`img_mime`(7232)、`is_image`(7246) | ~30 | 无 | 低 | **W1** |
| 3 | 审批回复解析 | `auth_parse_verdict`(3849) | ~20 | 仅全局 `av_*`（可回传） | 低 | **W2** |
| 4 | 硬墙谓词链 | `hd_*(4759–6329)` 约 30 个纯字符串判据 | ~1500 | 纯函数（安全敏感） | 中 | **W2** |
| 5 | 路径归一/裁决 | `expand_tilde`(3952)、`norm_path`(4209)、`resolve_path`(3967)、`path_access`(6494)、`hard_deny_path`(6188) | ~500 | 安全敏感，与 #4 成对 | 中高 | **W3** |
| 6 | skill 模块 | `scan_skills`(577)、`inject_skills`(620)、`tool_read_skill`(8491)、`skill_save`(8520) | ~260 | `messages` + `content` | 中 | **W4** |
| 7 | MCP 客户端层 | `mcp_cli`(2365)、`mcp_extract_json`(2398)、`mcp_name_ok`(2347)、`mcp_tool_schema_live`(2864) | ~120 | `MCP_OUT/ERR`（可回传） | 中 | **W5** |
| 8 | MCP 壳层 | `mcp_boot`(2627)、`mcp_show_surface`(2550)、`mcp_catalog_text`(2780)、`tool_mcp_*` | ~500 | 权限/审计/UI | 高 | **W5b** |
| 9 | 图片/视觉纯逻辑 | `img_token_*`、`img_refs_*`、`img_inline_*`、`img_build_map`、`vision_*`、`describe_images`(7212–8490) | ~1200 | 部分全局引用表 | 高 | **W6** |
| 10 | 文件类工具 | `tool_read_file`(6827)、`tool_write_file`、`tool_edit_file`、`tool_list_dir`、`tool_grep` | ~160 | `content`/`fn_args` + 熔断状态 | 中 | **W7** |
| 11 | 网络工具 | `tool_web_fetch`(6981)、`tool_web_search`(7091)、`web_search_build_body`(7049) | ~240 | `content` | 中 | **W8** |
| 12 | 压缩/上下文 | `compact_due`(3353)、`summary_pin_count`(3365)、`compact_apply`(3394)、`compact_messages`(3422)、`make_request_body`(3560) | ~400 | `messages` | 中高 | **W9** |
| 13 | 历史/持久化 | `save_history`(1171)、`get_history`(1213)、`add_message`(3764) | ~300 | `messages` + 文件 | 中高 | **W9** |
| 14 | 交互/确认 UI | `normal_line`(9986)、`ask_read_line`(1143)、`inp_msg`(1166)、`_confirm_yes`(6423)、`confirm_cmd`(6438)、`parse_placeholders`(9941) | ~400 | tty/全局 `ask_line` | 高 | **W10** |
| 15 | 权限 auto 装配 | `auth_think`(3885)、`auto_enable`(6709)、`auto_final_judge`(6338)、`auto_checkpoint`(6655)、`policy_self_check`(4605) | ~600 | 全局 + 审计 | 高 | **W11** |
| 16 | 核心循环（主结构） | `ask_ai`(8989)、`printContent`(9335)、`run_with_window`(1925)、`kill_tree`(1776)、`stream_box_*`(3208–3311)、`tools`(2971)、`tool_run_and_parse`(8672)、`parse_options`(1526)、主循环(10133–10184) | ~1800 | 全部 | 极高 | **W12** |

> 注：用户举例的 skill / mcp / 流箱 分别落在 #6 / #7#8 / #16（`stream_box_*`）。
> 流箱**不是**边角 —— 它嵌在 `ask_ai` 的 SSE 循环里（调用点 9061/9074/9149/9158/9223），
> 与 curl PID、Ctrl+C、thinking 累积逐行交织，所以被排到 W12。

---

## 三、接缝契约（每个 `wchat_py/` 模块都必须遵守）

### 3.1 目录与调用

```
wchat_py/
  __init__.py        包标记；放"契约版本号"常量
  _contract.py       共享：退出码、stdin/stdout 助手、开关判据（**只此一份**）
  sysutil.py         W1
  textutil.py        W1
  ...
```
bash 侧统一入口（**只此一处**装载）：
```bash
py_run() { local _m="$1"; shift; python3 "$PY_HELPER_DIR/wchat_py/_run.py" "$_m" "$@"; }
```
**不用 `python3 -m`**：`-m` 会把 **cwd** 放进 `sys.path[0]`，用户项目里若有同名
`wchat_py/` 会遮蔽本包（生产上是"换个目录就加载到别人的模块"），还会让负对照失效
（实测：`tests/hardwall_py_test.sh` 的接线证明因此变假绿）。`_run.py` 按**绝对路径**
被调用，把包父目录插到 `sys.path` 最前，与 cwd 无关。

### 3.2 退出码（沿用 `input_helper.py` 的既有语义，不另立）

| 码 | 含义 | 调用方行为 |
|---|---|---|
| 0 | 成功，数据在 stdout | 使用 stdout |
| 1 | 业务性失败（入参非法/对象不存在） | 用 stdout 里的错误说明，不回退 |
| 2 | **环境异常**（缺 python3/依赖、无法工作） | **回退 bash 旧实现** 或明确报错 |
| ≥3 | 保留（按模块自定义，须在模块 docstring 写清） | 按模块约定 |

### 3.3 数据与日志

- **stdout 只放数据**（结果文本 / JSON），**stderr 只放日志/进度**；调用方用
  `$(py_run …)` 捕获 stdout，stderr 任其显示或丢弃。
- 多值结果用**单行 JSON**（`json.dumps(..., ensure_ascii=False)`）回传，bash 侧用 jq 拆；
  禁止用自定义分隔符（消息文本里什么字符都可能有）。

### 3.4 开关判据（唯一一份）

```bash
# _py_on <module>：1=走 Python 活实现，0=走 bash 回退。所有迁移函数只调它。
_py_on() {
    [[ "${WCHAT_PY:-1}" == "1" ]] || return 1
    [[ -n "${PY_HELPER_DIR:-}" ]] || return 1
    local flag="WCHAT_PY_${1^^}"; flag="${!flag:-1}"      # 分模块开关,默认开
    [[ "$flag" == "1" ]] || return 1
    return 0
}
```
（`${1^^}` 是 bash4+ 语法，本文件已全篇依赖，见脚本头部「bash 5 兜底」段。）

### 3.5 回退写法（模板）

```bash
some_fn() {
    if _py_on SOME_MODULE; then
        local _out
        _out=$(py_run some_module some_sub "$@") && { printf '%s' "$_out"; return 0; }
        # ↓ Python 侧异常(退出码!=0)→ 落到下面旧实现;绝不把半截输出抛给调用方
    fi
    # ---- bash 旧实现（回退路径；收口期删除） ----
    ...
}
```

---

## 四、波次计划（由简到繁）

每一波是一个**可独立入库、可独立回滚**的批次。开始下一波前，该波闸门必须全绿。

| 波次 | 范围（模块） | 迁移单元 | 判定价依据（闸门） |
|---|---|---|---|
| **W0** | 地基：包骨架 + 契约 + 开关 + `py_run` | `wchat_py/`、`_py_on` | `tests/py_channel_test.sh`（新建） |
| **W1** | #1 文本工具（`clip_line_width` → `truncate_output`） | `textutil` | parity：Python 输出 == bash 输出**逐字节**一致 |
| **W2a** | #3 审批解析 `auth_parse_verdict` | `authparse` | parity + `test_hardwall.sh` T19 |
| **W2b** | #4 硬墙谓词链 `hd_*` | `hd.py`（谓词） | `test_hardwall.sh`(569) + `rulefail_check.sh`(123)——**须先建"569 向量打 Python 路径"的装置** |
| **W3** | #5 路径归一/裁决 | `pathutil.py` | `test_hardwall.sh` 路径侧 + 成对断言 |
| **W4** | #6 skill 模块 | `skill.py` | `skills_dir_test.sh` |
| **W5** | #7 MCP 客户端层；#8 MCP 壳层 | `mcp_client.py`；壳层留 bash | `mcp_channel_test.sh` |
| **W6** | #9 图片/视觉纯逻辑 | `img.py`、`vision.py` | `inline_image_test.sh`、`vision_layer_test.sh` |
| **W7** | #10 文件类工具 | `filetools.py` | `write_fuse_test.sh` |
| **W8** | #11 网络工具 | `webtools.py` | `traffic_replay.sh`（回放语料） |
| **W9** | #12 压缩、#13 历史 | `context.py`、`history.py` | `compaction_pin_probe.sh`、`test_history_render.sh` |
| **W10** | #14 交互/确认 UI | `ui.py`（与 `input_helper.py` 合流） | `tty_osc_input_test.sh`、`tty_e2e_check.sh` |
| **W11** | #15 权限 auto 装配 | `authz.py` | `auto_test.sh`、`audit_field_probe.sh` |
| **W12** | #16 核心循环（动主结构） | `core.py`；bash 收敛为薄壳 | 全部 12 道 |

### 每波的固定动作（checklist）

1. **读**该模块全部函数与注释（注释里的事故史是判据的一部分，别跳）。
2. **写** `wchat_py/<module>.py`（自包含，import `_contract`），docstring 写清退出码。
3. **建** `tests/py_<module>_test.sh`：至少覆盖
   - 正向：若干向量，Python 输出 == 期望；
   - **parity**：同一向量下 Python 与 bash 旧实现输出逐字节一致；
   - **开关**：`WCHAT_PY_<MODULE>=0` 时确实走 bash（用可观测副作用证明，不是 grep 源码）；
   - **接线锁**：默认路径确实走 Python（用假 python 或埋点证明）；
   - 反例：非法入参不得静默成功。
4. **改** `wchat.sh`：定义函数按 §3.5 模板接线；**只加不减**（旧实现先留着当回退）。
5. **跑闸门**：本波闸门 + `tests/run_gates.sh`（至少语法、全表、对应专用闸门）。
6. **改台账**：更新文末状态；把新的结论行数字与命令原文记进去。

### 收口动作（模块稳定后）

- 删 bash 旧实现 → 改断言为「旧路径不存在」的结构锁 → 再跑一次本波闸门。
- 台账标记 `已收口`。

**收口进展（2026-xx）**：`webs` 是第一个收口样板——旧 `case` 回退已删，改断言为**函数体内**结构锁
（`fnbody <函数> | grep -c '回退路径' == 0` 等）+ 不可用降级断言；`tests/webs_py_test.sh` 随之改成
收口后形态（不再 py-vs-bash parity）。
⚠ **其余模块的收口被一个装置问题挡住**：它们的 parity 段都在 `tests/py_channel_test.sh` 里，而该文件
本会话已被**路径熔断**（改动过频）锁死。要对它们收口，得先**把主闸门改成数据驱动的向量表**（每个
模块一行向量，别每加一个模块就手改一段脚本）——这正是熔断要提示的重构方向。在那之前，`webs` 之外的
模块维持「并存期」。

**死结已解（下一会话）**：主闸门 `tests/py_channel_test.sh` 已**机械拆分**为「共享前奏 + 每模块一个 gate」——
`tests/py_prelude.sh`（沙箱/断言/`finish`）+ `tests/py_<模块>_test.sh`（各模块自己的向量）。
原有 221 条断言**一条不少**地分散进 11 个文件（合计仍 221，逐文件全绿）。`py_channel_test.sh` 自身缩为
「通道机制」4 条（开关/回退）。`run_gates` 末尾追加这 10 个新 gate。
**意义**：以后**增/删/收口**一个模块只动它自己的小文件，**不再反复改同一个大文件** —— 熔断的死结由此解开。
收口其余模块现在只需：删 `wchat.sh` 里该模块的 bash 回退 → 把 `tests/py_<模块>_test.sh` 从"py-vs-bash parity"
改成"语义 + 函数体内结构锁 + 不可用降级"（样板见 `tests/webs_py_test.sh`）。
⚠ 拆分方式：用 `sed -n '<段范围>p'` 从原文件**机械切**，不手抄（原文件先 `git checkout` 恢复、`cp` 快照后再切；
否则会像我第一次那样**中途覆盖源文件导致后续段全空**）。

**已收口**：`webs`（纯构造器样板）、`authparse`（fail-closed 谓词样板）、`textutil`（`clip_line_width`/`truncate_output`，不可用→原样透传）、
`skill`（`scan_skills`/`tool_read_skill`，不可用→scan 空、read 明示）、`img`（`img_reject_wording`/`img_inline_sides`，不可用→不判不收图 / 输出首档）、`compaction`（`compact_due`/`compact_apply`，不可用→不压 / 不动 messages）、`msg`（`add_message`/`auth_add_message`，不可用→不追加 + stderr 明示）。收口后该模块的 gate 一律是
「语义 + 函数体内结构锁 + 不可用降级」，不再 py-vs-bash parity。
⚠ **底向上的连带效应**：收口一个模块会**使依赖它的模块的 bash 回退失效**（如 `scan_skills` 的 bash 回退调用
`clip_line_width`，后者收口后 `WCHAT_PY=0` 变成透传，导致 `py_skill` 的 parity 红）→ 依赖方必须**就地一起收口**
（本例即由 textutil 收口连带收口了 skill）。**已收口 13 簇**：`webs`/`authparse`/`textutil`/`skill`/`img`/`vision`/`compaction`/`msg`/`history`/`filefmt`/`mcp_client`/`hd`（谓词层 + write_targets/noncode_payload/h11_scan）/`pathutil`（W3 归一 4 函数）。**W12 拆解已出（§八）；W12-1 配置打包已完成**（`wchat_py/config.py` + `py_cfg_dump` + `tests/py_config_test.sh` 16/16）；**W12-2 `hard_deny_path` 已收口**（`wchat_py/pathrule.py` + `tests/py_pathrule_test.sh` 25/25）。下一步 W12-3（会话态过渡，为 `path_access` 铺路）。**W12-3 已完成**（`is_granted`/`auto_allow_hit` → `wchat_py/authz.py`，会话态经 argv 传入；已收口）。下一步 W12-4（核心循环评估）。

---

## 五、闸门对应表（迁移期新增/改动）

| 闸门 | 命令 | 迁移期职责 |
|---|---|---|
| py 通道（**新建**） | `bash tests/py_channel_test.sh` | 开关/回退/parity/接线；W0–W1 |
| 全表断言 | `bash tests/test_hardwall.sh` | W2/W3 的判据回归（569 条；**默认走生产路**=真实 `_py_on`/`py_run`，`HW_PY=0` 才回退桩） |
| 判据出口话术 | `bash tests/rulefail_check.sh` | W2 出口数锁（111） |
| 技能目录 | `bash tests/skills_dir_test.sh` | W4 |
| MCP 通道 | `bash tests/mcp_channel_test.sh` | W5 |
| inline 图片 | `bash tests/inline_image_test.sh` | W6 |
| 写熔断 | `bash tests/write_fuse_test.sh` | W7 |
| 压缩布局 | `bash tests/compaction_pin_probe.sh` | W9 |
| webs 通道（**新建**） | `bash tests/webs_py_test.sh` | W8（`web_search_build_body` parity） |
| 硬墙双路（**新建**） | `bash tests/hardwall_py_test.sh` | W2b（569 向量 × Python 路径 + 负对照） |
| 视觉转述（**新建**） | `bash tests/vision_py_test.sh` | W6（`vision_build_body` / `vision_parse_resp` parity） |

> 新闸门一律**追加在 `run_gates.sh` 表格末尾**（别插在「主套件」前，见该文件注释里的索引陷阱）。

---

## 六、执行台账

| 波次 | 模块 | 状态 | 说明 / 结论行 |
|---|---|---|---|
| W0 | 包骨架 + 契约 + 开关 + `py_run` | ✅ 已入库 | `wchat_py/_contract.py`；`wchat.sh` 增「Python 迁移通道」段（`PY_HELPER_DIR`/`WCHAT_PY`/`_py_on`/`py_run`，刻意置于 `inject_skills` 顶层调用之前） |
| W1a | `textutil.clip_line_width` | ✅ **已收口** | Python 唯一活实现(旧 awk 回退**已删**);组件不可用 → **原样透传**(降级为不截断,不丢字节)。闸门 `tests/py_textutil_test.sh` **16/16**(语义 + 结构锁 + 透传降级) |
| W1b | `textutil.truncate_output` | ✅ **已收口** | Python 唯一活实现(旧 bash 回退已删);不可用 → 原样透传。实测坑:BSD `head`/`tail` **不补**末行缺失的换行 |
| W1c | `sysutil`（`stat_num`/`detect_os`） | ⬜ 未开始 | 薄模块，随首个真正需要它的波次合并做，不为它单起进程 |
| W2a | #3 `auth_parse_verdict` | ✅ **已收口** | 结果经单行 JSON 回传、jq 拆回 `av_*` 三全局。收口后：旧 bash 回退**已删**，`WCHAT_PY=0`/组件不可用 → **fail-closed 判 N**。闸门 `tests/py_authparse_test.sh` 改为收口后形态（**18/18**：语义 T19 + 函数体内结构锁 + 降级）。`tests/test_hardwall.sh` 已改为**默认走生产路**（真实 `_py_on`/`py_run`；`HW_PY=0` 才用回退桩）—— 收口后回退已删，回退桩模式对收口模块必红，只作调试口 |
| W2b | #4 硬墙谓词链（`hd_*`） | ✅ 已收口·编排保留(`hard_deny_cmd`) | **装置**：`tests/hardwall_py_test.sh`（A `HW_PY=1` 整表 569/569；B 弄坏 `hd.py` 必红＝接线证明）。**已收口**（8 个已路由函数，旧 bash 回退已删）：`hd_split_words`、`hd_mask_readonly`、`hd_mask_msg`、`hd_mask_search_pat`、`hd_fold_lines`、`hd_foreign_cred_path`、`hd_strip_noncode`、`hd_h11_dest_hit` → `wchat_py/hd.py`。**降级一律复刻"函数整体缺席"时的既有行为（不新增放行面）**：mask_* 原样透传（调用方二次确认照拒=fail-closed）、fold 原样进 HD_FOLDED、split 清空词表+rc1（其余调用方不记账/不豁免）、strip 置空+rc1（退回原始行）、foreign_cred_path/h11_dest_hit rc1。**已收口**：`hd_write_targets` → `wchat_py/hd.py write_targets`（旧 bash 回退已删）。**实测坑**：py 分支用裸 `$(...)` 捕获会吃掉结尾换行，而调用方是 `< <(hd_write_targets …)` 喂 `while read` —— 末行无换行时 `read` 返回非 0、**最后一个写目标被丢掉**，全表一度红 8 条；修法：非空时 `printf '%s\n'` 补回一个换行。**H12 调用点已同步改为按 rc 判降级**（不可用 → 含重定向的命令一律交人工确认，fail-closed）。闸门 `tests/py_hd_test.sh`（**98/98**：11 函数收口结构锁 + 降级 + `hd_write_targets`/`hd_noncode_payload`/`hd_h11_scan` 语义锚点）。**已收口**：`hd_noncode_payload` → `wchat_py/hd.py noncode_payload`（旧 bash 回退已删；降级 rc1=「无正文」→ 调用方保留原文，天然 fail-closed，无需改调用点）。**已收口**：`hd_h11_scan` → `wchat_py/hd.py h11_scan`（旧 bash 扫描回退已删；降级**退回调用点原本的"粗判据"兜底**：整串一次正则 + HD_CODE 置原文 → H11 宁多拦、H12 照常提目标，fail-closed）。**留 bash**：`link_escapes_root`（`[[ -L ]]` 热短路，整体迁会白付 spawn）、`hard_deny_cmd`/`hard_deny_path`（体量大、读全局多；`in_root_exempt` 已随 W3 迁走） |
| W3 | 路径归一/裁决 | ✅ 已收口·编排保留(`path_access`) | **已收口**：`expand_tilde`/`norm_path`/`resolve_path`/`in_root_exempt` → `wchat_py/pathutil.py`（旧 bash 回退已删；`os.path.realpath` 与 `cd+pwd -P` 实测逐字节一致，含 macOS firmlink；`in_root_exempt` 是纯前缀判定）。降级复刻"函数缺席"：`expand_tilde` 原样透传、`norm_path`/`resolve_path`/`in_root_exempt` 返回 1（调用方保守处理）。闸门 `tests/py_pathutil_test.sh`（**49/49**：语义锚点 + 结构锁 + 降级）。**留 bash**：`hard_deny_path`/`link_escapes_root`/`path_access`（读全局多、含 IO/审计/弹确认/symlink 解析） |
| W4 | skill 模块 | ✅ **已收口**(scan/read) | `scan_skills` / `tool_read_skill` 已**收口** → `wchat_py/skill.py`(旧 bash 回退已删);`WCHAT_PY_SKILL=0`/组件不可用 → scan 输出空、read 明示"组件不可用"。闸门 `tests/py_skill_test.sh` **18/18**;**`tests/skills_dir_test.sh` 已改**为收口形态(守卫改断言在 python 侧)。`inject_skills`(改全局 `messages`)留 bash;**待迁**:`skill_save`(messages + 交互 + curl) |
| W5 | MCP 客户端层 / 壳层 | ✅ 已收口·编排保留(`mcp_call`) | **已收口**：`mcp_extract_json`（从噪声里抽 JSON 主体，纯函数）→ `wchat_py/mcp_client.py`；旧 bash 逐行试起点回退**已删**，`WCHAT_PY_MCP_CLIENT=0`/组件不可用 → **按"未命中"处理（rc=1）+ stderr 明示**（调用方据此把 MCP 关掉，绝不伪造命中）。闸门 `tests/py_mcp_client_test.sh` 改为收口后形态（**22/22**：语义 + 结构锁 + 函数口降级）。**留 bash**：`mcp_cli`（进程/墙钟/中断）、`mcp_name_ok`/`mcp_clip`（超小热谓词）、壳层（权限/审计/上屏） |
| W6 | 图片/视觉纯逻辑 | ✅ 已收口·编排保留(`vision_*`/`tool_read_image`) | **已收口**：`img_reject_wording`、`img_inline_sides`（→ `wchat_py/img.py`，旧 bash 回退已删；不可用→不判不收图 / 输出首档）、`vision_build_body`/`vision_parse_resp`（→ `wchat_py/vision.py`，旧 bash jq 回退已删；不可用→ build_body **rc=1 不产半截体**、parse_resp **输出空串**，调用方各自报"构建失败/无转述文本"）。闸门 `tests/vision_py_test.sh` 改为收口后形态（**25/25**）；`vision_layer_test.sh`/`vision_e2e.sh` 已接生产路。**留 bash**：token 估算族（读标定表/`.fp`）、`prepare_image`/`img_build_map`/`img_refs_*`/`describe_images`。闸门 `tests/py_img_test.sh` **25/25**；`tests/inline_image_test.sh` **293/293** |
| W7 | 文件类工具 | ✅ 已收口·编排保留(`tool_read_file` 授权/打印) | **已收口**（`tool_read_file` 的纯格式化半 → `wchat_py/filefmt.py`；旧 bash 格式化回退**已删**，`WCHAT_PY_FILEFMT=0`/组件不可用 → **明示"读取组件不可用"、不返回半截/错行号内容**）；安全裁决（`resolve_path`/`path_access`）与 `fn_args` 解析留 bash。闸门 `tests/py_filefmt_test.sh` 改为收口后形态（**22/22**：语义 + 结构锁 + 工具口降级）。**既有隐 bug 如实复刻**：旧 `content` 头里 `\n\n` 是**字面两字符**（与 `tool_read_skill` 同款，闸门有断言钉住"等价复刻"，另开一刀再修）。**待迁/留 bash**：`tool_list_dir`（`find -type f` 的 lstat 语义易漂）、`tool_grep`（外部 grep 编排）、`tool_write_file`/`tool_edit_file`（熔断记账 + `content`） |
| W8 | 网络工具 | ✅ 已收口·编排保留(`tool_web_fetch`) | **已迁**：`web_search_build_body` + `tool_web_fetch` 状态码映射 → `wchat_py/webs.py`。**本模块已收口（方案铁律 3 的第一个样板）**：旧 bash `case` 回退**已删除**，`WCHAT_PY=0`/组件不可用 → 该功能**不可用并明示**（`build_body` 返回 1 / `fetch_error` 给通用归因）。**留 bash/待迁**：`tool_web_fetch` 的 curl/渲染器/截断、`tool_web_search`（发请求 + 解析 + 上屏）。闸门 `tests/webs_py_test.sh` 已改为**收口后形态**（语义 + 函数体内结构锁"旧回退确已删" + 不可用降级） |
| W9 | 压缩 / 历史 | ✅ 已收口·编排保留(`compact_messages`/`save_history`) | **已收口**：`compact_due` + `compact_apply`（→ `wchat_py/compaction.py`；不可用→不压 / 不动 messages）、`add_message`/`auth_add_message`（→ `wchat_py/msg.py`；不可用→不追加 + stderr 明示）、`history pack`/`drop_orphan_tools`（→ `wchat_py/history.py`；调用方 `save_history` 不可用→**不落盘** + stderr、`get_history` 不可用→**消息原样保留** + stderr）。**留 bash**：`summary_pin_count`、`compact_messages`、`save_history`/`get_history` 的非纯部分、`ensure_chat_title`。闸门 `py_compaction`(22)/`py_msg`(19)/`py_history`(27) 均已改收口形态；`compaction_pin_probe.sh` 迁移桩已路由 Python |
| W10 | 交互/确认 UI | ⬜ 未开始（A 阶段只切"判定流程"，渲染留 B） | **边界**：`confirm_cmd`/`auto_final_judge` 的**判定与流程**（问不问、问什么、结果如何落 `granted_*`）属 A；提示的**渲染/按键读取**（pty、`stream_box_*`、`input_helper`）留 B。**与 W11 的交界**：W10 产出"结论"，W11 负责"结论如何装配进权限状态"。 |
| W11 | 权限 auto 装配 | ⬜ 未开始（**与 W12 会话态重叠，需先对齐**） | **范围**：`granted_files`/`auto_allow_ro`/`auto_allow_rw`/熔断计数的**装配与生命周期**。⚠ W12 那一行把同一批状态写成"会话态随状态机迁"——**两者是同一件事的两半**：W11 决定"状态长什么样/谁改它"，W12 决定"它住在哪（bash argv → Python 状态）"。**执行顺序：先 W11 定形状，再 W12 4d 的第二/三批搬所有权**，否则搬完再改形状要搬两次。 |
| W12 | 核心循环（主结构） | ✅ A 阶段已收口(4a/4b/4c/4d);循环=路由留 bash;`hard_deny_cmd` 编排单独立项 | **前置拆解见 §八**：全局态分「启动期配置」（`hd_re_*`/名单/`AUTO_*`/根路径 → 打包 JSON 传入）与「会话态」（`granted_files`/`auto_*`/熔断 → 随状态机迁）。**W12-1 已完成**：`wchat_py/config.py`（schema + `keys`/`validate`/`get`）+ `wchat.sh` 侧 `py_cfg_dump`/`_cfg_arr_json`（bash 仍是单一出处，导出 JSON 供 Python 读）；闸门 `tests/py_config_test.sh`（**16/16**：合法/校验/键集合一致/值透传/空数组/负例/schema 与脚本一致）。**待做**：W12-2 `hard_deny_path` 纯判定化（141 行、无 IO，迁 `pathrule.py`）→ W12-3 会话态过渡 → W12-4 主循环。`path_access`（138 行编排：审计/弹确认/备份/熔断）留壳随 W12-3 迁。**W12-2 已完成**：`hard_deny_path` → `wchat_py/pathrule.py`（配置 + 会话态 `auto_mode`/`auto_allow_rw` 全经 argv 传入；`link_escapes_root` 一并 Python 化）；旧 bash 全量回退**已删**，降级=**fail-closed 粗判据**（只认凭据/系统路径字面量前缀 + 根内放行，不做归一）；闸门 `tests/py_pathrule_test.sh`（**25/25**：语义锚点 + 结构锁 + 降级），parity 期已由 15 向量 + 全表 569 验证。**W12-3 进行中**：`is_granted`/`auto_allow_hit` → `wchat_py/authz.py`（会话态 `granted_files`/`auto_allow_ro`/`auto_allow_rw` **全经 argv 传入**，单一出处仍在 bash；`pathrule` 的 P3 白名单判定改为复用 `authz.auto_allow_hit`，消灭第三份抄写）；parity 装置 `tests/py_authz_test.sh`（**19/19**：13 向量 py-vs-bash + 语义锚点 + 接线锁），全表 569/569。**已收口**：旧 bash 回退已删（降级 fail-closed：`is_granted` 返回 1=未授权、`auto_allow_hit` 返回 1=不在名单）；闸门改为收口形态 **20/20**。**W12-4a 已完成**：`wchat_py/llm.py`（官方 OpenAI SDK 适配 + seam JSON-lines 编码 + 依赖探测，**无 curl 回退**，缺失依赖即报错）+ `requirements.txt`（`openai>=1.0`）；闸门 `tests/py_llm_test.sh`（**19/19**；`openai` 未装时**明示跳过** SDK 流式实测，不假绿）。**W12-4b 待做**（`make_request_body` kwargs 化）；**W12-4c 已完成并收口**：`ask_ai` 的 SSE 读循环改为 `py_run llm stream` + JSON-lines 事件（`text`/`reasoning`/`tool_calls`/`usage`/`error`），**curl 旧路径已删、无回退**（HTTP/SSE 例外）；收口锁 2 条（`py_run llm stream` 存在 / 旧 `-sSN --connect-timeout` 为 0），闸门 `py_llm` **20/20 → 33/33**；代价 = 每请求 +0.64s 冷启动（实测），e2e 夹具 pty **总时限 30s→60s**（静默阈值 2.5s 不动）。**已知缺口（下一步就补）**：curl 的**停滞兜底**（`--speed-time`/`--speed-limit`）在 SDK 路径上没有等价物，`SSE_STALL_TIMEOUT` 已成死变量、`WCHAT_LLM_TIMEOUT` 从未被 export —— 服务端挂死时不再 120s 报错。详见 §8.5。 | |

---

## 七、风险与对策（一览）

| 风险 | 对策 |
|---|---|
| 闸门 source 主脚本按函数名断言 → 搬函数即假绿 | 铁律 5：同轮改闸门；断言必须「行为断言」而非 grep 源码 |
| Python 每次跨进程 40–60ms | 铁律 4：按模块成组，不迁碎；热路径函数与其所属模块一起走 |
| Python 3.9 语法限制 | 标 `from __future__ import annotations`；CI/闸门里 `python3 -m py_compile` 兜底 |
| 迁移中途两套实现漂移 | 铁律 1/2：同一处开关二选一；闸门 parity 断言 |
| 大字符串走 argv 撞 ARG_MAX | 沿用既有约定：入参走 **stdin 或临时文件**，不走 argv（见 `wchat.sh` 3523 段） |

---

## 八、W12 前置拆解：把"读全局的大块"搬进 Python

到 W2b/W3 收尾时，**纯逻辑基本清零**，剩下的都是**读全局态的大块**。本节是它们迁移的施工图（结论来自实跑清点，非推测）。

### 8.1 全局态分两类（决定迁移形态）

| 类 | 内容 | 性质 | 迁移形态 |
|---|---|---|---|
| **启动期配置** | `hd_re_*`（22 条正则）、`hd_cred_subdirs`、`hd_h11_sys_prefixes`、`auto_tmp_prefixes`、`AUTO_*`/`MCP_*` 常量、`WORK_ROOT`/`HOME`/`CT_HOME`/`HISTORY_DIR`/`AUTO_BACKUP_DIR` | 启动后**不变** | 打包成一份 **JSON 配置**，Python 侧读取；bash 仍是单一出处（顶层赋值），`py_run` 时传入 |
| **会话态** | `granted_files[]`、`auto_mode`/`auto_downgraded`/`auto_approvals`/`auto_deny_streak`、`auto_allow_ro/rw[]`、`auto_backup_count`、熔断写计数 | 运行中**可变** | 随"权限状态机"一起进 Python（过渡期先用 JSON 快照：单一出处仍在 bash，Python 只读） |

### 8.2 两个大块的定位（实跑数据）

| 函数 | 规模 | 依赖 | 结论 |
|---|---|---|---|
| `hard_deny_path` | 141 行 | 只设 `hd_rule`/`hd_msg`；**无 IO**；读 `auto_mode`(4) + 配置；调 `norm_path`/`in_root_exempt`/`link_escapes_root`/`hd_foreign_cred_path`（**均已是 Python**） | **纯判定** → 可迁为 `hard_deny_path(path, mode, auto_mode, cfg) -> {rule,msg}` |
| `path_access` | 138 行 | 编排：`resolve_path`×10、`auto_mode`×8、`auto_audit`×6、`auto_write_fuse`×4、`grant_file`×3、`ask_path_grant`（**交互**）、`auto_backup`/计数 | **含 IO/状态** → 留壳，随会话态（8.3-3）一起迁 |

### 8.3 施工步骤（每步独立过闸）

1. **W12-1 配置打包**：新增 `wchat_py/config.py`（读一份 JSON）；`wchat.sh` 侧加 `py_cfg_dump`（从现有顶层变量生成 JSON，单一出处仍在 bash）+ 一个 `tests/py_config_test.sh` 断言"JSON 与 bash 变量逐键一致"。
2. **W12-2 `hard_deny_path` 纯判定化**：迁到 `wchat_py/pathrule.py`，签名 `hard_deny_path(path, mode, auto_mode, cfg)`，返回 `{rule,msg}`；bash 侧包装回写 `hd_rule`/`hd_msg`。闸门：全表 569（路径侧）+ `py_hd_test` 加锚点 + 新 parity。
   - `link_escapes_root` 的 `-L` 热短路：本步一并评估——用 `os.path.islink` 但**先测成本**；若单次 spawn 让每个根内路径都变慢，则**有意留 bash**（判定看链接名/真身的强耦合）。
3. **W12-3 会话态过渡**：`granted_files`/`auto_*` 用 JSON 快照（单一出处仍在 bash）；为 `path_access` 的**判定部分**铺路（`is_granted`/`auto_allow_hit` 可先纯化）。
4. **W12-4 核心循环**：主 REPL / `ask_ai`（SSE 流式 + 终端状态）整体评估——**A 阶段终点**：业务逻辑全 Python，bash 只留"参数解析 + 路由 + 终端/进程胶水"。

### 8.4 闸门策略（沿用铁律 5）

每个 W12 步骤：**全表 569 + 主套件 + 新 parity/锚点** 三件套；降级方向一律 **fail-closed**（硬墙绝不因组件缺席而放行）；收口同前（删旧路径 + 结构锁 + README/台账同步）。


### 8.6 A 阶段的**验收标准**（先定义终点，再谈进度）

缺这条是之前反复"感觉还没完"的根源。A 阶段的目标原文是「业务逻辑全 Python，bash 只留
参数解析/路由/终端/命令胶水」——下面把它变成**可判读的清单**：

| # | 验收项 | 判读方式 | 现状 |
|---|---|---|---|
| 1 | **同一业务不再有两份实现** | 每个已迁函数在 bash 里都有 `_py_on <模块>` 且旧实现已删；模块闸门全绿 | ✅ 23 模块 / 35 道闸门 |
| 2 | **bash 剩余每一块都能归类**（参数解析 / 路由 / 终端 / 命令胶水 / 数据） | 按函数逐个归类，**不允许"说不清"** | ✅ 已归类（见下表） |
| 3 | **每一块保留都有闸门守护** | 该块被至少一道闸门的行为断言覆盖 | ✅ 除 `hard_deny_cmd` 编排内有 569 条覆盖 |
| 4 | **没有"进行中"的模糊状态** | 进度表里不再有"🟡 进行中"而无明确剩余项 | ⬜ 需把 🟡 逐条改成"已收口(编排保留)" |

**bash 剩余归类（实测代码行）**
| 类别 | 内容 | 依据 |
|---|---|---|
| 参数解析 | `parse_options`(187) | A 阶段明确保留 |
| **路由** | `tool_run_and_parse`(178) / `ask_ai`(167) / `get_history`(100) | **"路由"就是 A 阶段允许留下的东西**——它串 Python 能力与终端 |
| 终端 | `run_with_window`(242) / `handle_cmd_line`(168) / 渲染 / 确认弹窗 | B 阶段 |
| 命令胶水 | `path_access` / `hard_deny_cmd` 编排 / `checkpoint` / `counters` | git、rsync、审计、一次加减 |
| 数据 | `tools_spec.json`(已外置) / 提示词 / UI 文案 / 配置常量 | 不是逻辑 |

🟢 **`hard_deny_cmd` 迁移可行性评估（本轮实测，结论：可行，装置改动很小）**
- harness 机制（`tests/test_hardwall.sh` 85–108）：把源码里**按名字 eval 进测试 shell**
  （一个自动循环 `^hd_[a-z_0-9]+\(\)` + 一个显式名单，`hard_deny_cmd` 在名单里），
  然后 569 条断言**直接调用**这些 bash 函数。
- ⇒ 迁 Python 后**不需要重写抽取模型**：只要在名单处把 `hard_deny_cmd` 换成
  **一个 stub**（`hard_deny_cmd() { eval "$(py_run hd cmd "$@")"; }`），断言一行都不用改。
  **我先前说"必须同轮改测试装置、是大工程"是错的**——装置侧改动只有这一处 stub。
- 真正的难点是**结果回传**：`hard_deny_cmd` 通过 **bash 全局**通信
  （`hd_rule` / `hd_msg` / `HD_SOFT` / `HD_PAYLOAD`）。子进程无法设 bash 全局，所以
  Python 侧要把这些**编码成 shell 赋值语句**返回、由 stub `eval` 回去；`hd_msg` 是自由文本，
  必须严格转义（用 `printf %q` 或 base64）。
- 另一利好：它调用的 11 个 `hd_*` 谓词**都已路由到 Python**，所以 Python 版的
  `hard_deny_cmd` 是**同进程直接调**那些纯函数，没有 IPC 开销。
- **工作量估计**：221 行代码的直译 + 回传协议 + stub + 跑 569 条。**约一轮可完成**，
  且 569 条断言天然兜住每个规则分支（改错立刻红）。

⇒ **结论修正**：`hard_deny_cmd` 不该"单独立项拖着"，它是**一轮可做完的**，
建议作为 A 阶段真正的收尾项（做完 A 阶段就真的没有争议项了）。

⇒ **按此标准，A 阶段只剩一项真正的争议项**：`hard_deny_cmd` 的**编排半**(221 行代码，
谓词已在 `hd.py`)。它被 569 条断言"按名字从 bash 抽函数"的模型锁住，迁它必须同轮改测试装置。
**建议：把这一项单独立项，而不是算作 A 阶段未完成**——A 阶段的定义（逻辑在 Python、bash 留路由/胶水）
它已经满足。

### 8.5 W12-4 核心循环拆解（HTTP/SSE **用官方 OpenAI SDK**，不照搬 curl）

> **口径调整（用户拍板）**：迁到 Python 后，主/子请求的 HTTP+SSE **直接用官方 OpenAI SDK**
> （`from openai import OpenAI`），**不逐字照搬 curl** —— 各家都是 OpenAI 兼容端点，`extra_body`/
> `default_headers` 足以表达现有厂商特例。**代价**：`openai` 是**新运行时依赖**，必须在文档与启动检查里登记。

**SDK 映射（现有 → SDK）**：

| 现状（curl） | SDK 写法 |
|---|---|
| `BASE_URL` / `$API_KEY` | `OpenAI(base_url=..., api_key=...)` |
| `-H "api-key: $API_KEY"`（MiMo 兼容头，勿删） | `default_headers={"api-key": API_KEY}` |
| body `model/messages/stream:true/tools/stream_options{include_usage}` | `create(..., stream=True, tools=..., stream_options={"include_usage": True})` |
| `thinking:{type}` | `extra_body={"thinking": {"type": thinking}}` |
| `reasoning_effort`（带图+关思考→`"none"`，否则`"high"`） | 同名 kwarg（条件不变） |
| SSE 逐行解析 + `reasoning_content`/`tool_calls` 增量 | `for chunk in stream:` + `chunk.choices[0].delta`（厂商额外字段走 `delta.model_extra`） |

**降级（按用户拍板，2026-xx）**：HTTP/SSE 层**不保留 bash curl 回退** —— 迁完即**收口**（删旧路径），
`openai` 是**必需的运行时依赖**（启动时 `import` 探测，缺失 → **明确报错并给安装命令**，而不是退回 curl）。
理由：迁移的本意就是"用更强的语言替代 bash"，长期留一条 curl 回退只会增加复杂度与维护面。
（其余模块仍走"并存期 parity → 收口"的常规节奏；这里特指 HTTP/SSE 这一层不建 parity 并存。）

**范围边界（A 阶段）**：
- **迁**：`make_request_body`（纯 JSON 组装）、`ask_ai` 的 **HTTP/SSE 读循环 + 增量解析**（走 SDK）、`messages` 数据模型、轮次/branch 管理、`retry_after_compact` 自愈。
- **留 B 阶段**：终端渲染与 pty（`stream_box_*`/`stty`/`run_with_window`）、主 REPL 按键输入（`input_helper` 已是独立 python 进程）。理由：终端层是"壳"，用户已同意迁移落地后再做。

**接口缝（seam）**：Python 侧做 **HTTP/SSE 与增量解析**，把每个事件（文本增量 / `reasoning_content` 增量 / `tool_calls` 增量 / `usage` / `[DONE]`）以**一行 JSON** 写 stdout；bash 侧只读这些行做**终端渲染与累积**。这样"HTTP/SSE"一次性收进 Python（**不需要 curl 回退**），终端层留在 bash（B 阶段），缝是一份明确的 JSON-lines 协议。

**依赖登记**：`openai` 写入 `requirements.txt`（并补 README 安装段）；启动时探测，缺失即**报错并给** `pip3 install openai`（不静默、不回退）。

**步骤**：
1. **W12-4a 依赖与适配层**：`wchat_py/llm.py`（`OpenAI` 客户端封装 + `stream=True`）；启动时 `import openai` 探测；文档登记依赖。
2. **W12-4b `make_request_body` → Python**：纯 JSON；parity 对照 jq 输出**逐字节**。
3. **W12-4c `ask_ai` SSE 读循环 → Python（SDK 流）**：**已完成并按 HTTP/SSE 例外收口——不留 curl 回退**。
   `ask_ai` 每轮尝试改为 `py_run llm stream < "$req_file" > "$req_file_dir/fifo" 2>&1 &`（仍用 FIFO +
   显式后台 PID + `handle_ctrl_c` 的 TERM 逻辑，逐尝试重开 FIFO 读端以避开"复用 fd 立即 EOF"的老坑）；
   读循环由"解析 `data:` SSE 行"改为"解析 JSON-lines 事件"（`text`/`reasoning`/`tool_calls`/`usage`/`error`，
   `usage` 带 `cached`/`image`），渲染/重试/中断/收尾逻辑原样保留。`llm.py` 侧：`cmd_stream` 支持从环境取
   `BASE_URL`/`WCHAT_API_KEY` 与 `timeout`，`_emit` 逐事件 `flush`（否则 FIFO 会整段缓冲，前端表现为"卡死"）。
   收口锁：`tests/py_llm_test.sh` 结构锁两条（`py_run llm stream` 存在=1、旧 `-sSN --connect-timeout` 主请求块=0），
   闸门由 20/20 → **22/22**。

   ⚠ **代价（实测，勿当噪音绕过）**：curl 首字节 ~10ms，改用独立 python 进程后**每请求 +0.64s 冷启动**
   （量测 `.scratch/measure_firstbyte.py`，3 次稳定 0.642~0.650s）。这笔钱买的是官方 SDK 与可维护性，接受。
   但它击穿了测试夹具里一个**与实现无关的时间预算**：`tests/auto_test.sh` 的 pty 驱动器原以 30s 墙钟跑完每个场景
   （curl 时代够用），多请求场景现在跨过 30s → 应用被 SIGTERM、场景截断，表现为"请求数=0 / 某步没发生"（**flaky**：同一份代码两次跑出 6 红与 20 红）。
   单变量判别后确认：**静默阈值（2.5s = 假用户耐心）不动**，只把总时限 30s → 60s，e2e 即由 6~20 红转 0 红；
   断言一行未改。理由与实测值已写进 `tests/auto_test.sh` 该处注释。

   🔴→✅ **停滞兜底缺口（本轮已补）**：`SSE_STALL_TIMEOUT` 重新变成活变量——bash 侧派生
   `export WCHAT_LLM_STALL`（单一出处仍是它），`llm.py` 新增 `_build_timeout()` 用它构造
   **`httpx.Timeout(connect=15, read=N)`**（read timeout＝"无字节进展即判死"；**刻意不用总超时**，
   总超时会把健康长流一起砍掉）。闸门 `py_llm` 两条锁：`停滞兜底用 read timeout`、`未设则不传 timeout`。
   ⚠ 覆盖强度如实说明：这两条锁在**单元层**钉住语义（`Timeout.read == N`），**没有**跑"桩挂住不吐字节"
   的真实停滞 e2e；要补真实停滞用例，得给桩加一个 stall 模式，留作后续。原始分析留档如下。

   🔴 **原始分析（当时的缺口）**：curl 那条命令所带的**流式停滞兜底**
   （`--speed-time "$SSE_STALL_TIMEOUT" --speed-limit 1`：连接建立后平均速率 <1B/s 持续 120s 即自行断开、
   报 `curl: (28)` 进 `api_error`，防止无人值守时服务端静默挂死、读循环无限 pending）**在 SDK 路径上没有等价物**。
   证据：`$SSE_STALL_TIMEOUT` 全脚本零引用（只剩第 995 行的定义与注释），`WCHAT_LLM_TIMEOUT` 从未被 `wchat.sh`
   export（`llm.py:159` 读它但永远读不到）⇒ `cmd_stream` 不传 `timeout`，走 SDK 默认（约 600s 总超时）。
   注意**不能用总超时顶替**：`--speed-time/--speed-limit` 是"**无进展**即判死"，普通 `timeout` 会把健康的
   长流一并砍掉。正确等价物是 httpx 的 **read timeout**（`httpx.Timeout(connect=15, read=SSE_STALL_TIMEOUT)`）。
   补法（单一出处不变）：`wchat.sh` 由 `SSE_STALL_TIMEOUT` 派生并 export `WCHAT_LLM_STALL` → `llm.py` 用它构造
   `httpx.Timeout` 的 `read`；随后把只当注释用、已无引用的 `SSE_STALL_TIMEOUT` 收敛到这一处；
   再补一条**行为断言**（桩网关挂住不吐字节 → 必须在超时窗口内报错退出，而不是无限 pending），
   并按 §八 的收口清单同步闸门/README。

   🟠 **形状契约(本轮教训——同一个坑连踩四次)**：W12-4c 上线后用户实跑连抛四个错，根因**全在
   "bash 侧 jq 造的请求体 ↔ Python SDK 的 kwargs" 这条缝上**：两边对"形状"的隐含假设不一致。
   1. **凭据在哪**：`API_KEY`/`BASE_URL` 是普通 shell 变量、**从不 export**，子进程拿不到 ⇒ SDK
      `Missing credentials`。修：启动行显式 `WCHAT_API_KEY="$API_KEY" BASE_URL="$BASE_URL"`。
      （顺带修掉一个既有 bug：`API_KEY="${WCHAT_API_KEY:-}"` 无条件赋值，把第 94–110 行刚从
      `~/.ct/config` 读进来的 key **清空** —— 脚本自带提示"1) 写入配置文件(推荐)"那条路一直是坏的。）
   2. **URL 是根还是端点**：本项目 `BASE_URL` 是**完整端点**（带 `/chat/completions`），而 SDK 的
      `base_url` 是**根**、会再拼一层 ⇒ `.../chat/completions/chat/completions` ⇒ 404。
      修：`_normalize_base_url()` 剥掉已知端点后缀，根形状原样保留。
   3. **字段是对象还是标量**：请求体里 `thinking` 已是 `{"type": ...}`，python 侧又包一层 ⇒ 服务端
      serde 把内层 map 的键名当枚举变体 ⇒ 422 `unknown variant 'type'`。修：`_thinking_type()` 归一；
      **顺带修好一条从未生效的规则**——`thinking == "disabled"` 拿对象比字符串恒为假，于是
      "带图 + 关思考 ⇒ `reasoning_effort=none`"从未生效（注释里写着"实测 qwen 会 400"）。
   **为什么四道闸门全没拦住**：夹具的输入形状与生产**不同**——它 export 了密钥、传的是根 URL、
   桩只按 body 内容分类而**不校验字段形状**。于是"看不见的绿灯"连骗四关。
   已改：夹具改走生产形状（密钥只经 `~/.ct/config`、`BASE_URL` 用完整端点），桩日志增记
   `path`/`thinking_type`，并补**承重锁**（URL 正/反锁、`thinking.type` 未被双包）。闸门 `py_llm`
   20/20 → **33/33**。
   **结论**：这类问题的系统性解药恰好是 **W12-4b 的逐字节 parity**（把"形状契约"变成可执行的东西，
   而不是靠人记），故把 4b 提到停滞兜底之前先做。

   🟢 **W12-4b-1 交接（截至本轮：尺子已造好，尚未切）**
   - `wchat_py/request.py`：`build_body()` + `py_run request body`（stdin 收
     `{"model","messages","thinking","tools"}`，stdout 出请求体）。**与 jq 版逐字节等价**。
   - `tests/py_request_test.sh`（闸门 `请求建体`，**22/22**，已登记进 `run_gates`，闸门 31→32）：
     4 组语料（纯文本/带图 × thinking enabled/disabled）× 逐字节 `cmp` + 键顺序 + `thinking.type` 标量
     + `reasoning_effort` + Python 侧无终端转义。
   - **两个"并存锚点"（切换时要把期望反过来）**：`生产仍走 jq 建体` = **3**、`Python 建体尚无调用方` = 0。
   - ⚠ **jq 的调用点是 3 个，不是 2 个**：`wchat.sh:3692 / 8244 / 8706`（代码注释里写的"3617/8262 两处"
     已漂且漏了第三处）。**只切 ask_ai(8244) 会漏掉两条**——收口必须三个都过一遍。
   - ⚠ **别用 `grep -c 'request_body=$(make_request_body)'` 数调用点**：注释里有同款文本，会数出 5。
     要用 `grep -Ec '^[[:space:]]*request_body=$\{?\(?make_request_body'` 这类锚定行首的写法。
   - **切换的设计约束（不可退让）**：建体与发送必须**同一次 python 调用**（`cmd_stream` 内部建体），
     否则每请求多一次 0.64s 冷启动，e2e 墙钟会再爆（见上面"代价"一节）。
     落法：`llm stream` 增支持 `messages_file`（大 messages 走文件，保 ARG_MAX）与 `no_tools`，
     ask_ai 不再调 `make_request_body`，直接把 `model/thinking/no_tools/messages_file` 交给它。
   - **收口顺序建议**：先切 8244（热路径）→ e2e 验 → 再切 3692/8706（非热路径）→ 最后删 jq 的
     `make_request_body` 与三个**已死字段**（`reasoning_effort`/`stream`/`stream_options`），
     闸门转收口形态（`make_request_body() {` 计数=0 + 锚点期望反过来），跑全量对账取新 sha。

   🔴 **顺序修正（实测后改）**：**4b-2 必须先于 4b-1 的切换**。三条证据：
   1. `ask_ai` 的 `$1` 是**建好的请求体**（`local request_body=$1`），三个调用点
      （`3692/8244/8706`）都是 `request_body=$(make_request_body)` + `ask_ai "$request_body" ...`
      —— 切换是**入参契约变更**，不是局部替换。
   2. `img_token_sample` 对 body 的唯一依赖是
      `jq '[..|objects|select(has("image_url"))]|length' == 1`（只标定"恰好一张图"）。
      切换后 `messages_file` 里是 `[[IMGREF:id]]` **标记**、`image_url` 个数为 0 ⇒ 该函数
      在那一行**静默 return**，图片计费标定从此不再发生（不报错、不告警）。
   3. 因此正解是：**图片展开搬进 Python**（4b-2），由 Python 在展开后的 messages 上数
      `image_parts`，经事件（如 `{"t":"start","v":{"image_parts":N}}`）回传给 bash；
      `img_token_sample` 改成吃这个数字，彻底不再依赖请求体文本。
   **新顺序**：4b-2（展开 + image_parts 事件 + `img_token_sample` 改造，单独提交并跑全量）
   → 4b-1 切换（信封 + 三个调用点 + e2e）→ 删 jq/死字段/翻锚点 → 全量取 sha。

   🟡 **当前进度（截至 commit `ac2b9df`，可直接从这里接手）**
   已完成并各自验证：
   - `request.resolve_inputs()`：`messages_file`/`no_tools`/`ref_map_file`(展开) 的**唯一出处**，
     `llm.cmd_stream` 与 `request.cmd_body` 共用（`llm.py` 里那份重复实现已删）。
   - `make_request_inputs()`（bash）：只交输入 + `tmpdir`（临时文件由 ask_ai 在既有清理点
     `rm -f "$req_file"` 之后统一 `rm -rf`，**不能由调用点建** —— 否则每轮泄漏 MB 级文件）。
   - `img_token_sample` 改吃 `start.image_parts`（bash 侧不再解析请求体文本）。
   - 闸门：`请求建体` 31/31（六族展开 parity + 文件形态输入）、`py_llm` 33/33、
     `inline_image` 298/298、`e2e` 0 失败。
   **未完成（必须一次做完再提交）**：
   ① `wchat.sh:8438` 仍是内联的 `ask_ai "$(make_request_body)"` —— 第**四**个调用点，形状与
      另外三处（`request_body=$(…)`）不同，按单一形状 grep 会漏（已实测漏过一次）。
   ② `inline_image_test` 第 7 节 6 条断言把 **jq 建体当被测物**：其中"与 `git show HEAD:wchat.sh`
      逐字节相同"**必须换掉**（提交删除后 HEAD 里没有该函数 → 永远 red；职责已由六族 parity 承担），
      其余 5 条改成断言 Python 建体输出，并**重定 298 基线**。
      反例已实测：先删函数后跑，`inline_image` 从 298 掉到 291 —— **尺子依赖被测物**。
   ③ 删 `make_request_body` 本体 + 三个死字段（`reasoning_effort`/`stream`/`stream_options`），
      闸门加结构锁 `make_request_body() {` 计数 = 0。
   ④ 全量对账取新 sha + 台账。
   ⚠ 顺序固定：②→③（反了必红）；③做完才算满足铁律 1（现在 `8438` 一条活路径仍在用 jq）。
   ⚠ 不能顺手删 `img_build_map` / `_JQ_EXPAND_REFS`：展开虽归 Python，但"从磁盘收集图片引用"
     仍在 bash，`make_request_inputs` 用它产 `refmap.json`。删错 = 静默丢图。

   🔵 **W12-4d 侦察结论（`messages` 数据模型，下一步）**
   - 规模：`messages` 是 bash 全局 JSON 字符串，**11 处直接赋值**、2 个主入口
     （`add_message` 3675 / `compact_messages` 3411）、全文 115 处引用。
   - 赋值点按性质分四类，**迁移顺序应按这个来**（风险从低到高）：
     ① 初始化/重载：641、8950（新会话）、1241（从历史文件读）、1253（孤儿修复）
     ② 注入标记：636（skills）、2830（MCP）、5897（auto 能力清单）——都是"往 system 里塞一段"
     ③ 追加：`add_message` 3688
     ④ **回滚**：7943/8032/8050/8146（`messages=$msgs_before_call`，失败时回滚到调用前）—— 最危险，
        它们依赖"整份快照可复制"，换成文件存储后语义会变（要改成显式事务/快照）
   - **铁律（这一步的关键）**：过渡期 `messages` 只能有**一个所有者**。要么 bash 持有、传给人 Python
     （现状，即"信封"），要么**文件持有、bash 只拿路径**。绝不允许"bash 变量 + 磁盘文件两份"并存 ——
     那必然分叉，症状是我们这轮已经见过的那类：前缀被改写（token 从打折变全价）、图丢了却没人报错。
   - ⚠ **修正（核准代码后）**：`msg add` / `msg auth_add` / `compaction due` / `compaction apply`
     **已经是 Python**（W9 迁入）。所以 4d 剩下的**不是"迁操作"，而是"迁状态所有权"** + 五个仍是
     bash jq 的赋值点：
     · 三个**注入点**（636 skills / 2830 MCP / 5897 auto 清单）——形状完全一致（"往 system 里塞一段
       带标记的文本"），可合成一个 `msg inject <mark> <text>`，一处迁三个；
     · 两条**初始化**（641 / 8950 的 `jq -n '[{role:"system",content:"You are a helpful assistant."}]'`）
       → `msg init`；
     · **载入/孤儿修复**（1241 `jq .messages` / 1253 `messages=$orphan`）；
     · **四个回滚点**（7943/8032/8050/8146）→ 改成显式快照 id，语义要重新定。
   - 建议落地：`wchat_py/msg.py` 增 `inject`/`init`/`load`（纯函数，逐字节对照现有 jq 输出）；
     store 仍是 bash 全局字符串（现状），所有权切换单独一轮做，**不做"变量+文件"双份**。

   🔴 **切注入点的拦路石（实测，必须先定方案）**：`inject_skills` 在 **645 行被顶层调用**——
     也就是**脚本源化时就执行**（文件开头第 46 行的注释正是为此而写：py 助手的位置必须早于任何
     迁移函数的首次调用）。若把它改成 `py_run msg inject`，就等于**每个闸门装载 wchat.sh 时都会
     起一个 python 进程**：装载口变慢、且 python/依赖环境异常时会连带打坏**所有**闸门的装载。
     同时注意 **`inject_skills` 被调了两次**：源化时（645）与运行时 `system_inject_all`（2836）。
     注入本身幂等（`split(mark)[0]`），所以两者的语义重叠 —— 先要回答"源化时那一次到底为什么必需"
     （是否为了让 skills 在 parse_options 之前就进 messages？），再决定：
       (a) 删掉 645 的源化期调用、只保留运行时（行为可对照验证），或
       (b) 保留但**不迁**（把 `inject_skills` 列为"终端/装载胶水"，A 阶段留 bash）。
     ⚠ 不要为了"迁得干净"直接把 python 调用塞进源化路径——那是拿所有闸门的装载口冒险。
   ✅ **结论（已定）：走 (b) —— `inject_skills` 归为"装载胶水"，A 阶段留 bash，4d 不迁它。**
     依据不是偏好，是代码里已有的锁：`tests/mcp_channel_test.sh:711` 明确断言
     "顶层首次注入只调 inject_skills(源化时 inject_mcp 未定义)" —— 删掉源化期调用会直接撞红它；
     那条锁与 `wchat.sh:642-644` 的注释编码的是同一个不变量：**源化时 `inject_mcp` 尚未定义，
     所以该位置只能调 `inject_skills`**。同理，`inject_skills` 若改为 `py_run`，就必须在源化期起
     python 进程，等于把这个不变量换成"装载期依赖运行时"。
     ⇒ 4d 的迁移清单里划掉 636；`inject_mcp`(2830)/`inject_auto`(5897) 已切（运行时调用，无此约束）。

   ✅ **4d「赋值点」阶段收尾判断（本轮）**：剩余条目按**性质**归档，不再逐个迁：
     · `1253 messages=$orphan` —— **已经是 Python**（`py_run history drop_orphan_tools`，fail-open：
       组件不可用则原样保留并往 stderr 明示，符合"不静默丢消息"）。
     · `1241 jq '.messages'`、`has_reasoning` / `.thinking.type` 查询 —— 都是**字段读取胶水**，
       不是业务逻辑。为它们各起一次 python 进程只是把 bash 换成 IPC，收益为负 ⇒ **保留 jq**。
       判据：迁移的目的是消掉"同一业务的两份实现"，而不是消灭所有 `jq`。
     · **回滚点**（7943/8032/8050/8146 `messages=$msgs_before_call`）—— 不是迁移问题，是**语义问题**：
       现在靠"整份字符串可复制"实现回滚；换成文件 store 后必须改成显式快照 id，否则回滚会静默失效。
     · **状态所有权** —— 4d 的真正终点，单独一轮做。
   ⇒ 4d 可先以"注入与初始化已切、其余按性质归档"收尾；**回滚语义 + 所有权**是下一步的两件事。

   🔴 **回滚点零覆盖（实测）**：`tests/auto_test.sh` 的"10. 回滚点 checkpoint"测的是 **auto 的
   git/rsync 工作区快照**，与 `messages=$msgs_before_call`（7865 函数内 4 处：7949/8038/8056/8152）
   **完全无关**——消息回滚**没有任何断言**。
   现状机制：`msgs_before_call=$messages`（7931）整份字符串快照，工具被拒/失败后赋值回滚。
   ⚠ 这正是"状态所有权"的前置：**改为文件 store 后，整份复制不再成立，回滚会静默失效**
   （表现：失败的工具调用残留在上下文里，下一轮请求带着孤儿 `tool` 消息 → 服务端 400 或行为漂移，
   而屏幕上没有任何提示）。
   **先补断言，再动所有权**——建议形状（不需要改生产代码）：
   让桩日志记录每次请求的 `n_messages` 与**消息角色序列**（`[role for m in messages]` +
   `tool_call_id` 列表），然后在 e2e 里断言：
   "一条命令被硬墙拒绝后，**下一次请求里不得出现该 tool_call_id 的 `tool` 消息**"
   （即回滚真的把未完成的调用收干净了）。这条断言对"整份复制"和"快照 id"两种实现都成立，
   所以它能陪着所有权迁移一起用。

   🟢 **4d 接线方案（本轮定，按 §8.5"先单一出处 + 读写接口,再逐步替换"执行）**
   - **store 路径 = 会话/历史文件本身**（`$CT_HOME/history/<name>.json`），**不新建第二份文件**。
     理由：那个文件已经是 `{messages, thinking, model, title}` 的形状，`msgstore.load/save` 已能
     兼容（对象形状 + 保留兄弟字段，闸门 13/13 守着）。新建 store 文件 = 两份状态 = 铁律禁区。
   - **单一出处规则（接线期）**：文件是**跨轮的唯一真相**；轮内 bash 变量是工作副本。
     只允许两条通路：`msg_load`（轮首/载入时从文件填充）与 `msg_save`（写点回写文件）。
     **禁止**任何绕过它们直接改文件的路径。
   - **第一批（最小可验）**：只接**载入点**（`get_history` 的 `jq .messages` → `msg_load`）与
     **新会话点**（`-n` 初始化后 `msg_save`）。这两处是"填充/建文件"，不碰轮内写入，
     风险最低，且能被 `py_history 27` + `e2e` 直接看着。
   - **第二批**：`add_message` / `inject_*` / 回滚四个点（轮内写入）——此批完成后，轮内所有写入
     都过 `msg_save`，才算真正"单一出处"。
   - **第三批**：删掉其余直接读写、加结构锁（`grep -c '\$messages' ` 收敛目标），跑全量取新 sha。

   🟠 **4d 第二批的真实落点（实测后修正）**
   - **store 路径可推导**：`save_history()`(1190) 里文件名来自 `chat_title`（加载时是
     `basename "$1"`，新会话由 `overview` 生成），即 `$CT_HOME/history/<sanitized title>.json`。
     所以"轮内回写"没有路径障碍。
   - **但绝不能"再加一个写入方"**：`save_history()` **本来就在写这个文件**（对象形状：
     messages + thinking + model + title）。若 `msgstore.save` 也写同一文件 → **两个写者写同一文件**，
     谁后写谁赢，`messages` 与 `thinking` 可能来自不同时刻 → 正是最难查的那类"状态半新半旧"。
     ⇒ 第二批的正确做法是**替换**：让 `save_history()` 内部改调 `msgstore`，
     而不是在 append 点另外落盘。
   - **接口需要扩展**：现在的 `msgstore.save(path, messages)` 只替换 `.messages` 并保留已有兄弟字段
     （对"已有文件"正确）。但**新文件没有兄弟字段**，`thinking`/`model`/`title` 会缺失
     ⇒ 需要 `save_doc(path, doc)`（整份对象写）或给 `save` 加 `siblings=` 参数。
     这一条**必须先补 + 补断言**，否则第一批接线会让"新会话存盘后丢 thinking"。
   - 顺序：① 扩 `msgstore` 接口 + 断言（新文件带兄弟字段）→ ② `save_history` 改调它
     → ③ 再谈 append 点是否也走 store。

   🔴 **`retry_after_compact` 的迁移边界（读代码后定）**
   它是**编排**：读 `api_error`（bash 动态作用域）→ 判"是否越界" → `compact_messages`
   → 用 `make_request_inputs` 重建请求 → 递归重发。整块迁 = 迁核心循环，不在小步之列。
   **其中只有一处是可独立迁的纯逻辑**：越界判据
   ```bash
   hd_ovf_sig='(context (length|window|tokens)|maximum context|too many tokens|…|长度超限)'
   api_error_lc=$(printf '%s' "${api_error-}" | tr '[:upper:]' '[:lower:]')
   # 命中 → 判越界;否则 est_tokens=bytes*2/5,与 CTX_OVERFLOW_GATE 比较兜底
   ```
   ⚠ **这段正则不能盲迁**：注释里写明它防的是"真越界被判成网络抖动反复重试"，而
   `bytes*2/5` 的粗算**两个方向都会错**（对代码/英文偏高；又漏算 tools schema 与技能清单
   那几万 token）。一旦 ERE→Python re 的转写有细微差异（`{0,20}`、`|` 优先级、大小写折叠），
   症状是**某些越界不再触发压缩、只是一直重试**——没有报错。
   ⇒ **先把它变成可判读的东西再迁**：给它一组特征样本（各家越界原文 + 网络抖动原文 +
   大小写变体 + 中文变体），断言"该判越界的判越界、不该判的不判"；有了这组样本，
   正则才能安全换实现。**没有样本前，正则留在 bash。**（本轮已抽成 `ctx_overflow_kind` + 8 条特征样本，见闸门。）

   🟢 **W12-4 收口判定（A 阶段意义上，本轮结论）**
   §8.5 列的三块"业务逻辑"逐一评估完毕：
   | 块 | 结论 |
   |---|---|
   | `messages` 数据模型 | **操作层已全 Python**（add/auth_add/inject/init/render_summary/append_user + load/save）；bash 只剩"持有字符串并转交"与**计数查询**——按判据属胶水。**所有权 + 4 个回滚点留 B 阶段**（与"会话文件所有权"绑死，`orphan_tools==0` 安全带已备） |
   | 轮次/branch 管理 | `tool_round=$(($tool_round+1))` + `> MAX_TOOL_ROUNDS` 比较 —— **计数器 + 算术语义**，每轮工具调用都执行（热路径）。**留 bash** |
   | `retry_after_compact` 自愈 | 编排（读 `api_error` → 判定 → 压缩 → 重建 → 递归重发）属核心循环；**其中唯一的纯逻辑（越界判据）已抽出并被样本钉住**。编排本身留到 B 阶段随核心循环一起 |
   ⇒ **判定：A 阶段"业务逻辑全迁 Python、bash 只留参数解析/路由/终端/命令胶水"的目标，
   在 W12-4 这一块已经达成**。剩余工作属于 B 阶段（终端/pty、状态所有权）或已判定的胶水。
   下一步主线：把 §八 的进度表与各模块"进行中"的尾巴（W2b/W3/W5–W9 的编排半）重新盘一遍，
   确认它们是否也该按同一判据归档，而不是继续逐个迁。
   🟢 **W2b/W3/W5–W9 尾巴盘点（本轮实测：每个模块只剩**一个编排函数**）**
   | 模块 | 剩余 bash 函数（行号） | 性质 | 结论 |
   |---|---|---|---|
   | W2b | `hard_deny_cmd`(4757) | 编排：串谓词链 + 审计 | 留 bash |
   | W3 | `path_access`(5762) | 编排：审计/弹确认/备份/熔断 | 留 bash |
   | W5 | `mcp_call`（`_py_on mcp_client` 1 处） | 编排：通道 + 超时 + 错误映射 | 留 bash |
   | W6 | `vision_build_body`/`vision_parse_resp`/`tool_read_image` | 编排：HTTP + 图片落盘 | 留 bash |
   | W7 | `tool_read_file`(6098) | 编排：读文件 + 编码 + 报错 | 留 bash |
   | W8 | `tool_web_fetch`(6252) | 编排：HTTP + 状态码 + 渲染 | 留 bash |
   | W9 | `compact_messages`(3420) / `save_history`(1190) | 编排：判据 → 调 Python → UI/落盘 | 留 bash |
   **判据**：这 7 个都不是"同一业务的第二份实现"，而是**编排**——把 Python 纯函数、审计、确认、
   备份、HTTP、落盘串起来。它们正是 A 阶段允许 bash 保留的"路由/胶水"。
   ⇒ **因此这些模块在 A 阶段意义上可以标为已收口**（表里的 🟡 表示"函数级收口已完成、
   编排有意保留"，不是"还有活没干"）。真正的下一步是 **B 阶段**：终端层与状态所有权。
   🟠 **A 阶段剩余的真相：还剩"核心循环"一整块，不能按函数零敲碎打**
   实测 `tool_run_and_parse`(314) 与 `ask_ai`(301) 是**互相递归 + 共享 bash 全局**
   （`tool_ids/tool_names/tool_args` 收集区、`full_response`、`msgs_before_call`、
   `add_message`、`render`、确认弹窗）。迁其中一个必然要迁另一个，且要连带
   **工具注册表 `tools()`(194)** 与确认流程 —— 这是一块 ~1000 行的整体。
   ⇒ 因此"一个函数一个函数搬"的说法对它们是**不成立**的；它们是 W12-5「核心循环」
   的范围，必须作为一个单元迁移与验证（e2e + 全量 + 569 断言同时看）。
   🔴 **seam 划不出来的原因（读 tool_run_and_parse 中段后实测）**
   那个 for 循环把 **"簿记" 与 "工具实现" 交织**在一起：
   ```bash
   for i in "${!ids_arr[@]}"; do
     content=""                      # 全局清零(防上一条结果被当本次)
     msgs_before_call=$messages      # 回滚快照
     add_message … "assistant" …     # 只有第一条带 reasoning_content(否则死循环复读)
     … dispatch by fn_name → 调 tool_* 函数(仍是 bash,各 100~200 行)
     add_message … "tool" …          # 配对补齐
   done
   ```
   ⇒ **无法"只迁循环"**：Python 循环无法回调 bash 工具函数（bash→Python 是单向调用）。
   所以真正的迁移单元是 **核心循环 + 它调用的所有工具实现**，这是一块 1000+ 行。
   **修订后的 W12-5 顺序（可执行）**：
   1. **先逐个迁工具实现**（自包含、可单独验）：`tool_read_file`（其格式化半已在
      `filefmt.py`）→ `tool_web_fetch`（`webs.py` 已有状态码映射）→ `tool_read_image`
      → `grep/list_dir/write_file/edit_file` → `run_shell_command`(最后，最重)；
   2. **工具都成 Python 后**，循环里只剩"簿记 + 调 Python"，此时迁 `tool_run_and_parse`
      才是自洽的（一个 python 进程内 for 循环调 python 工具）；
   3. 最后迁 `ask_ai` 的编排（渲染留 bash）。

   📏 **工作量校正（实测代码行 vs 注释行，本轮）**
   全文函数合计：**代码 4487 行 / 注释+空行 1785 行**。最长的几个（代码行）：
   `run_with_window` 242（**终端，B 阶段**）、`hard_deny_cmd` 221、`parse_options` 187、
   `tool_run_and_parse` 178、`handle_cmd_line` 168（**终端，B 阶段**）、`ask_ai` 167。
   ⇒ 我之前说"核心循环 1000+ 行"**被注释撑大了**；真实代码约 **345 行**（178+167）＋
   各工具函数的"授权/参数/打印"胶水（每处 10–20 行）≈ **400 行上下**。
   ⇒ A 阶段剩余真实代码量级：**~600–800 行**（核心循环 + 工具胶水 + hard_deny_cmd 221），
   终端层 ~410 行属 B 阶段。这比"还有 9000 行"的直觉小得多 —— 因为 9368 行里
   **一半以上是注释、提示词与配置数据**。

   ⚠ 每迁一个工具都要：`py_<模块>` 闸门 + e2e + 相关行为断言；`orphan_tools==0` 全程盯着。

   每迁一个工具都要：
   `py_<模块>` 闸门 + e2e + 相关行为断言；`orphan_tools==0` 全程盯着。
   **另一条量化修正**：bash 里**没有巨型提示词 heredoc**（实测仅 1 个 5 行的 MCPJSON；
   提示词是散落在各函数里的 `text+=$'…'` 与超长单行）。所以"外置提示词砍几千行"的
   假设不成立，不要据此排期。
   当前真实分布：全文 9368 行；8 个大编排函数 ~2164 行（23%）；其余为中小函数 + 配置常量。
   ⚠ 唯一例外请留意：`save_history`(1190) 的**写盘不是原子的**（`echo > file`）——
   那是一条独立的健壮性待办（半截 JSON = 历史读不出），与迁移无关，已单独记在此。

   🟣 **W11 会话态形状（本轮实测 13 个变量，A 阶段只搬"持久"那一类）**
   实测位置：`wchat.sh` 711–777、934、4021、4121。
   - **持久（跨调用要活，4d 第二/三批的搬移对象）**
     `auto_mode`(档位) / `auto_downgraded` / `auto_allow_ro[]` / `auto_allow_rw[]` /
     `granted_files[]`（元素形状 `"mode|path"`，见 3873/3877）/ `auto_session_id` /
     计数 `auto_installs` `auto_approvals` `auto_deny_streak` `auto_backup_count` /
     回滚点 `auto_ck_kind|head|note|dirty`。
   - **瞬时（单次调用内有效，随状态机一起搬，不在本轮）**
     `auto_judge`/`auto_judge_reason`（本次判定结果）、`auto_estop`（窗口内急停）、
     `auto_tmp_prefixes[]`（本次临时前缀）。
   **形状约定**（供 Python 侧直接吃）：
   ```json
   {"mode":"sandbox|full|read|''", "downgraded":0,
    "allow_ro":["/p1"], "allow_rw":["/p2"],
    "granted":["sandbox|/p1"],        // 保留 "mode|path" 原形状,不重新编码
    "session_id":"20261016-120000-1234",
    "counters":{"installs":0,"approvals":0,"deny_streak":0,"backups":0},
    "checkpoint":{"kind":"","head":"","note":"","dirty":0}}
   ```
   ⚠ **`granted` 保持 `"mode|path"` 字符串不动**：它已经被 3873/3877 的追加逻辑和路径判定读着，
   重编码成对象会牵动 `authz`/`pathrule`（两个已收口模块），风险远大于收益。
   **搬移顺序**：先 `granted`+`allow_*`（纯数据，`py_authz`/`py_pathrule` 已用 argv 传），
   再 `counters`（只是加减），最后 `checkpoint`（依赖 git/rsync 命令，属胶水，可能留 bash）。

   ✅ **W11 定案（实测后，A 阶段意义上的收尾）**
   - **已搬 Python**：`granted_files`→`granted_list`（4 处）、`auto_allow_ro/rw`→字符串（16 处，
     含显示计数 7 处走纯 bash 的 `list_count`/`list_join`）。形状不变（换行分隔 `mode|path`），
     **`authz`/`pathrule` 接口零改动** —— 已收口模块不动，是这一步的安全底线。
   - **定案留 bash：`counters`**（`auto_installs`/`auto_approvals`/`auto_deny_streak`/
     `auto_backup_count`）。理由：**算术语义 + 热路径**。它们只是 `=$((x+1))`，只被 bash 侧的
     显示/审计读，没有任何 Python 模块需要；`auto_backup_count` 更是每次文件备份都 +1
     （`cp -p` 的下一行）。搬进 Python = 每次备份起一个进程换一行加法，**收益为零、成本是一次冷启动**。
   - **定案留 bash：`checkpoint`**（`auto_ck_kind/head/note/dirty`）—— 依赖 git/rsync **命令**，
     属命令胶水，不是数据。
   - **瞬时态**（`auto_judge`/`auto_judge_reason`/`auto_estop`/`auto_tmp_prefixes`）随状态机一起搬，
     不在 A 阶段这一轮。
   判据（与 `inject_skills`、`jq '.messages'` 同一条）：**迁移的目的是消掉"同一业务的两份实现"，
   不是消灭所有 bash**。凡是"一次加减 / 一次字段读取 / 装载期执行 / 调用外部命令"，留在 bash 更对。
   `inject_mcp`(2825) 与 `inject_auto`(5888) 都在**运行时**被调（`system_inject_all` 1372 / 1374、
   `inject_auto` 6051），没有这个障碍，可以照原计划先切这两处。
     先迁①③（纯追加，可逐字节对照），再迁②（注入），最后 ④ 回滚（改成显式快照 id）。
     每步都要 `新增/消失的红条` 可判读，并保持 `全表 569` 与 `堆压缩 37` 不变。

   ⚠ **上面这条"4b-2 先行"的说法在实现层面站不住（本轮勘误）**：图片展开目前发生在
   **bash 的 `make_request_body`**（`img_build_map` + `$_JQ_EXPAND_REFS`）。只要 bash 还在展开，
   Python 就永远看不到 `[[IMGREF:id]]` 标记 —— `expand_refs()` 与 `image_parts()` 会**永远空转**。
   也就是说 **4b-2 的 bash 侧 = 4b-1 的切换**，二者是同一个动作，不能拆成两轮：
   - Python 侧可以先行（已完成：`expand_refs`/`image_parts` + `ref_map_file` + `start` 事件，
     六族 parity 已进闸门、`py_llm` 33/33）；
   - **bash 侧必须与切换同一次落地**，且 `img_token_sample` 必须在**同一个提交**里改吃
     `start.image_parts` —— 否则一旦 bash 停止展开、而采样还去 `jq` 请求体数 `image_url`，
     得到的是 0，`img_token_sample` 会在 `[[ "$parts" == "1" ]]` 处**静默 return**，
     标定表从此不再增长（不报错、不告警）。
   **落地清单（一次提交，四个点）**：
   ① `make_request_body` 的信封带上 `ref_map_file` / `gone_pre` / `gone_hint`（`img_build_map`
      仍留在 bash，它产出的是**文件**，不是请求体）；
   ② 三个调用点（`3692/8244/8706`）不再调 `make_request_body` 的展开分支；
   ③ `ask_ai` 读循环加 `start` 分支存 `last_image_parts`；
   ④ `img_token_sample` 改吃 `last_image_parts`（删那句 `jq '[..|has("image_url")]|length'`）。
   验收：`inline_image_test.sh` + `e2e` + **标定表真新增一行**（不能拿"没报错"当通过）+ 全量对账。
4. **W12-4d `messages` 数据模型**：先"单一出处 + 读写接口"，再逐步替换 bash 侧的直接读写。

**闸门策略**：桩网关 e2e（不联网）+ 新 parity；`openai` 包缺失时，gate 走**回退路径**或**跳过 SDK 专测**并明示（不假绿）。
| 回退路径长期不删变成僵尸 | 铁律 3：收口期显式删除并加结构锁 |