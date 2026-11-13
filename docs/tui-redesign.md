# wchat 交互前端重写:Textual 全屏 TUI 方案

> 状态看板(每次推进会更新这一段,跨会话先看这里)
>
> | 阶段 | 内容 | 状态 |
> |---|---|---|
> | 调研 | 选型 + 真终端可行性验证 | ✅ 完成(2026-11) |
> | M0 | Textual 外壳 + 输出/输入接线 + 开关灰度 + 闸门 | ✅ 完成(2026-11) |
> | M1 | 流式接入(退役 StreamBox)+ 应用内滚动 | ✅ 完成(2026-11,M1-α 流式;M1-β markdown 定稿已用 Markdown 渲染) |
> | M2 | 输入特性迁移 + 确认/审批 ModalScreen | ✅ 完成(2026-11,含用户实测 5 问题修复) |
> | M3 | 视觉闸门重写 + 终端兼容矩阵 | ⬜ 未开始 |
> | M4 | (v2)打破串行枷锁:边流式输出边输入 | ⬜ 未开始 |
>
> 最近更新:2026-11 · 负责人:见 git blame · 关键结论:**用 Textual,不再手搓 prompt_toolkit**
>
> ⚠ 阅读顺序:先读「一、结论」「四、架构」,再看「六、阶段计划」挑当前阶段动手。
> 每完成一个里程碑,回来把这个看板的状态改掉、把该阶段的"实现记录"补上。

---

## 一、结论(先看这个)

把 wchat 的**交互前端**(输入框 + 模型输出/流箱渲染)从"prompt_toolkit 内联 + 终端
scrollback + 手写相对光标重绘"改成 **Textual 全屏 TUI(alternate screen)**。

- **不再手搓**:用成熟 TUI 框架 Textual 的现成组件(RichLog / Input / ModalScreen / worker),
  不自己拼布局、不自己算光标行数、不自己擦屏。
- **根治一整类问题**(不是补丁):
  - 输入框 resize/切 tab 残影、多框 —— 全屏每帧整屏重绘,旧帧残留**从架构上消失**;
  - 流箱漂移(StreamBox 的相对 `ESC[nA` 记账那一整套脆弱代码)**整体退役**。
- **开关灰度**:`WCHAT_TUI=1` 开启;非 TTY / `-st` / 子命令 / 管道继续走老路。Textual 缺失时
  自动回退老交互,不让用户卡死。

## 二、问题背景(为什么要重写)

现状交互渲染有两块,都建立在"**内联 + 可滚动终端缓冲**"之上:

1. `wchat_py/render.py::StreamBox` —— 模型流式输出用"`\r` + `ESC[nA` 相对上移 + 自己记
   `drawn` 行数"在普通缓冲里就地重绘。注释里全是终端兼容血泪(Terminal.app 不认 `CSI s/u`、
   绝对行号一滚就失效……)。
2. `wchat_py/inputbox.py` —— prompt_toolkit **内联**(非全屏)输入框,resize 时靠"相对擦屏"。

**这两个是同一病根**:在会滚动的内联缓冲里、用相对/绝对光标做增量重绘。于是:
- 输入框 resize/切 tab → 旧帧擦不干净 → 残影/多框(已两次尝试补丁、回滚,见 git:
  `4a59cd7`→`d8f8b39`(引入多框回归)→`9a8bb16` 回滚);
- 流箱依赖终端认不认某些转义、屏幕滚不滚,长期脆弱。

**补丁路线已证明会反复翻车**(每修一处崩另一处),必须换渲染模型。

## 三、选型:为什么是 Textual

真实调研 + 真终端(tmux)验证后的对比:

| 库 | 定位 | 结论 |
|---|---|---|
| **Textual** ✅ | Textualize 出品的最成熟 Python TUI 框架(8.x,活跃,生产在用),底层就是 Rich(我们已在用);内置 RichLog(流式滚动日志)、Input/TextArea、ModalScreen、CSS 布局、worker | **采用** |
| prompt_toolkit(现用) | 本质是"输入/提示框"库,全屏也要自己拼 Window/HSplit,无现成滚动日志/widget 体系 | 就是"手搓"的来源 |
| urwid | 老牌但 API 陈旧、样式笨重、活跃度低 | 备选 |
| Rich 单独 | 只是渲染库,无输入/事件循环 | 不能单独用 |
| npyscreen/py_cui/asciimatics | 小众/维护弱 | 不采用 |

**可行性验证(2026-11,已做,真 tmux 终端)**:用 Textual 8.2.8 按 wchat 形态写探针
(可滚动 RichLog 内容区 + 固定底部 Input + 流式追加 + markdown),真终端 resize 100→68→120:

| 列宽 | 输入框 | 内容区边框 | 残影/多框 |
|---|---|---|---|
| 100 | 1 | 1 | 无 |
| 68 | 1 | 1 | **无** |
| 120 | 1 | 1 | **无** |

流式 45 行连续追加、自动滚动、按新宽度换行正常;markdown(标题/加粗/列表/code/链接)正确渲染。
→ **全屏框架把"resize 残影"从架构上消掉,证实可行。**

依赖体积(相对现在):新增 `textual` + linkify-it-py / markdown-it-py / mdit-py-plugins /
platformdirs / Pygments / typing_extensions;**Rich 已在用**。均为主流纯 Python 包。
**M0 阶段先不并入硬依赖**(见 §6.1),按需安装、缺失自动回退。

## 四、架构设计

### 4.1 组件结构(`wchat_py/tui.py`,新模块)

```
WchatApp(Textual App)          # 全屏应用
├── RichLog  #content           # 内容区:模型输出/思考/工具横幅/确认结果,append-only、可滚动
│                              #   取代 StreamBox + 所有 stdout 排版
├── Static   #status           # 一行状态(auto档位/模型/token)—— 可选,M1 再加
└── Input    #input (dock bottom)  # 底部固定输入区,取代 inputbox.py 子进程
```

- `full_screen=True`(alternate screen):由框架独占整屏、每帧确定性重绘 → 无旧帧残留。
- 布局用 **CSS**(`#content{height:1fr} #input{dock:bottom;height:3}`),不算行、不算光标。

### 4.2 输出路由(关键:不逐处改 54 处 stdout)

现状 cli.py 有 ~54 处 `sys.stdout.write/flush`(横幅/思考/markdown/工具行/越界提示…)。
**不改这 54 处**,而是在会话期把 `sys.stdout` 换成 **TUI shim**:
- shim 把写入的字节累积、按行 flush → `app.call_from_thread(log.write, ANSI(文本))`
  (用 `rich.text.Text.from_ansi` 保留颜色/加粗)。
- 于是现有 `render_markdown()`、`_banner_text()` 等原样输出即可上屏,只是落点从终端变成 RichLog。

> ⚠ **StreamBox 例外**:它对流式增量做"相对光标重绘",在 RichLog(append-only)里没有意义。
> **M1 起**:流式不再用 StreamBox,`run_turn` 的 `on_text/on_reason` 增量**直接 append 到一个
> 可变 RichLog 段**(或增量拼成一条消息后用 `RichLog.write(..., shrink=False)` 覆盖末段)。
> M0 原型先用"非流式:思考+正文整段落地"证明接线通,流式留给 M1。

### 4.3 输入与 turn 执行(异步桥,最大的工程量)

现状:`run_turn` 是**阻塞**的(起子进程、等 LLM SSE)。TUI 里不能阻塞事件循环:
- `Input.Submitted` → 在 `@work(thread=True)` worker 里跑"一轮 turn";
- worker 通过 `app.call_from_thread(...)` 把输出喂 RichLog;
- 输入区始终可用(这天然满足 M4"边输出边输入",v1 先保证不冻 UI)。

接线点(见 cli.py `main(["repl"])` 的 `isatty()` 分支):
- 现状:`session_text_handler(_s, _cl_stream, _run_tool_boxed, ...)` + `run_interactive(_h, ...)`。
- TUI:`WchatApp.run_tui()` 拿一个 `handle_line(text)`;内部 `handle_line` 用现有
  `session_text_handler`/`orchestrate` 跑一轮,输出经 shim 进 RichLog。

### 4.4 确认/审批(ModalScreen)

`render.confirm_block` / 路径授权 / `auto` 的 `AUTO` 签字 —— 现在是内联 `sys.stdin.readline`。
TUI 下:
- 优先做 **ModalScreen**(在应用内弹窗,真·异步等待用户点/输);
- 或临时 `app.run_system_command(...)` 挂起 TUI 跑一次内联提示再恢复(兜底,简单但要闪屏)。
- M2 做;M0 遇到确认场景**降级**:沿用旧内联路径(见 §6.1 fallback 边界),不阻塞推进。

### 4.5 生命周期与降级

- 入口:`main(["repl"])` 且 `sys.stdin.isatty()` 且 `WCHAT_TUI=1` 且 `import textual` 成功
  → 走 TUI;任一不满足 → **原样走现在的 `run_interactive`**(完全不动老路径)。
- 退出:Textual 正常退出会恢复终端(alt-screen 复原);`Ctrl+C` 在应用内绑定为"清当前输入/
  二次强制退出",与现有"两次 Ctrl+C 急停"语义对齐(需在 M2 校验)。

## 五、横切设计要点

1. **非 TTY / 管道 / `-st` / 子命令**:一律不走 TUI(cli 里已有 `isatty()` 门,天然 gate)。
2. **宽字符**:Textual 原生支持 CJK 宽度(基于 wcwidth),无需 `display_width`。
3. **超长会话**:RichLog 会一直长;需上限(只保留最近 N 行 / 或分批 trim),M1 处理。
4. **复制/粘贴**:Textual 支持鼠标选择 + OSC52(部分终端);图片粘贴/文件选择需重做成
   Textual key-binding(M2)。
5. **scrollback**:全屏 TUI 没有终端原生回滚,历史靠应用内滚动(PgUp/滚轮)。**已知取舍**。

## 六、阶段计划(每阶段:交付物 + 验收 + 回滚)

### 6.1 M0:Textual 外壳 + 接线(已完成 2026-11)

**实现记录(截至 M0):**

- **`wchat_py/tui.py`(新增)**:`available()` / `make_app(handle_line, banner)`(供 run 与闸门共用)/
  `run_tui()` / `TuiStdout`(stdout→RichLog 桥,缓冲+主线程定时 drain,线程安全)。
  `WchatApp`:`RichLog #content + Input #input`(dock bottom),`full_screen=True`,
  `on_input_submitted` → `@work(thread=True)` 跑一轮;worker 内只写 shim 缓冲,主线程 drain 进
  RichLog。**懒加载 textual**(无 textual 时模块仍可 import)。
- **`wchat_py/cli.py`**:`main(["repl"])` 的 `isatty()` 分支前加 `WCHAT_TUI==1 and tui.available()`
  → 走 `run_tui`;`handle_line` 用现有 `session_text_handler` + `_call_llm`(非流式)+ `_run_tool`
  跑一轮,输出经 shim 上屏,`on_final` 一次性渲染 markdown。**不满足即原样走老路径**(零回归)。
- **`tests/tui_test.py`(新增,M0 闸门)**:headless `App.run_test()` 断言:compose 恰好 1 RichLog
  + 1 Input;输入回车→回显;resize 后仍单 widget;handle_line 的 emit 经 worker→shim→RichLog 落地。
  textual 缺失时按 run_gates 约定 `SKIP:` + exit 77。
  **运行方式(重要)**:run_gates 的 runner 是 3.9(无 textual),故本闸门**暂不并入 run_gates**
  (并入会恒 SKIP 并改变套件 skip 计数)。用带 textual 的解释器跑:
  ```bash
  # 用项目里带 textual 的解释器;开发期可建一个工作目录内 venv:
  python3 -m venv .scratch/tui_venv && .scratch/tui_venv/bin/pip install textual
  .scratch/tui_venv/bin/python tests/tui_test.py       # → RESULT total=11 pass=11 fail=0
  ```
- **验证结果**:headless 闸门 11/11;真 tmux 终端跑 `python -m wchat_py.tui`,resize 100→68→120
  **始终 1 个输入框、无残影/多框**;全量老闸门 76/76(老路径零回归)。

**启用**:`WCHAT_TUI=1 ./wchat`。`textual` 已并入主 `requirements.txt`(`>=8,<9`),正常安装即随包带入;
若某环境缺它,`WCHAT_TUI=1` 会自动回退旧交互(不卡死)。
**M0 已知限制(留给 M1/M2)**:非流式(整段落地,非逐字);确认/审批仍走内联(可能受 TUI 干扰,
先只在 auto/lowsensitive 场景用);无应用内滚动上限;输入特性(Ctrl+F/粘贴图)未迁移。

**交付物清单**:`wchat_py/tui.py`(新增)、`cli.py`(接线)、`tests/tui_test.py`(闸门)、本文件。

### 6.2 M1:流式接入 + 退役 StreamBox(设计已定,待实现)

**已查清的关键事实(决定实现形态)**:
- `session.orchestrate` 调 `call_llm(msgs)` **不传 on_text/on_reason**(session.py:382)。
  流式是**在 call_llm 包装函数内部**做副作用实现的:传进去的 `call_llm` 本身在内部调
  `_call_llm(m, "", on_text=…, on_reason=…)`,边流边 `emit`;orchestrate 只拿最终文本。
  → 所以 M1 要给 TUI 写一个 `_cl_stream_tui(m)`:内部 `_call_llm(m,"",on_text=append,
  on_reason=append_dim)`,把增量 append 进 RichLog。**不需要改 orchestrate。**

**设计岔口(append-only RichLog 的取舍)**:
- RichLog 只能 append,不能"替换/清空一段"。于是"逐字流式(纯文本)"与"最终渲染 markdown"
  二者难两全:若边流边 append 原文,又在结尾 append markdown = 重复。
- **分两步走**:
  - **M1-α(先做,低风险)**:`_cl_stream_tui` 边流边 `emit(delta)` 纯文本 append(先做行缓冲
    合并,别一字一 write);`write_final=False`、`on_final=None`(不重复渲染)。工具轮之间
    的横幅照常经 shim 上屏。**收益:模型输出逐字出现(核心 UX),代价:不渲染 markdown。**
  - **M1-β**:引入"live 区域"(RichLog 之上/之内用一个可重建的 Widget 承载当前轮,完结时
    用 markdown 重建这一块),或在完结后 `RichLog.clear()` 再重放整段 markdown。需要 Textual
    的 `RichLog.clear()`/自定义 `Widget`;单独验证。
- **退役 StreamBox**:TUI 路径不再用 StreamBox(它那套相对光标字节在 RichLog 里是垃圾)。
  StreamBox 保留给**非 TUI 老路径**;等 TUI 默认开再评估整体删除。

**验收(M1-α)**:
- headless 闸门:用一个"分批 emit 的假 streaming handle_line"断言 RichLog 里**增量**出现
  多段文本(而非结尾一次性);一个 turn 结束后内容完整、无重复。
- 真 tmux:接一个假流源(带 sleep 的分段 emit),肉眼确认逐字出现、无残影。
- 全量老闸门 76/76(老路径不动)。
- 长会话:RichLog 行数上限(如只保留最近 N 行),验证内存/滚动。

**实现记录**:(M1 完成后在此补:改了哪些函数、闸门数字、tmux 证据)

**M1 实现记录(已完成 2026-11,方案 M1-α + Markdown 定稿)**:
- `wchat_py/tui.py` 重写为「单线程安全事件队列」模型:
  · `TuiStdout(app[,style])`:sys.stdout/stderr 的 shim,**直接** `app._q.put(("log",s,style))`
    (装进 worker 后,横幅/markdown/错误都进 log 通道,排序与 stream 一致)。
  · `emit(s)`→("log")、`stream(s)`→("live")。`#live` Static 显示当前流式缓冲;`#content`
    RichLog 存已完成条目。
  · 主线程 `set_interval(0.04,_drain)`:按队列**发出顺序**消费——遇 "live" 累积;遇 "log"
    **先把 live 定稿进 RichLog(Markdown 渲染)再写日志**(保证工具轮前正文先落地、横幅不乱序)。
    跨线程只 put 队列,绝不跨线程碰 widget。
  · worker(`@work(thread=True)`) 装 shim→跑 `handle_line(text, emit, stream)`→还原→
    `call_from_thread(_on_turn_done)`(收尾 drain + live 定稿)。
- `cli.py` 的 TUI `_tui_handle_line(_text,_emit,_stream)`:内部 `_cl_stream_tui(m)` 用
  `_call_llm(m,"",on_text=_stream,on_reason=_stream)` **边流边喂 live**;**不再用 StreamBox**;
  工具经 `_run_tool_tui`(先 emit 横幅再执行)。`write_final=False`、不额外渲染(流式正文即最终,
  由 drain 用 Markdown 定稿,不重复)。
- **StreamBox 未删**:仍服务非 TUI 老路径;TUI 路径不引用它。整体退役留到 TUI 默认开之后。
- 验证:headless 闸门 `tests/tui_test.py` **12/12**(含"流式答案定稿进内容区""工具横幅经 log 通道
  出现""resize 仍单 widget");真 tmux 跑 `python -m wchat_py.tui`,输入 hello → 流式落地、resize
  100→68→120 始终 1 个输入框无残影;全量老闸门 **76/76**(老路径不动)。
- 已知限制(M2):思考(on_reason)当前与正文一起进 live(未做 dim 区分);工具确认/审批仍走内联
  (TUI 下可能干扰,需 ModalScreen);输入特性(Ctrl+F/粘贴图)未迁移;live 区无历史回滚(RichLog 有滚动)。

### 6.3 M2:输入特性 + 确认/审批(已完成 2026-11)

**M2 实现记录(对应"用户实测 5 问题",全部已修)**:
用户开 `WCHAT_TUI=1` 实测暴露的问题与修法:
1. **回复内容不在内容区内** → 原来流式进 `#live`(RichLog 之外的独立 Static,显示在内容框下方)。
   改为把 `#live` **嵌进带边框的 `#contentbox`**(`Vertical[RichLog #content(1fr), Static #live(auto)]`),
   流式就显示在内容框内底部;完结定稿进 RichLog。
2. **确认弹窗太大** → `ConfirmModal` 收敛为**小居中框**:`.dialog { width:64; height:auto; max-height:12 }`,
   类 window/mac 小确认框。
3. **`\` 续行无效 / Ctrl+F 无效** → 输入从单行 `Input` 换成**多行 `ChatInput(TextArea)`**:
   `Binding("enter","submit_or_continue")`(行尾 `\`=续行换行,否则发送)、`Binding("ctrl+f","pick_file")`
   (调 `repl._pick_file()` 选文件并插入占位行,提交时 `session_text_handler` 的 parse_placeholders 吃它)。
4. **`-q` 无法退出 /其他 `-*` 命令失效** → 提交流程先 `cmdline.classify(text)`:`quit`→`self.exit()`;
   其它 meta→`on_meta(action,arg)`(worker+shim,输出进内容区,`-cl` 特判清 RichLog);`text` 才进模型。
5. **思考文本 dim 无效** → 流式区分正文/思考:`stream(s)`→正文(普通),`reason(s)`→思考(dim);
   定稿时思考写 `Text(style="dim")`、正文写 `Markdown`。→ `handle_line(text, emit, stream, reason)`。
- `render.py`:加**单一确认钩子** `set_confirm_hook(fn)`;`confirm_block` 有钩子走 `fn(title,mark,ask)`、
  否则原 readline(非 TUI 零改动)。所有 yes/no 提问都经 confirm_block → 一处接通全走弹窗。
- `tui.py`:`_confirm_blocking(title,mark,question)`:worker 线程里 `call_from_thread(push_screen(ConfirmModal,...))`+
  `threading.Event` 阻塞等答案(主线程弹窗、用户回答桥回 worker)。
- 验证:headless `tests/tui_test.py` **23/23**(续行/流式定稿进内容区/思考 dim 段/工具横幅/确认弹窗 y→True n→False/
  钩子路由);真 tmux:内容框内显示 echo+思考+答案、`-q` 退出(GONE)、确认弹窗为小框、按 y→`结果:True`;
  全量老闸门 **76/76**。
- 遗留:①粘贴图片(Ctrl+V)未迁移(GUI 剪贴板;优先级低);②交互式 meta(如 `-l` 选历史号)在 TUI 下会读 stdin
  受限,用到时再单独适配;③`AUTO` 首签仍在启动前(非 TUI 阶段),不受影响。

**§6.3 遗留已清(2026-11)**:
- ② 交互式 meta(`-l` 选历史号):`_default_meta` 的编号读取改走 `render.read_line`(TUI 弹 `InputModal`、
  非 TUI 退回 readline)。同理 `auto_enable` 的 AUTO 签字(见上批)已迁移。
- ① Ctrl+V / Ctrl+G 粘贴图片:`ChatInput` 加 `action_paste_image`,复用 `inputbox._clip_image_file`
  从系统剪贴板取图(经 `_grab_macos`,osascript/sips,零额外依赖),插入 `📋 [图片] <路径>` 占位行;
  提交时 `session.parse_placeholders` 读进 input_images(与老 inputbox 同管线)。无图则 bell。
- 验证:headless tui_test 36/36(新增 Ctrl+V 插入占位行、read_line 路由);全量 76/76。

**M2 UI 精修(用户截图 6 点,2026-11)**:
1. 去掉内容区顶部提示文本("内容区:模型输出…"),on_mount 不再挂它。
2. 内容区**去掉边框**(`#contentbox border:none`),与终端融合(输入框仍保留边框)。
3. 思考折叠**真正单行**:`_render_reason` 只取首个非空行(▸ + 压空白),超宽由 CSS
   `#content .reason { text-wrap:nowrap; text-overflow:ellipsis }` 保证**一行 + … 截断**
   (原先手工按宽度算会因内容框比整屏窄而折成两行)。
4. 滚动条:`#content scrollbar-size-vertical:1; scrollbar-color:gray; scrollbar-background:transparent`
   → 缩小且灰色,与思考文字同色。
5. 输入框边框:`#input border:round gray`(原来是 accent 蓝),与思考内容同为灰。
6. 输入框上方留白:`#status` 默认 `height:0`,仅 auto 开启时加 `.on` 类(`height:auto`)——
   无 auto 时不留过宽空档。
- 验证:真 tmux:无提示文本、content 无边框(全屏仅 1 个 ╭=输入框)、思考单行带 …;headless 36/36;全量 76/76。
  (滚动条/边框"颜色"在文本捕获里不可见,靠 CSS 断言;实际观感需真机确认。)

**M2 UI 精修 2(用户截图,2026-11)**:
- 内容区**完全贴合终端、不留缝**:`Screen padding:0`;`#contentbox padding:0 margin:0 border:none`;
  `#content padding:0 margin:0 border:none`(去掉原来 `padding:0 1` 造成的左右 1 列缝)。真 tmux:内容行
  从列 0 起,无缩进;全屏仅 1 个 ╭(输入框)。
- 滚动条更暗更细:`scrollbar-size-vertical:1`、`scrollbar-color:#444`(+hover #555/active #666)、
  `scrollbar-background:transparent`(hover/active 同)。→ 缩到 1 列、深灰不抢眼。
- 输入框边框改深灰 `#6a6a6a`(与思考同档灰)。
- 说明:滚动条"圆角"Textual 不支持(只有方形块);"再缩 1/3"已达最小值 1 列,无法再细。

**M2 UI 精修 3(背景融合,2026-11)**:
- 现象:内容区与终端之间有一圈**背景色差细缝**(非 padding 问题)。根因:`textual-dark` 主题把整屏涂成
  `#121212`,与用户终端背景不同。实测:`background:transparent` 对 Screen 无效(回落 #121212)、
  `ansi_default` 近似成 #0c0c0c 仍≠终端。
- 解法:`App.theme = "ansi-dark"` → Screen 背景发 `\e[49m`(=终端默认背景)×每帧数百次,**零 RGB 背景填充**,
  彻底贴合终端(含其透明/背景图)。显式 hex 颜色(边框#6a6a6a/灰字/滚动条#444)实测**精确保留**。
- 输入框同样处理:`#input { background: transparent; border: round #6a6a6a }`(+ `#input:focus` 同),
  TextArea 内部背景实测全 `\e[49m`(无面板填充),**只留边框线**,内部与终端融为一体。
- `padding` 恢复 `#content { padding: 0 1 }`(文字不贴边,更耐看)。
- 验证:真实 WchatApp 抓流:458× `\e[49m`、0 个 RGB 背景填充、边框灰在;tui_test 36/36;全量 76/76。
  (前提:深色终端;浅色终端可考虑切 ansi-light。)

**M2 追加修(用户实测:回复从底部冒出,而非紧接提问下一行)**:
- 根因:内容区原为 `RichLog(1fr)` + 其下独立 `#live` Static;RichLog 吃满高度把 #live 顶到**箱底**,
  于是流式从底部冒、与顶部提问之间一大段空白。
- 修法:内容区改成**消息列表**——`VerticalScroll #content` 里每段一个 `Static` 顺序 `mount`(append)。
  回显/思考(dim)/正文(Markdown)/工具横幅都按发生顺序挂成 widget,live 是"最后那个 Static"原地
  `.update()` 增长,完结 `_finalize_live()` 移除它并挂正式的 dim/Markdown 段。天然**从上往下、紧接上一段**,
  自动 `scroll_end`。`-cl` 清空所有子件。
- 验证:headless 22/22(含"回显在思考/答案之前"顺序断言);真 tmux:捕获行号 `❯ hi`=4、`思考`=5、
  `你好!我是`=7、markdown 列表 9-13 —— 紧接提问下方,无底部空白;全量 76/76。

**M2 追加修(用户实测第 2、3 问题)**:
- 问题2(输入框换行不变高、看不到上文):`#input` 由 `height:3` 改 `height:auto; max-height:12` ——
  TextArea 随行数自动变高(验:输入 `aaa\`+回车续行再输 `bbb`,框内两行同时可见)。
- 问题3(`-auto=full` 的 AUTO 签字收不到、AUTO 被当消息发给模型):根因是 `auto_enable.enable`
  用裸 `sys.stdin.readline()`(TUI 下 stdin 归事件循环 → 立刻空 → auto 静默关闭,随后输入的
  AUTO 被当普通消息)。修法:`render` 加**单行输入钩子** `set_line_hook/read_line`;`auto_enable`
  改用 `read_line`(TUI 弹 `InputModal` 收字符串、非 TUI 退回 readline);TUI 装 `_line_blocking`
  (worker 用 `call_from_thread(push_screen(InputModal))`+Event 桥回);meta 执行也置 `_busy` 防误发。
- 验证:headless 26/26→28/28(新增 InputModal 桥、read_line 路由、完整 `-auto=full`→弹窗→键入 AUTO→开启);
  真 `./wchat`:`-auto=full` 弹 "请输入大写 AUTO" 输入框、键入 AUTO→`⚡ auto 已开启`、AUTO 不入模型、`-q` 退出;全量 76/76。

**M2 追加修(用户实测第 4 批)**:
- 问题1(auto 提示应"在输入框侧",不应进内容区):新增**状态区** `#status`(内容框与输入框之间的一行区,
  最多 6 行)。`_run_meta` 的 stdout/stderr 走 `TuiStdout(status=True)` → 进状态区;元命令回显与输出
  (auto 横幅/usage/model 状态…)都在那里,**不污染对话正文**。真 `./wchat`:`-auto=full` 的
  `└─ 开启 auto / 请输入大写 AUTO / ⚡ auto 已开启` 落在内容框(21 行)与输入框(28 行)之间;内容区干净。
- 问题2(思考不折叠):`_finalize_live` 改用 `render_reason(reason)` —— 默认折叠成一行(`▸` 首行),
  `-tf=on` 整段,与老 UI 一致。headless 断言思考含 `▸`(折叠标记)。

**M2 追加修(用户澄清:状态区只放 auto 档标记;`-auto` 输出仍进内容区;思考折叠须单行)**:
- 更正上一批:之前把**所有 meta 输出**塞进状态区是错的。用户要的是——`-auto` 的开启输出
  (`开启 auto / 请输入大写 AUTO / auto 已开启`)留在**内容区**;而常驻的 `⚡ auto=full · 已自动放行 N 次 · …`
  **档位标记**才放输入框上方(老 bash UI 里它就常驻在输入框上沿、auto 期间一直可见)。
  · `_run_meta` 的 stdout/stderr 恢复走内容区(TuiStdout 默认);`❯` 回显不进内容。
  · 新增 `_refresh_status()`:每 tick 若 `AUTO_MODE` 开着就用 `auto_enable.marker_text()` 刷新 `#status`
    (该标记含"已自动放行 N 次",随放行递增),关则清空 → **常驻可见**。
  · `_drain` 的 log 分支**丢弃**含"本次会话已自动放行"的行(它来自 auto_enable.repl 的 stdout 打印),
    避免与状态区重复。
- 思考折叠**真正单行**:`_render_reason` 不再用 `render_reason`(它按**整屏宽度**截断,而内容框更窄 →
  折成两行)。改为:取首个非空行、压空白、按**内容区实际宽度**(`content_size.width`)逐字(CJK 记 2)截断、
  超宽补 `…`,保证只占**一行**;`-tf=on` 仍整段。
- 验证:headless 34/34(内容区含"已开启"、状态区含"已自动放行"、内容区无重复标记、思考折叠单行且以…结尾);
  真 `./wchat`:auto 输出在内容框内、`⚡ auto=full…` 标记在内容框与输入框之间的状态区;全量 76/76。

### 6.4 M3:视觉闸门重写 + 终端兼容矩阵

- 重写 `inputbox_layout_test` 等"断言旧渲染"的闸门 → Textual `run_test` 快照(内容区行数/
  输入区位置/无重复 widget)。
- 终端矩阵:Terminal.app / iTerm2 / Ghostty 各跑一遍 resize + 切 tab + 滚动。
- **这次必须真终端验证**(前几轮的教训:模拟屏护不住)。

### 6.5 M4(v2):打破串行枷锁

- 边流式输出边输入:worker 跑 turn 期间输入区仍可编辑/预填下一条;Textual 原生支持。

## 七、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 失去终端原生 scrollback | UX 变化 | 应用内滚动 + 明确文档;用户可 `WCHAT_TUI=0` 回旧模式 |
| Terminal.app 真彩/鼠标兼容弱 | 观感/操作差异 | M3 真机矩阵;必要时 256 色回退 |
| markdown/ANSI 保真度 | 渲染细节丢失 | 用 `Text.from_ansi`;极端序列抽查 |
| 异步桥接 bug(worker/线程安全) | 卡顿/崩溃 | `@work(thread=True)` + `call_from_thread` 单一通道;充分测试 |
| 输入特性迁移遗漏 | 功能缺失 | M2 逐项清单 + 用例 |
| 闸门大面积失效 | 质量下降 | M3 重写;过渡期老闸门与新 UI 并存(开关) |
| 依赖体积 | 安装变大 | M0 可选依赖;M3 再决定是否并入 brew |

## 八、回滚

- 运行期:`WCHAT_TUI` 不设 = 旧路径(默认关闭,直到 M3 验收通过再考虑默认开)。
- 代码级:每阶段独立提交,可单独 revert。

## 九、待定问题(推进中填)

- [ ] M0 后:是否把 textual 并入 brew 硬依赖(看体积/是否默认开)?
- [ ] 流式增量上屏:用"末段覆盖"还是"分块 append"?(M1 定)
- [ ] 状态栏要放哪些信息(档位/模型/token/审计)?(M1 定)