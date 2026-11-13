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
GATE_NAMES=(语法 全表断言 判据出口话术 压缩布局 审计字段 形状向量 技能目录 MCP通道 流量回放 主套件 内联图片 写熔断 py通道 硬墙双路 网络搜索 视觉转述 py工具 认证解析 硬墙谓词 技能解析 MCP客户端 图片逻辑 压缩py 消息构造 历史变换 文件格式 路径归一 配置打包 路径裁决 会话授权 LLM适配 请求建体 消息存储 权限状态 工具规格 工具运行 硬墙语料 硬墙接缝 终端地基 终端引擎 工具完整性 编排轮次审计 历史存档名 Ctrl-C中断 发行包 确认链 H14范围 首次引导 录入终端 上下文体积 安装完整性 渲染字节 流箱屏幕 子命令 运行时自举 调用记账 工具参数 工具流解析 UTF8完整性 read文件分页 分层结构 Python版本 可移植性 deb打包 apt仓库 非正文灰化)
GATE_CMDS=("bash -n wchat_ref && bash -n tests/test_hardwall.sh && bash -n tests/run_gates.sh && echo SYNTAX-ok-3-files" "bash tests/hardwall_py_vectors.sh" "bash tests/rulefail_check.sh"
           "bash tests/compaction_pin_probe.sh" "bash tests/audit_field_probe.sh"
           "bash tests/probe_shapes.sh" "bash tests/skills_dir_test.sh"
           "bash tests/mcp_channel_test.sh"
           "bash tests/traffic_replay.sh" "bash tests/auto_test.sh unit"
           "bash tests/inline_image_test.sh" "bash tests/write_fuse_test.sh" "bash tests/py_channel_test.sh"
           "bash tests/hardwall_py_test.sh" "bash tests/webs_py_test.sh" "bash tests/vision_py_test.sh"
           "bash tests/py_textutil_test.sh" "bash tests/py_authparse_test.sh" "bash tests/py_hd_test.sh"
           "bash tests/py_skill_test.sh" "bash tests/py_mcp_client_test.sh" "bash tests/py_img_test.sh"
           "bash tests/py_compaction_test.sh" "bash tests/py_msg_test.sh" "bash tests/py_history_test.sh"
           "bash tests/py_filefmt_test.sh" "bash tests/py_pathutil_test.sh" "bash tests/py_config_test.sh" "bash tests/py_pathrule_test.sh" "bash tests/py_authz_test.sh" "bash tests/py_llm_test.sh" "bash tests/py_request_test.sh" "bash tests/py_msgstore_test.sh" "bash tests/py_state_test.sh" "bash tests/py_tools_test.sh" "bash tests/py_tools_run_test.sh" "bash tests/hdcmd_corpus_test.sh" "bash tests/hardwall_seam_test.sh" "bash tests/term_test.sh" "bash tests/term_suite.sh" "bash tests/tools_complete_test.sh" "python3 tests/orchestrate_e2e.py" "python3 tests/history_list_test.py" "python3 tests/interrupt_e2e.py" "bash tests/brew_dist_test.sh" "python3 tests/authflow_test.py" "python3 tests/h14_scope_test.py" "python3 tests/setup_test.py" "python3 tests/setup_tty_test.py" "python3 tests/ctx_budget_test.py" "python3 tests/pkg_integrity_test.py" "bash tests/py_render_test.sh" "python3 tests/streambox_test.py" "python3 tests/subcmd_test.py" "python3 tests/bootstrap_test.py" "python3 tests/apilog_test.py" "python3 tests/toolargs_test.py" "python3 tests/toolstream_test.py" "python3 tests/utf8_test.py" "python3 tests/filefmt_paging_test.py" "python3 tests/layout_test.py" "python3 tests/pythonver_test.py" "bash tests/portable_paths_test.sh" "python3 tests/deb_pkg_test.py" "python3 tests/apt_repo_test.py" "python3 tests/toolui_test.py")
GATE_MODE=(c x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x c c c c c c c c c c c c c c c c c c c c c c x c c c)
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
         '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '基线与当前一致' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$' '^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
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
           'filefmt.read(M 制拆分·已收口)' 'pathutil expand_tilde/norm_path/resolve_path/in_root_exempt(W3·已收口)' 'py_cfg_dump 配置导出 + schema 一致性(W12-1)' 'hard_deny_path 纯判定化(W12-2·已收口)' 'is_granted/auto_allow_hit 会话授权(W12-3·已收口)' 'llm.py Seam 编码/kwargs/依赖探测(W12-4a)' '请求建体 parity(Python 建体 vs jq 逐字节)' 'messages 文件 store(回滚=截断/原子写/损坏抛错)' '权限会话态形状(幂等/顺序/rw⊃r/空串)' '工具注册表(原样字节/两句独立变换/组合不丢 MCP)' 'tools_run 五工具 parity(content/状态行/落盘字节)' '硬墙差分语料 44 条(bash 基线稳定; Python 版 parity 的 oracle)' '硬墙接缝(前端 WCHAT_PY_HARDWALL=1 vs 0 判定等价)' '终端层地基: raw_mode 三路退出恢复 termios)' '终端引擎: raw/pty/信号/run_shell_command 四用例' '工具完整性:tools_spec 每个工具都有实现(行为断言)' '编排:轮次上限缺省 250/撞顶不静默/审计空路径落 CT_HOME' '历史文件名只有一个 .json(-fs 不许出 xxx.json.json)' 'Ctrl+C 打断流式输出:回输入框/不刷栈/已完成部分保留(半截正文留、半截tool_calls不落库)' '发行包:可复现/无字节码/装后能跑/Formula 一致/首次运行生成 MCP 清单' '确认链:硬墙yes不再问/同命令不重问/路径记账不重问' 'H14 范围:叙述文本不算装包/真安装仍拦/包名不跨段' '首次引导:缺项只提示不进循环/逐项问/校验指到点上/600落盘/强制验连通' '录入终端:真 pty 里 key 能收进去/key 回显/整段粘贴不丢值' '上下文体积:工具输出列宽+行数截断/MCP字节帽/标题兜底/轮内压缩/越界自愈' '安装完整性:包内不缺模块/解释器失效可兜底/缺模块提示可读/随包资产齐全且 Formula 都装上' '渲染字节:reserve/paint/close 逐字节 + 禁 CSI s/u 存光标 + close 擦净' '流箱屏幕:阶梯重复/右移在四种终端能力组合下都不出现' '子命令:model 一份实现/MCP 清单收进 .ct 并迁移/技能只补缺不覆盖/模板缺失报安装不完整/-mcp 首跑先补清单' '运行时自举:断链自愈/锁不重跑/版本校验/安装失败停住' '调用记账:不记正文/归因标签齐全/工具定义成本/审计判据' '工具参数:必填缺失必须报错(spec 为真源)/空命令不执行/横幅不留白' '工具流解析:分片缺 index 也归同一 slot(对齐 master)/空名调用不进执行' 'UTF8完整性:合法输入不产生替换字符/pty 先 join 再解码' 'read_file 分页:limit 生效/只给 offset 也前进(零进展检测)/越界明说/负 offset 归 1/头部不许谎报区间' '分层结构:cli 行数上限/已切出函数不许回流/下层不 import cli/任意导入顺序/门面同一对象/账本不重新绑定' 'Python版本:shebang 只管"要个 python3"/brew wrapper 不读 shebang(实验双向)/低于下限自动换解释器/兜底也要过版本校验' '可移植性:夹具不写死 mac bash 路径/fixtures 软链必须相对(两条都实测假红过)' 'deb 包契约:control/python3-venv/postinst 离线幂等/prerm 删 venv/wrapper 指 venv/清单与 brew 同源/验收不挂源码' 'APT 仓库:pool+dists 结构/Filename 必须仓库相对(上线才炸)/Release 双 hash/InRelease+Release.gpg/验收真走 apt 不绕 dpkg' '非正文输出统一灰化:横幅/输出/状态行各≤1行 + 拒绝给理由不回显命令 + 正文不灰(对照)')
# 新闸门用 += 追加,**不改上面的字面量**:那五个数组按位置一一对应,
# 在长行中间插一个字面量极易错位(插错位置会得到一条假绿,本项目已踩过同类)。
# 追加到所有数组末尾,顺序天然一致。
GATE_NAMES+=('MCP启动')
GATE_CMDS+=('python3 tests/py_mcp_boot_test.py')
GATE_MODE+=('c')   # 复合命令(python3 ...)必须走 bash -c,选 x 会变成 `bash python3 ...` → rc=126
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('MCP 启动链:per-server 超时预算/失败当场放锁且不注册死工具/不重复枚举(全离线)')
GATE_NAMES+=('思考显示')
GATE_CMDS+=('python3 tests/think_view_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('思考文本:默认两行(三角+首行,其余 …)/-tf=on 整段/启动与会话内同一真值表/非法值明说')
GATE_NAMES+=('输出折叠')
GATE_CMDS+=('python3 tests/shell_view_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('shell 输出箱跟随 -tf:默认只留收尾行(-tf=on 展开)/上下文仍全量/-sf 已并入且会明说')
GATE_NAMES+=('输入框形状')
GATE_CMDS+=('python3 tests/inputbox_layout_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('输入框:两横线紧贴且同宽(宽度取自本帧)/配色全 dim/折行与窄终端不裂/resize 补 reflow 行数')

GATE_NAMES+=('装技能')
GATE_CMDS+=('python3 tests/skill_install_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('第三方技能安装:四形态发现/落盘前校验/解引用软链/逃逸拒绝/记账/dry-run/只备份不删')

GATE_NAMES+=('项目记忆')
GATE_CMDS+=('python3 tests/memory_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('项目记忆:按 git 根隔离/索引超限报错不静默截断/拒收凭据/注入带边界声明/工具四动作/CLI')

GATE_NAMES+=('MCP加删')
GATE_CMDS+=('python3 tests/mcp_cmd_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('mcp add/remove:只插入一段(注释/缩进/键序/末尾空行照抄)/600/拒明文密钥/重名不覆盖/坏JSON不顺手修')

GATE_NAMES+=('shell孤儿')
GATE_CMDS+=('python3 tests/shell_orphan_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('shell/pty 孤儿:SIGTERM 主动 killpg(不靠 SIGHUP)/pgid 用后归零/超时三形态/SIGKILL 边界如实记录/nohup 不清')

# 这两道曾经不在闸门清单里,于是随架构演进(图片改 [[IMGREF]] 标记、-skill 落地)悄悄漂红
# 没人察觉。接进来后,类似"测试停在旧设计"的退化会被当场抓住。
GATE_NAMES+=('会话编排')
GATE_CMDS+=('python3 tests/session_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('Session.from_env/usage/orchestrate(工具续发+Ctrl+C保留语义)/存盘命名/图片两层(会话标记→请求image_url)/auto状态(纯逻辑)')

GATE_NAMES+=('REPL循环')
GATE_CMDS+=('python3 tests/repl_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('run_repl:文本/元命令分发/-q停止/默认handler真实现/session跨轮/write_final=False/on_quit')

GATE_NAMES+=('文档读取')
GATE_CMDS+=('python3 tests/docread_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('docx/pdf→Markdown(read_file集成):类型判定/真实转换/扫描件判空/.doc给提示不吐乱码/未装markitdown降级/缓存命中/Ctrl-F自动读小文件/超限退回分页/pdfminer字体警告压制')

# M3①:Textual TUI 的两道闸门(功能 + 视觉/布局)。它们会**自举**:本机 system python3 无
# textual 时自动换到 ~/.ct/venv(或 WCHAT_TUI_PY)重新执行;找不到带 textual 的解释器才
# 打印 SKIP + exit 77(环境性缺席,不计失败)。所以并入套件不会恒 SKIP。
GATE_NAMES+=('TUI视觉')
GATE_CMDS+=('python3 tests/tui_visual_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('TUI 布局不变量(run_test 真布局引擎):输入框贴底整宽/内容区从顶起且顺序堆叠不重叠/resize 一组尺寸后仍单输入框且贴底/状态区位置')

GATE_NAMES+=('TUI功能')
GATE_CMDS+=('python3 tests/tui_test.py')
GATE_MODE+=('c')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('TUI 交互端到端(headless run_test):续行/流式定稿进内容区/思考折叠/确认弹窗 y·n/InputModal 桥/元命令 -auto=full/粘贴图占位')

# 真终端 resize 冒烟(tmux)。tmux 缺 → SKIP 77。守"转义序列在真终端模拟器里渲染成单框贴底、
# 不出残影/多框"——这层 run_test 的 region 断言管不到(它只改 app 尺寸,不验真终端重绘)。
GATE_NAMES+=('TUI终端')
GATE_CMDS+=('bash tests/tui_term_test.sh')
GATE_MODE+=('x')
GATE_PAT+=('^RESULT total=[0-9]+ pass=[0-9]+ fail=0$')
GATE_NOTE+=('TUI 真终端 tmux resize:多尺寸后每次恰好 1 输入框/宽=新宽/贴底(真终端模拟器重绘,非手搓屏;需 tmux,缺则 SKIP)')

# ⚠ 新闸门一律**追加在末尾**,别插在 主套件 之前:上面两处 `i == 9` 是按索引认主套件的
#   (LIST_ONLY 与执行循环各一处),插中间会让"跳过慢闸门"的特例指到别的闸门上 ——
#   这种"索引当身份证"的写法本就脆,但一次改动只治一个病,这里先不顺手重构。

want() {  # 有没有被参数选中
    (( ${#FILTERS[@]} == 0 )) && return 0
    local f n; for f in "${FILTERS[@]}"; do [[ "$1" == *"$f"* ]] && return 0; done; return 1
}

# 环境性缺席的**唯一**表达方式:套件打印一行 `SKIP: 原因` 并 exit 77。
# 为什么要有它:有些闸门天生依赖"这台机器上恰好有的东西" —— 真实流量语料、
# macOS 专有工具、需要 root/非 root 的前提。**没有语料就报绿是撒谎**(所以
# traffic_replay 一直很正确地拒绝),可报红又会被当成"产品坏了",于是每次
# 换机器都要重新做一遍归因(本项目已经栽过:容器里 `MCP通道` 37 条一起红,
# 真因是没装 node)。77 让"不具备"成为一个**显式、可审计**的第三种状态:
# 它会打印在结论行里(skip 计数),想让它真跑的人看得到,也不会被当成绿。
EXIT_SKIP=77
skip_reason() {  # 从日志里取 `SKIP: ...` 那行
    grep -a -m1 '^SKIP:' "$1" 2>/dev/null | sed 's/^SKIP:[[:space:]]*//'
}

if (( LIST_ONLY )); then
    for i in "${!GATE_NAMES[@]}"; do
        (( i == 9 && ! RUN_MAIN )) && printf '  %-10s %-34s (跳过,--main 才跑)\n' "${GATE_NAMES[$i]}" "${GATE_CMDS[$i]}" && continue
        printf '  %-10s %-34s %s\n' "${GATE_NAMES[$i]}" "${GATE_CMDS[$i]}" "${GATE_NOTE[$i]}"
    done
    exit 0
fi

LOG=$(mktemp -d "${TMPDIR:-/tmp}/gates.XXXXXX"); trap 'rm -rf "$LOG"' EXIT

# --- 并发互斥:同一时间只允许一份 run_gates -------------------------------------
# 为什么需要:跑两份会互相抢 pty 与信号状态,得到"看起来产品坏了、实际是测试打架"。
# 但**更常见**的假红来源是另一件事(2026-11 发布前实测):
#   用 `nohup … &` / 后台任务跑闸门时,bash 会把后台作业的 **SIGINT 设成 SIG_IGN**,
#   这个处置会被所有子进程继承(POSIX:被忽略的信号跨 exec 保留)。于是:
#     · `MCP启动` 的"不抢 SIGINT"断言看到 `getsignal(SIGINT) is SIG_IGN` ⇒ 红;
#     · `MCP加删` 的 Ctrl+C/pty 用例收不到 SIGINT ⇒ 挂到超时(HUNG= True)。
#   前台跑同样代码 100% 绿 —— **这不是产品 bug,是运行方式的问题**。
#   所以:闸门必须**前台**跑(发布脚本 packaging/release.py 本来就是前台调用)。
_LOCK="${TMPDIR:-/tmp}/.wchat_gates.lock"
if ! mkdir "$_LOCK" 2>/dev/null; then
    _held=$(cat "$_LOCK/pid" 2>/dev/null || echo '?')
    if [ -n "$_held" ] && kill -0 "$_held" 2>/dev/null; then
        echo "⚠ 已有一份 run_gates 在跑(pid ${_held}),并发跑会互相干扰(pty/信号)。" >&2
        echo "  等它跑完再跑,或先 kill ${_held}。" >&2
        exit 2
    fi
    rm -rf "$_LOCK"; mkdir "$_LOCK" 2>/dev/null || true
fi
echo $$ > "$_LOCK/pid" 2>/dev/null || true
trap 'rm -rf "$LOG" "$_LOCK"' EXIT

SRC_SHA=$(sha256sum wchat_ref 2>/dev/null | cut -c1-12); [[ -n "$SRC_SHA" ]] || SRC_SHA='<无sha256sum>'
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
    if (( rc == EXIT_SKIP )); then
        skip=$((skip + 1))
        printf '  \033[33m⏭  %-8s\033[0m %s\n' "$name" "$(skip_reason "$out")"
    elif [[ -n "$concl" && $rc -eq 0 ]]; then
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
        # 只留 3 行不够定位(2026-11 发布前:两道闸门偶发红,尾巴里全是 ok,看不到
        # 到底是哪条断言的错)。把完整日志留一份到固定路径,便于事后查证。
        _keep="/tmp/wchat_gate_fail_${name//\//_}.log"
        cp "$out" "$_keep" 2>/dev/null && printf '      │ 完整日志: %s\n' "$_keep"
    fi
done

printf '\n\033[1m── 合计 pass=%d fail=%d skip=%d · 源码 %s ──\033[0m\n' "$pass" "$fail" "$skip" "$SRC_SHA"
printf 'GATES run=%d pass=%d fail=%d skip=%d sha=%s\n' "$((pass + fail))" "$pass" "$fail" "$skip" "$SRC_SHA"
(( fail == 0 )) || exit 1