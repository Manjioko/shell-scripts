# 回归闸门清单(名字与命令以此为准,别再凭印象编)

这轮踩的坑值得钉在纸上:上下文压缩后我凭印象造出一个从未存在的
`tests/hardwall_table.sh`,认定"517 条断言的表消失了、且从未入库",连着查了
好几轮(找文件、查 git 历史、翻备份目录、怀疑被哪个测试越界 rm),最后发现
**517 那张表就是tests/ 下的 `tests/test_hardwall.sh` 本体** —— 它的 `expect H5 '...'`
表体一直在,还有 `EXPECT_TOTAL` 专门守住"断言总数对不上就 FAIL",结论打在
`RESULT total=… pass=… fail=… expect=… src=<sha>` 这一行里。文件 tracked,
什么都没丢。所以以后:报"资产丢失"之前,先按**输出行的指纹**去 grep,不要按
记忆里的文件名去找。

| 闸门 | 命令(都用 `/opt/homebrew/bin/bash`,产品要求 bash 5+) | 我看过的真实结论 |
| --- | --- | --- |
> 基线数于 2026-08-07 从 **517 重固化为 527**:新增第十八轮 H13(写目标不可解析)6 条拦截 + 3 条反锁 + 1 条"上下文三数关系"结构锁。
> 第二十一轮再加 **8 条 H14 安装面向量**,基线 527 → **535**(5 拦 + 3 反锁:引号里的散文、卸载、使用已装工具都不许算安装动作)。
> 第二十二轮再加 **25 条**(基线 535 → **560**):9 条 R22 表行 + 2 条提取器断言 + 11 条**折叠不变量** + 2 条接线锁 +
> 1 条重名结构锁。起因是审计里 H14 命中 6 次**全是误拦**(6/6 带 heredoc、0/6 真装包):调用点把命令折成一行喂
> `hard_deny_cmd`,判据看不见行边界;而 `hd_install_tail`/`auto_install_allowed` 各有两份定义 —— 产品按 source 顺序
> 跑后一份,本表抽函数在第一个 `^}$` 就停、只测前一份,**这张表的绿当时是假的**。
> 同轮再 **+2 条**(560 → **562**):把两条**靠巧合才绿**的向量(顶层 `WORK_ROOT="$PWD"`,而作者恰好在
> `/Users/jwan/bin` 跑表)的前提就地钉成 `WORK_ROOT="$HOME/bin"`,并各配一条**反例**(根不是它 → 必须判 P3)。
> 反例是重点:只钉前提的话,下次"消红"的最短路径就是把期望放宽,反例把它堵成双向。
> 实测:仓库根与 `cp wchat.sh tests/test_hardwall.sh <别的目录>` 两处都 `562/562 fail=0`(改前别处是 560/558/2);
> 变异"把反例改成 NP"当场红 1 条。**装置防不住的**:同时删掉反例与它那行 `EXPECT_TOTAL` 仍然自洽,只能靠 review。
> ⚠ 本机现存 2 条红与判据无关,是 T23 装载口那两条(`source ./wchat.sh` 会先跑启动期代码、撞 REPL 读取):
> 看 `RESULT` 时按 `fail=2` 起算,别把它们读成回归。
> **上一条已作废(同日实测转绿)**:那两条红的真因不是"启动期代码跑一遍",而是主脚本第 44 行
> 用了那时还没定义的 `$API_KEY` —— 带 `set -u` 的夹具当场 `unbound variable` 死在口之前,现象
> 与"口没实现"一模一样(同一根因还连带红掉形状探针那道)。改法是两处:44 行 `${API_KEY:-}`、
> 无 key 的 `exit 1` 那道加 `&& -z "${_WCHAT_LIB_ONLY:-}"`(真实执行路径一字未改)。
> 现在本机基线:**全表 569/569 fail=0**、`bash tests/run_gates.sh --main` → `pass=10 fail=0 skip=0`。
> MCP 接入轮再加 **7 条**(562 → **569**):P1 路径 3 拦 + H6 命令 2 拦 + 2 反锁钉 `~/.mcporter` 前缀相似目录不误伤。
> 成对是刻意的:命令侧 `hd_re_secret_dir` 与路径侧 `hard_deny_path` 的名单是**两份实现**,只改一侧
> 就会出现"shell 拦死、read_file 放行"——本轮第一次就只改了正则,结果 4 条路径断言当场红。
> 另:T23/T25 原先把被测文件名写死成 `./wchat.sh`,对副本跑表时它们测的还是工作副本(改好了验不出),
> 现已全部改按 `$SRC_ABS`。负对照实测:从副本里删掉装载口 → T23 两条 + T25 一条立刻红。
> 那次全表跑不出 RESULT 的根因不是判据坏了,是 harness 变量抽取 `grep -vF '$('` 把 `$((…))` 算术派生
> 一起排除在外,夹具里 TOKEN_THRESHOLD/CTX_OVERFLOW_GATE 根本不存在 —— 详见该文件第 74 行注释。
> **W9(压缩/历史)收口轮**:`test_hardwall.sh` 的迁移通道开关由"默认回退桩"改为**默认生产路** ——
> 收口把 bash 回退按铁律 3 删掉后,回退桩模式对已收口模块(A2 起:authparse…)必判红;`HW_PY=0`
> 保留为调试口。同时 `mcp_channel_test.sh` 那条 `grep 'split($mark3)'`(grep 源码的旧断言,
> 铁律 5)改为**行为断言**(python `history pack` 剥第 3 刀)+ **接线断言**(`save_history` 传 `$MCP_MARK`)。
> 基线:全表 569/569、MCP 通道 130/130、`py_history` 27/27、`py_filefmt` 22/22、`py_mcp_client` 22/22、主套件全通过。
> **W7(文件类工具)收口轮**:`filefmt.read` 收口 —— `tool_read_file` 里的 bash 格式化回退删除,
> 组件不可用 → **明示"读取组件不可用"、不返回半截/错行号内容**;闸门改为语义 + 结构锁 + 工具口降级。
> **W2b(硬墙谓词)收口轮**:8 个已路由的 `hd_*`(分词/脱敏/折叠/他人家凭据/剥注释/H11 落点)收口,
> 旧 bash 回退删除;降级一律**复刻"函数整体缺席"时的既有行为、不新增放行面**;`hd_write_targets`
> 的 split 守卫加 `&& hd_split_words "$seg"` 以在降级时退空白切分(否则空目标=fail-open)。
> 基线:`py_hd` 53/53、全表 569/569。
> **W6(视觉)收口轮**:`vision_build_body`/`vision_parse_resp` 收口 —— 旧 bash jq 回退删除;
> 不可用 → build_body `rc=1`(调用方报"请求体构建失败")、parse_resp 空串(调用方判"无转述文本");
> `vision_layer_test.sh`/`vision_e2e.sh` 沙箱接 `_py_on`/`py_run` 生产路。基线:`py_vision` 25/25。
> **W12-2(`hard_deny_path`)收口轮**:`hard_deny_path` → `wchat_py/pathrule.py`(配置+会话态全经
> argv 传入);旧 bash 全量回退删除,降级=fail-closed **粗判据**(凭据/系统路径字面量前缀 + 根内放行)。
> ⚠ 两条**对 `wchat.sh` 源码的静态 grep 断言**(凭据文案出口说明、P2 名单含 `/root`)因判据迁 Python
> 而假红 —— 已改成**行为断言**(调 `hard_deny_path` 看 `hd_rule`/`hd_msg`),这是铁律 5 的又一实例。
> 基线:`py_pathrule` 25/25、全表 569/569。

| 全表断言(即"517 那张表") | `bash tests/test_hardwall.sh` | `RESULT total=… pass=… fail=0` + `PASS=… FAIL=0`;**默认走生产路**(真实 `_py_on`/`py_run`:已迁移/已收口的谓词走 Python 唯一活实现,未迁移的照旧 bash;`HW_PY=0` 才回退桩,仅供调试);**慢**,几百条各起一次判定,请用后台通道跑,别在 3 秒无输出的前台管道里等 |
| 判据规则失败核查 | `bash tests/rulefail_check.sh` | `PASS=111 FAIL=0` |
| 形状探针(36 行表) | `bash tests/probe_shapes.sh` | 逐条 `ok H3/NP …` |
| 真实流量回放对账 | `bash tests/traffic_replay.sh` | `一致 25 / 有损未参与 74 / 判定崩溃 0`;更新基线:`GEN=1 bash tests/traffic_replay.sh` |
| 审计字段探针 | `bash tests/audit_field_probe.sh` | 3/3 通过(钉住 `auto_audit` 的 1200 上限、`trunc`/`tlen`、`root` 工作根、`pol` 生效判据版本) |
| 闸门对账(一条命令跑完上列全部) | `bash tests/run_gates.sh` | 每道必须 **rc=0 且匹配到自己的结论行**;`--list` 看清单,`--main` 加主套件 |
| 压缩布局探针 | `bash tests/compaction_pin_probe.sh` | 37/37 通过(**不联网络**钉住旧摘要锁定:`summary_pin_count` / `compact_apply`;含"失败硬截断也不许丢掉摘要"、**数组型 content 不许把锁定计数打成空**、以及"裸 content 惯用法只剩片段定义那 1 处"的结构锁 + 反例锁)。⚠ 它的夹具登记名单里必须含 `_JQ_CONTENT_TEXT`:被测函数一依赖全局,漏登记=沙箱里它是空串=jq 编译失败=函数返回空,断言会红(有"没抽到就拒跑"守卫,不会假绿) |
| inline 图片通道闸门 | `bash tests/inline_image_test.sh` | **293/293**(刀3 时是 102/102;刀4–刀7 陆续加组 → 262,刀8「交付事实句」第 22 组 + 「字节通道不许放大」第 23 组 + 「转述通道也封像素」第 24 组,并把"缩过"判据从"不是首档"改成"交付件尺寸 ≠ 原文件尺寸" → 284 → 293;第 12 组另加两条反向锁(格式/解码器类报错**不许**判成"不收图" —— 收成统一判据时丢过这层排除,当时只有不在套件里的 vision_layer 探针钉着 → 漂了没人红),**不联网**;第 10 组 = 切档视觉预检的离线性:预检=不收图 时 attach 必须直接拒绝、结论未知时照常尝试、**缓存必须排在所有前置检查之前**(否则一次缺 key 就把已知事实抹成未知)、缺配置只能判"未知"不得判"不收图";第 11 组 = **轮边界位置锁**:降级必须发生在换轮时的 printContent 开头,而不是等下一次读图 —— 机制本身在坏版本上也是对的,**坏在谁调用它**,所以只能钉位置:递增在 `if 本轮有图` 之前、全文件仅一处递增、递增后紧接着 prune。HEAD 负对照 = 69/71 红 2 条,正是那两条位置锁;第 12 组 = **「不收图」判据用真机抓回的报错原文做断言**:`Unexpected item type in content`、`item must be dict and key[type]` 必须判住(纯文字档就是这么回的,里面没有 image/vision/not support 任何词,旧判据一条都不匹配 → 每轮 400 也永不退回转述),而"几何限制 / 型号不存在 / 鉴权失效 / 含 vision 的无关报错"四种**不许**判成不收图 —— 最后一条专治旧 ask_ai 那份 `*vision*` 单条件的误杀(一次拼错型号名就永久关掉通道);另有结构锁钉"判据只一处定义、兜底必须改名 .gone 不得 rm .url、正向判据必须看 image_tokens 而非只看有回复")。HEAD 负对照 = 76/86 红 10 条钉住 `read_image` 与**用户发图 `@`/`-img`** 把原图交给主模型这两条链(第 9 节专测"按轮保留"与"单条消息多标记":两处缺陷同源 —— jq `capture` 对一段文本只返回第一个匹配,**采集端 `img_build_map` 与展开端 `_JQ_EXPAND_REFS` 必须一起改**,只修一半时现象会从"图不对"变成"图更少",比不修更会骗人;HOLD 的单位是**轮**不是张,否则用户一次甩来四张面板时前三张会在同一轮消失):按像素封顶、`.url` 尾字节必须是 `3d` 而非 `0a`(base64 结尾换行会混进手拼 JSON,展开静默失败 → 表现为"模型只看到一行标记、没有图")、live 展开必须真出 `image_url` 部件、**外来同款字面量一个字都不许改写**(没发过号的 id 不进 map)、回收走 `.url`→`.gone` 改名且 gone 形状含"痕迹/路径事实/重看义务"三件、**禁止任何"图还附着"类现在时断言**、`IMG_INLINE=0` 时 attach 必须被拒、无引用时请求体与 HEAD 逐字节相同。负对照:对 HEAD 跑本闸门 = 47/54(红 7 条全落在第 9 节的新向量上),且**必须给出 RESULT 行** —— 上一版在这里崩过一次(`set -u` 下取空数组元素),而**崩比红更坏:红会指路,崩只会把人带去查错方向**。变异验证四条各自可杀(撤 `tr` 红 16 条 / 去掉发号守卫红 2 条 / gone 丢义务红 1 条 / 改回删文件红 4 条)。⚠ 捕获请求体时重定向要放在**子 shell 内层**:`source` 主脚本会连带装上那条打印光标转义的 EXIT trap,放外层会把 `\033[?25h` 收进请求体尾巴,`jq -e` 判非法而单键取值照样成功 —— 现象是"一半断言红一半绿",极难归因(实测踩过) |
| 历史渲染闸门(原仓库根 `test_history_render.sh`) | `bash tests/test_history_render.sh` | **6/7,`FAIL T1b 消息丢失 — head=0 tail=0` 是既有红,与本轮搬移无关**:同一份脚本配 `git show HEAD:wchat.sh` 在干净目录里复跑,红得一模一样(它要真 pty/交互环境)。搬动时只改了一处 `cd "$(dirname "$0")/.."`(退回仓库根找 wchat.sh 与 render_helper.py)。**不在 run_gates 的 12 道之内**,别把它当套件红去查 |
| pty 分页闸门(原仓库根 `test_pty_pager.sh`) | `bash tests/test_pty_pager.sh` | `PASS=7 FAIL=0 SKIP=0`;同样只改自定位一行。不在 12 道之内 |
| 写熔断闸门 | `bash tests/write_fuse_test.sh` | 28 条(**不联网**;第 6 组是回归锁 —— **全新会话、日志文件不存在时也必须建文件并计数**:刀3 把"检查时记账"拆成"只判 + 成功后记"两处,两边都写了 `[[ -f "$log" ]] || return 0` 却没人创建该文件,于是整个会话里熔断 100% 静默失效;而当时 24 条断言全绿,因为每条向量都先 `: > "$L"` 建好了文件 —— **夹具把 bug 复制成了前提**。这一组刻意不预建,且在带 bug 的版本上必红);在临时工作根里 source 被测脚本,跑真实的 `tool_write_file`/`tool_edit_file`):F1 超上限、**上限=0 是"关闭"不是"全拒"**、非 auto 不判、F2 来回改必拒、**线性推进不许拒**(误杀正当重构就是这条在挡)、F3 同内容连写必拒、远期重复 `A B C A` 不算来回改、**旧格式行仍计数**(升级不许把刹车解除)、失败编辑不记账、record 只在两个写工具里(结构锁)、键两步归一同源(接线锁)。负对照:对 `git show HEAD:wchat.sh` 必须红(实测 17/24 红)。六条变异各自可杀:检查时追加 6 / 计数退回 `grep -cxF` 1 / 拆 F2 2 / 指纹带空格 1 / 键退回原始 `$p` 1 / 上限改回 8 1。⚠ 三条踩过才写进来的坑:① body 是在**内层 bash** 展开的,`$W` 必须显式传进去,否则路径塌成 `/root/...`、报出"记账没发生"这种看着像产品坏了的假信号;② 比键必须走 `pwd -P` 物理路径(`/var/…` 与 `/private/var/…` 是同一目录两种写法);③ 结构锁要先剔注释再数 —— 熔断块注释里本来就要讨论 `record`,不剔就是假红 |
| 审批链单测 | `bash tests/auto_test.sh` | `ck` 系列:缺 reason 直接 DENY 等 |
| MCP 通道闸门 | `bash tests/mcp_channel_test.sh` | `-mcp` 开关与暴露面(排除别家程序 import 进来的 server)、名字校验拒 `-y`/`../`、L0 幂等注入与超预算折叠、`mcp_call` 的成功/isError/CLI 失败/图片丢弃四分支、**会话级授权的四种形状**(默认只问 1 次 / `always` 问 3 次 / auto 不问 / 被拒不记账)、**次数刹车的默认形状**(默认不限、显式设上限才生效、"不限"绝不写成"上限 0 次"、标记不带 `/0` 分母)、**通用暴露面**(参数差异不改变暴露、实际命令行逐字摊出、缺 description 被点名、源码零第三方专名与 flag 字面量)、**启动失败的五种形状**(秒失败/慢失败/在线但空/上游缺席/名字不合法各自措辞,`durationMs` 与上游 `counts` 必须转述;点名打错不许静默;失败原因**只走 stderr**,有反向锁禁止它进 L0/`tools()`)、次数刹车、无人值守范围闸、`tools()` 关闭时逐字节不变。**全部打桩**:不依赖 npm/Chrome,可离线跑 |
| 技能目录外置闸门 | `bash tests/skills_dir_test.sh` | SKILLS_DIR 覆盖/退回、三条目录校验、链接守卫三侧配对、`-skill` 的 name 清洗。负对照:`--source .scratch/base/wchat.sh`(未打补丁版应**拒跑**并报"沙箱漏抽") |
| 终端应答泄漏闸门 | `bash tests/tty_osc_input_test.sh` | 50 条:五类应答丢弃、真按键/粘贴正文一个不吞、DSR 按 CPR 配额放行、`input=` 接线、pty 里真起一次输入助手看交回文本,第 7 节另管**输入语义**(回车=发送;行尾 `\`+回车=续行且反斜杠不许留在正文里;空缓冲区回车不发送不退出;行中反斜杠不触发续行)。负对照两条:`HELP=.scratch/mut/pristine.py`(`git show HEAD:input_helper.py`)→ 报"沙箱漏抽";整表对 HEAD 版 helper → 8 条红(含第 7 节全 4 条),再只去掉 `delete_before_cursor` 一行做变异 → 恰好红 1 条且正是"反斜杠被吃掉"(实得 `abc\\\ndef`)。变量名是 `HELP` 不是 `HELPER`,传错会一路假绿(实测踩过) |
| Python 迁移通道(**已拆分·W0–W9**) | `bash tests/py_channel_test.sh`(通道机制 4) + `tests/py_<模块>_test.sh`(各模块 parity) | 原单体 221 条已**机械拆分**成"共享前奏 `tests/py_prelude.sh` + 每模块一个 gate",合计仍 221、逐文件全绿;`py_channel_test.sh` 缩为通道机制 4 条(开关/回退/哨兵)。各模块 gate:`py_textutil`(27)/`py_authparse`(22)/`py_hd`(98)/`py_skill`(14)/`py_mcp_client`(22)/`py_img`(24)/`py_compaction`(18)/`py_msg`(19)/`py_history`(27)/`py_filefmt`(22)/`py_pathutil`(49,W3)/`py_config`(16,W12-1)/`py_pathrule`(25,W12-2)/`py_authz`(20,W12-3·已收口)/`py_llm`(20,W12-4a)。**目的**:增删/收口模块只动各自小文件,不再反复改大文件(解**路径熔断**死结);收口样板见 `tests/webs_py_test.sh`。⚠ 各 gate 各 source 主脚本多次,`py_hd` 最慢(~3min),务必后台跑。坑:①`truncate_output` 读**全局** `TOOL_OUTPUT_MAX_*`;②**`python3 -m` 的 cwd 会遮蔽本包** → 用绝对路径启动器 `wchat_py/_run.py`;③BSD `tail` 不补末行换行;④重定向别把 `"$outf"` 写成 `'$outf'`;⑤`source`/抽函数式闸门须有 `_py_on` 回退桩;⑥谓词 `rc=1` 是业务结果、不回退;⑦`clip_bytes` 返回带尾换行的"流",`$(...)` 要自己剥;⑧parity 会把既有隐 bug 也钉住(`\n\n` 字面两字符);⑨`$'...'` 别套外层双引号;⑩大内容走临时文件/stdin;⑪`jq -r` 回读记得 `-j`。方案见 `docs/migration-to-python.md` |
| 硬墙判据 Python 路径闸门(**新增·W2b**) | `bash tests/hardwall_py_test.sh` | 2 条,但每条是一次**整表**:A) `HW_PY=1`(真实 `_py_on`/`py_run`,`HW_PY_DIR` 可指包位置)跑 569 向量 → 期望 `569/569`(已迁谓词走 Python,未迁的照旧 bash);B) **负对照/接线证明** —— 把 `wchat_py/hd.py` 的 `mask_readonly` 改成原样返回再跑一遍,期望表**变红**(实测 `560/569`,T3 那 9 条红)。没有 B,A 可能只是"python 路径静默退回 bash"的假绿。⚠ 慢(跑两遍整表),用后台通道 |
| 网络搜索 Python 路径闸门(**新增·W8·已收口**) | `bash tests/webs_py_test.sh` | 15 条:`web_search_build_body`(mimo/qwen/未知)+ `fetch_error`(000/403/429/4xx/5xx/其它)的**收口后形态** —— 语义锚点 + **函数体内结构锁**(旧 bash `case` 回退确已删、只剩一行 `py_run`)+ **不可用降级**(`WCHAT_PY_WEBS=0 → build_body rc=1`,不静默)。**单列成文件的原因**:主迁移闸门 `tests/py_channel_test.sh` 本会话改动过频,被**路径熔断(F1/F2)**拦写 —— 新模块一律开独立 parity 文件;收口其它模块前需先把主闸门改成数据驱动向量表 |
| 视觉转述 Python 路径闸门(**W6·已收口**) | `bash tests/vision_py_test.sh` | **25 条**:`vision_build_body`(有/无 system、parts+text 拼接、model/thinking/stream)+ `vision_parse_resp`(字符串/数组/null/缺字段/非法 JSON/非 text 部件)的**收口后形态**——语义锚点 + **函数体内结构锁**(旧 jq 回退确已删)+ **不可用降级**(build_body rc=1 不产半截体、parse_resp 空串)。独立文件;`vision_layer_test.sh`/`vision_e2e.sh` 的沙箱已接 `_py_on`/`py_run` 生产路 |

**本轮把仓库根的四张表搬进 `tests/`**:`test_hardwall.sh`、`test_history_render.sh`、`test_pty_pager.sh`、`tty_e2e_check.sh`(用 `git mv`,历史保留)。搬它们不是因为写错,是因为根目录只剩产品与这四张表,而四张表全都用 `cd "$(dirname "$0")"` + `$PWD/wchat.sh` 定位被测源—— 直接搬会变成"找不到被测源文件"(就是 `skills_dir_test.sh` 现在这副样子)。所以每张都改成 `cd "$(dirname "$0")/.."` 退回仓库根,并同步改了引用点:`run_gates.sh`、`mutate_call_site.sh`(MUT_SUITE 默认值)、`rulefail_check.sh`、`traffic_replay.sh`(它按 `^WORK_ROOT="$PWD"` 的行号从硬墙表里**抽装载片段**,路径不改就抽空)、`tests/README.md` 本身,以及 `wchat.sh` 注释里的 5 处指向。另修了 `test_hardwall.sh` 的 `HW_SELF`(`$PWD/$(basename $0)` 搬目录后会指到不存在的路径,而三条结构守卫靠它 grep 表自身)。验证方式:从 `/tmp` 调用这张表,照样全绿。

`tests/tty_e2e_check.sh` 是**要真人终端**的手动探针(测 bash 侧 `tty_*` 函数,判"不漏也不吞")。
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

## 四条硬约定

1. **新增回归脚本当轮就 `git add`**。只留在工作区的回归资产等于没有资产 ——
   这条本来是我准备用来解释"表丢了"的理由,结果发现自己连"丢"都是错的:
   真正丢的那类风险,是靠入库而不是靠记性来消的。
2. **总结里报数字,必须同时记下产生它的命令原文与文件名**。压缩之后残留的
   数字会挂在错的名字上,然后就会发生这轮这种事:我把**单道 MCP 闸门**的结论行
   当成全闸门结论报了出去,而同一轮的全表其实是 `568/1`。结论行只覆盖它自己那道。
3. **别用 `grep '^PASS='` 判断套件跑完**(harness 1955 行已解释:那行可能带
   ANSI 前缀,行首不是 P)。判"跑完"要看 `RESULT …` 行里 `src=` 的 sha ——
   它同时能证明你测的是哪份源码(变异脚本被打断导致源码停在突变态,坑过一次)。
4. **断言要区分"源码里有这句话"与"这句话真会执行"。** 只 grep 源码的断言,把它的
   `if` 条件改成恒假仍然绿 —— MCP 授权常驻标记那条就是这么被变异 3 逃过的
   (`93/93` 全绿,而标记已经永远打不出来了)。凡"某句话会显示/写入/触发"类断言,
   一律走**行为断言**:在夹具里真调那个函数、对它的输出做匹配;grep 源码只配当接线粗筛。
4b. **比对式自己也会错,红之前先怀疑它。** 本轮三次:① 复用 `_strip_ctl` 做行结构断言,而它里面有
   `tr -d '\n'`(为"整值相等"设计的),整个输出被压成一行 → `^  · 提醒` 永远 0 命中,看起来像"代码缺换行";
   ② 捕获时没剥 ANSI 就拿 `^  \[mcp\]` 锚定; ③ 成员判断用 `" $X "` 包夹,而那个变量是**换行**拼的
   → 三个正常 server 全判"未启用"。**"断言说没有、人眼看明明有"时,先查比对式,再查代码。**
5. **改了"通用化"这类判据,必须同时留反向锁并换形实测。** 只断言"新话术在"是不够的:
   旧话术以后会被"顺手加回来"。MCP 这轮的形状是 —— 正向锁断言通用分工句在 L0/`mcp_call` 描述里,
   反向锁断言浏览器语汇(`真实浏览器`、`文件:行号`、`点击/输入/拖拽`、`渲染后的 DOM`)计数为 0,
   并**用另一种 server 形状的夹具真渲染一遍清单**(chrome 形与 figma 形对照)确认措辞不再偏向某一个。
   同理,"某第三方专名不该出现在源码里"用 `grep -c == 0` 是**正确**形状:缺失性断言不存在
   第 4 条那个"存在≠会执行"的坑。

> **W12-4c（`ask_ai` 流式改官方 SDK）基线**：`py_llm` **20/20 → 33/33**（新增收口锁：`py_run llm stream`
> 存在=1、旧 curl 主请求块=0）；全表仍 **569/569 fail=0**（py 模块 gate 的 `ck` 不计入全表总数，无需动
> `EXPECT_TOTAL`）；主套件断言 **+4**（e2e 新增：URL 正/反锁各 1、`thinking.type` 未被双包 1、
> `屏幕无'API 请求失败'` 1 —— 最后这条钉的是"`{"t":"done",...}` 掉进非事件兜底被当 API 错误显示"）。
> **新增闸门 `py_request`（请求建体 parity）29/29，闸门总数 31 → 32**；全量对账
> `run=32 pass=32 fail=0`，`sha=6704094fb60c`（W12-4b-2 期间）。
> **四条"形状契约"教训**（凭据不 export / URL 是完整端点 / `thinking` 对象 vs 标量 / 夹具必须用生产形状）
> 见 `docs/migration-to-python.md` §8.5 —— 它们全是"夹具形状与生产不同"造出来的假绿。
> **代价**：每请求 +0.64s 冷启动（实测 3 次稳定），因此
> `tests/auto_test.sh` 的 pty **总时限 30s → 60s**（静默阈值 2.5s 不动）。红了先看是不是又被时间预算咬到：
> 判别法是"只放总时限、不动阈值"，若转绿就是时间预算，不是断言错。
> **W12-4b-2（图片展开/采样解耦）基线**：`inline_image` **293 → 298**（新增 5 条：`img_token_sample`
> 此前**无任何覆盖**，先写测试在旧实现上全红、再改实现转绿）；`请求建体` **29/29**（六族展开 parity）；
> 全量 `run=32 pass=32 fail=0`，`sha=6704094fb60c`。**教训**：拍照计费这类路径的失败是**静默**的
> （`parts!=1` 就 `return 0`），所以断言必须看"表里有没有新增一行"，不能看"有没有报错"。

> **W12-4b 收口（请求体构建归 Python 唯一实现）**：删除 jq 版 `make_request_body`（103 行）与三个死字段；
> 4 个调用点（3 处 `request_body=$(…)` + 1 处内联 `ask_ai "$(…)"`，**形状不同**）全部切到 `make_request_inputs`；
> `inline_image` 第 7 节改为断言 Python 建体、删掉"与 HEAD 逐字节"（职责由契约断言承担）；
> `请求建体` parity 窗口关闭后转**契约断言**（键顺序/thinking 标量/effort/无转义/展开 parity）+ 收口锁。
> 基线：`请求建体` **30/30**、`inline_image` **297/297**、`压缩布局` 37/37、全量 **run=32 pass=32 fail=0**，
> `sha=a9abdfd0ee0c`(W12-4d 注入与 -n 切换后重取)。
> ⚠ 收口连带损伤一处（全量对账才照出来）：`压缩布局` 的接线锁钉的是"压缩后立刻重建请求体"的调用点，
> 函数改名后失效（37→36）——**单跑各道全绿、整体跑才红**，这就是全量的价值。

> **W12-4d 阶段基线**: `inject_mcp`/`inject_auto`/`-n` 初始化切到 Python(`inject_skills` 与 641 顶层按"装载胶水"保留 bash,依据 `mcp_channel_test.sh:711` 既有锁);`消息构造` **22/22**(+3:inject 幂等/init 形状);新增**回滚安全带**(桩记 `orphan_tools`,e2e 断言拒绝后无孤儿 tool 消息,对"整份复制"与"快照 id"两种实现都成立);全量 **run=32 pass=32 fail=0**,`sha=a9abdfd0ee0c`。

> **W12-4d 存储层基线**: 新增闸门 `消息存储`(`py_msgstore_test.sh` **7/7**),闸门数 **32 → 33**;
> 全量 **run=33 pass=33 fail=0**,`sha=a9abdfd0ee0c`(`msgstore` 未接线,`wchat.sh` 未动)。
