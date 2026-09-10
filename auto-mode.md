# wchat auto 模式 —— 设计、实现与维护指南

> 写在前面：这份文档同时给两个人看——**你**（用它决定敢不敢开、开到哪档）
> 和**下一次改这套代码的我**（避免把已经踩过的坑再踩一遍）。
> 行号会变，**函数名和文件路径才是锚**；改任何一条规则前，先读第七节。
>
> 落地于 2026-09-10，提交 `f757506`（分支 `feature/ptk_input`）。
> 主文件 `wchat.sh`（4944 行），本目录 `~/bin` 即已 push 到 github 的 git 仓库。

---

## 一、它解决什么

原来碰到敏感权限（工作目录外的文件、审批器判 N 的命令）会弹 `y/n` 等你点。
auto 模式把这些交给人工以外的东西决定，**但不是交给模型一句话决定**——
"AI 判断要不要给 AI 权限"是自我说服；人写死的白名单又跟不上模型临时起意。
所以是三条独立机制叠着判，任何一条都能单独把命令拦下来：

| # | 档位 | 放开什么 |
|---|---|---|
| 0 | 关（默认） | 什么都不放开，越界与判 N 一律弹人 |
| 1 | `read` | 只把**工作目录外的只读**交给 AI 自批。shell 判 N 仍弹人工。日常最推荐 |
| 2 | `sandbox` | + 工作目录内（含 `/tmp`、`$TMPDIR`）全自动；审批器判 N 的命令交终审器带 `reason` 复审。**默认档** |
| 3 | `full` | + 写 `--allow-write` 名单里的越界路径。名单外的越界写**任何档位都拒** |

> **auto 不是无限权限**。硬墙 11 条 + 路径红线 3 条在任何档位下都不放开，
> AI 没有批准自己权限的能力，`reason` 写得再好也过不去。它只是不再**问你**。

---

## 二、三层裁决（数据流）

```
模型发起 tool_use(run_shell_command / read_file / write_file / ...)
  │
  ├─[文件工具] resolve_path 在工作目录内 → 直接执行
  │            越界 → path_access(tier / 名单 / 凭据 → 只给读 → 落 r|path)
  │
  └─[shell] ① hard_deny_cmd   正则命中 → auto: 拒 + 回传规则号与替代方案(不弹人、不花审批请求)
                                 非 auto: 严格确认(只认 yes)
            ② auth_think      判 Y → 直接执行
                              判 N ─┬─ auto_mode 为空 / read 档 / 已降级 → confirm_cmd 问人
                                    └─ auto_takes_shell → ③ auto_final_judge
                                                            ALLOW → 执行 + 审计
                                                            DENY  → 拒 + 理由回传模型
```

**为什么必须有 ③**：实测 10 条被拒命令里 4 条是误判（`kill 12345` 被当"杀进程"、
相对路径 `../` 被当"越界"）。误判的代价不是危险，而是**卡住自动化**。

**为什么 ③ 只在 sandbox/full 生效**：`read` 档的语义承诺就是"shell 判定仍归人"。
这条实现时写错过（判成"只要 auto_mode 非空就接管"），被端到端测试抓个正着。

---

## 三、关键函数索引（`wchat.sh`）

| 函数 | 行 | 职责 |
|---|---|---|
| `kill_tree` | 1059 | 递归杀进程树（TERM→KILL） |
| `run_with_window` | 1068 | **工具命令执行器**：pty + 实时窗口 + 按键转发 + 墙钟超时 |
| `auth_think` | 2016 | 审批器（判 Y/N + ≤20 字理由），独立模型、裁剪上下文 |
| `expand_tilde` / `norm_path` | 2072 / 2245 | 路径归一化（**所有名单比对的唯一入口**） |
| `grant_file` / `is_granted` | 2133 / 2144 | 授权名单，条目格式 `r\|路径` / `rw\|路径` |
| `ask_path_grant` | 2169 | 人工确认路径越界（高危只认 `yes`） |
| `auto_audit` | 2231 | 单行 JSON 审计 |
| `auto_allow_hit` | 2292 | 命中 `--allow` / `--allow-write` 名单 |
| `auto_backup` | 2309 | 覆盖写前落备份 |
| `hard_deny_cmd` | 2364 | **硬墙 H1–H11** + H12 写目标扫描 |
| `hard_deny_path` | 2465 | **路径红线 P1–P3** |
| `auto_final_judge` | 2544 | 终审器（带 reason 复审 N） |
| `auto_note_deny` / `auto_note_allow` | 2591 / 2597 | 连拒降级 / 放行配额计数 |
| `auto_takes_shell` | 2610 | **单点判定**：auto 是否接管 shell 的 N |
| `path_access` | 2661 | 文件工具越界入口（6 个调用点收敛到这里） |
| `inject_auto` | 2738 | 把规则注入 `messages[0]`（压缩安全） |
| `auto_checkpoint` | 2754 | git HEAD 或 rsync 快照 |
| `auto_enable` | 2808 | 能力清单 + 签字 + 回滚点（tty 必需） |
| `auto_report_exit` | 2880 | 退出小结与回滚指令 |

其他入口：`parse_options`（863，`-auto` / `--allow`）、`handle_cmd_line`
（`-auto=` / `-noauto` / `-fs` / `-md`）、分发循环在 `tool_run_and_parse` 里
（轮次上限、`content=""` 清零、审计）。

---

## 四、硬墙清单（改它之前先看第七节）

### shell 命令 `hard_deny_cmd` → `H1..H11`

| 号 | 拦什么 | 为什么不能靠审批器 |
|---|---|---|
| H1 | `sudo` / `doas` / `su` | 提权后沙箱形同虚设 |
| H2 | `curl … \| sh`、`bash <(curl …)`、`eval "$(wget …)"` | 内容不可预知，事后无法回滚 |
| H3 | `rm -rf ~` / `$HOME` / `/*` / `/usr` / `--no-preserve-root` | 一条命令毁所有 |
| H4 | `dd of=/dev/*`、`mkfs`、分区 | 文件系统级 |
| H5 | 写/删 `~/.bashrc` `.zshrc` `.ssh` `.aws` `.gnupg` `~/Library/LaunchAgents` | 持久改变环境，不可回滚 |
| H6 | 读私钥 / `.env` / `~/.aws` / `.netrc` / `security dump-keychain` / `~/.ct/history` / `~/.ct/config` | 泄露即永久；内容会进上下文并随请求发给服务端 |
| H7 | `git push --force`、`push :分支`、`filter-branch`、`clean -fdx`、`-no-verify` | 改写已发布历史，牵连他人 |
| H8 | `shutdown` / `halt` / `poweroff` / `systemctl stop` | 会连带杀掉正在运行的 wchat 自己 |
| H9 | `pkill` / `killall` / `kill -9 -1` / 对 `$$`、`ppid` 动手 | 同上（进程炸弹） |
| H10 | `curl --upload-file` / `-d @文件` 上送 `.env`、`*.key` | 外发，不可回滚 |
| H11 | 重定向写入 `/etc` `/usr` `/bin` `/System` 等 | 持久改变系统，不在 checkpoint 覆盖范围 |
| （H12 层） | auto 下重定向/写动词的目标越界 | **规则号沿用 P1/P2/P3**，不复造第二套编号 |

### 文件路径 `hard_deny_path` → `P1..P3`

| 号 | 判据 |
|---|---|
| P1 | 读写凭据：`.ssh` `.gnupg` `.aws` `Keychains` 及 `id_rsa`/`.pem`/`.key` 等敏感名 |
| P2 | 写系统路径：`/etc /usr /bin /sbin /Library /System /private /opt /var /boot /dev`（工作目录内的写**排在它前面**） |
| P3 | auto 下写工作目录之外且不在 `--allow-write` 名单（`/tmp`、`$TMPDIR` 豁免） |

误杀代价 = 一次确认；漏放代价 = 不可逆事故。**所以设计上偏严**。
`cat /etc/hosts` 这类纯读命令不算硬墙（读系统文件交审批器）。

---

## 五、配置项与默认值（全部可用环境变量覆盖，不必改代码）

| 变量 | 默认 | 含义 |
|---|---|---|
| `CMD_TIMEOUT` | **120** | 单条 shell 命令墙钟上限（秒），`0` 关闭。auto 的兜底，但**人工模式同样生效**（见 6.3） |
| `MAX_TOOL_ROUNDS` | 25 | 单轮用户输入内工具调用上限（原被注释掉，实为无限） |
| `AUTO_MAX_APPROVALS` | 200 | 每会话自动放行配额，超了退回人工（防失控循环 + 防烧钱） |
| `AUTO_DENY_STREAK` | 3 | 连续被拒 N 次 → 本轮降级人工（模型撞墙空转的信号） |
| `AUTH_MODEL` | `$pro_model` | 审批器/终审器模型。**刻意与执行模型解耦** |
| `AUTH_CTX_KEEP` | 6 | 送审链保留 system + 最近 N 组（防变贵变慢 + 防历史裁定锚定） |
| `AUTO_SNAPSHOT_MAX_KB` | 204800 | 非 git 仓库快照体积上限，超了**降级 read 档**而不是不开 auto |
| `WCHAT_AUTO_ASSUME_YES` | 关 | CI/无 tty 下代替 `AUTO` 签字（副作用：两次 Ctrl+C 急停失效） |
| `AUTO_SHELL_PATHS` | 1 | 置 0 可关掉 H12 写目标扫描（误杀排查用） |

其他窗口参数：`INT_FORCE_TIMEOUT=2s`（Ctrl+C 杀组后硬收尾）、
`BG_SILENT_TIMEOUT=3s`（命令主体已退但子进程攥着 FIFO）、`PENDING_MAX=256KB`。

**落盘位置**
- `~/.ct/auto/audit.log` —— 单行 JSON：`ts/tier/ev/target/rule/verdict/reason/sid`。
  **只记决策，不记命令输出**（否则敏感数据绕过"不落盘"承诺二次落盘）。
- `~/.ct/auto/snapshot/<会话id>/` —— 非 git 仓库的整树快照。
- `~/.ct/auto/backup/<会话id>/` —— 覆盖写前的单文件备份。
- 快照与备份**只留最近 3 个会话**（防涨盘）。快照是整树拷贝，目录被外部改名会
  让 rsync 重建，实测 12 份快照 6.7MB → 清理后 20KB。
- `~/.ct/config`（0600）—— `API_KEY=...` 或 `WCHAT_API_KEY`。权限不合规即拒绝加载。
  **绝不把 key 写回 `wchat.sh`。**

---

## 六、命令挂死与超时（pty 那一层，务必读）

### 6.1 工具命令不是普通子进程
`run_with_window`（1068）把命令套进**伪终端**执行：macOS `script -q /dev/null`，
Linux `script -qec`，都没有则 python `pty.fork` 兜底。目的是让子命令以为自己
面对终端，恢复行缓冲，从而能**实时**渲染输出。

### 6.2 副作用：`git log` 这类命令会挂在分页器上
子命令看见 tty → git 自动开 `less` → **命令永不退出**。此时：

- **只有人能解开**：窗口循环每轮代读真实 tty 的按键并转发进子命令 stdin
  （1234–1242 行）。用户看着窗口按 `q` 就出来了。
- **模型解不开**：工具列表里没有"发按键"的能力，它要等命令结束才拿回控制权。
- **兜底**：`CMD_TIMEOUT` 到点 → `kill -TERM -- -pgid` + `kill_tree` → 走与
  Ctrl+C 同一条收尾路径（最多再等 2s，EOF 不来也收尾），并把事实写进工具输出
  （1392 行）告知模型"改用非交互参数 / 改 `run_in_background=true` / 拆短命令 /
  让用户设 `CMD_TIMEOUT=600`"。

所以：**事前模型不知道会挂，事后知道**；代价是最坏白等 120 秒；一整轮最多
25 次工具调用，理论上模型反复撞同一个坑能耗掉数十分钟——`AUTO_DENY_STREAK`
管不到它（超时不是"被拒"），只有轮次上限封顶。

### 6.3 `-st` 会话模式不受影响
现在这个会话里工具命令由 claude-code 自己的 Bash 执行，stdout 是**管道**不是
tty → git 自动不分页。会踩 6.2 的是 `-t` / auto 这条自建工具链。

### 6.4 已知缺口（**未实现**，别以为有）
- 没有交互式命令黑名单（`vim`/`less`/`git log` 事前不拦）。
- 没有注入 `GIT_PAGER=cat` / `PAGER=cat` / `GIT_EDITOR=true`。
- 没有"疑似在等输入"探测（输出末尾像 `?` / `[y/N]` / `:` 时提醒用户），
  用户不在屏幕前就只能等 `CMD_TIMEOUT` 到点。
- `CMD_TIMEOUT=120` 对**人工模式也生效**——`npm run build` 超两分钟会被杀。
  想只约束 auto，需要把它改成按 `auto_mode` 取值。

要补的话，性价比从高到低：注入关分页器 env（一行）> 等输入探测提示 >
交互命令事前拦截 > 超时改按档位取值。

---

## 七、被推翻的写法（防止回退，改前先读）

1. **`read` 档不得接管 shell 的 N**。接管条件必须是 `auto_takes_shell()` 单点判定；
   函数体末尾**不能再跟 `return 0`**（会让它恒真）。
2. **硬墙必须在审批器之前**，且命中后**不得再发审批请求**（白烧一次 RTT 与钱）。
   端到端专门断言 `auth` 请求次数。
3. **`granted_files` 必须带权限位**（`r|path` / `rw|path`，`rw ⊃ r`，目录含子树）。
   早期不带位，导致 `@` 这个"只读手势"实际同时放开写入。
4. **高危确认只认 `yes`**，且与审批器判 N 的人工确认**分两次问**——共用一次
   `y/n` 会把"确认执行这条命令"误当成"永久信任该路径"。判定顺序必须是
   "先判严格模式再判 y"（早期写反，且 `y` 仍被放行）。
5. **`content` 是全局变量**，每次工具分发前必须 `content=""` 清零，否则拒绝分支
   会把**上一条工具的结果**当成本次结果回传模型。
6. **正则一律先装进变量**再 `[[ x =~ $re ]]`：右操作数只免词分割与 glob，
   **不免命令替换**，含 `` ` `` `$(` 的字面量等于开任意代码执行口子。
7. **POSIX ERE 不支持 `\b`**，不报错、当普通字符 → 规则静默失效。词边界用
   `(^|[^[:alnum:]_./-])` 之类显式上下文；左侧类**必须允许 `/`**，否则
   `/usr/bin/sudo ls` 绝对路径绕过全部硬墙。
8. **空正则恒匹配**：`[[ x =~ $empty ]]` 为真。测试若漏抽顶层 `hd_re_*` 赋值，
   结果不是失败而是**全绿**——已有"正则非空"断言兜住。
9. **路径比对两侧必须同源归一化**。踩过的坑：`/` 祖先拼出 `//x/y`；尾斜杠未剥
   导致 `$TMPDIR/T` 拼错；macOS `/tmp`→`/private/tmp`、`/home`→
   `/System/Volumes/Data/home` 的 firmlink；测试夹具因此改用**不存在的顶层路径**。
10. **"工作目录内的写"必须排在系统路径判据之前**（`WORK_ROOT` 本身常在
    `/private/var` 下，先判系统会把"在自己项目里写文件"误拒）。
11. **auto/skill 注入段存盘前必须剥离**（`inject_auto` 幂等、按 `AUTO_MARK` split），
    否则普通模式加载历史后 system 里还写着"当前处于 auto 模式"。
12. **审批器判 N 的理由必须回传**，且模型必须填 `reason`（不填直接 DENY）。
    早期只回"用户拒绝"，模型换等价写法再撞同一堵墙。
13. **`--allow` 取参数别写 `${!n}`**（应为 `${@:_n:1}`）：写错时 bash 报
    `invalid indirect expansion` 并把路径丢掉，`--allow` 静默失效。
14. **不要用 `mv` 覆盖 `wchat.sh`**：新建文件默认 644 会丢掉可执行位，
    `wchat` 软链直接跑不起来（本次就踩过）。
15. **`save_history` 的 `name` 是全局变量**（无 `set -u`）。`-fs` 现在按
    `last_saved_history` 判是否真落盘，不能写 `save_history && echo 已保存`：
    对话为空时它静默不写仍返回 0。

---

## 八、测试

```bash
cd ~/.cache/wchat-tests
bash auto_test.sh          # 全部(~50s,200 项断言)
bash auto_test.sh unit     # 仅单元(4.4s,零网络)
bash auto_test.sh e2e      # 仅端到端(桩网关 + pty)
```

- `auto_test.sh` —— 唯一入口。**必须** `</dev/null` 跑（内部已给 `path_access`
  显式喂 `/dev/null`，否则真 tty 下会阻塞等回车）。
- `stub_gateway.py` —— OpenAI 兼容桩：把最后一条 user/tool 内容与
  `STUB_AUTH_DENY_IF` / `STUB_FINAL_DENY_IF` 标记比对决定 Y/N，
  `STUB_LOG` 落请求流水。**零真实网络**。
- 单元覆盖：硬墙命中/不命中（40+33 条表驱动）、路径裁决矩阵、档位接管语义、
  授权位、人工确认语义、终审 fail-closed、送审链裁剪、审计可 jq 解析、
  注入幂等、checkpoint（git/rsync/超体积）、`parse_options`、`-fs`、静态接线。
- 端到端覆盖：sandbox 档一次跑完"放行/硬墙/终审接管/终审拒绝"四种分流并核对
  **请求次数**（硬墙不消耗审批请求）、`read` 档归人、轮次上限、非 auto 基线不变、
  审计落盘且不记命令输出。
- 回归：`vision_layer_test.sh`（33 项）。
- 抽函数沙箱注意：主循环在脚本顶层 `while`，整文件 `source` 会起 TUI；只能按
  函数名 `awk` 抽取，新增函数要同步加进 `for fn in ...` 列表，否则抽不到=测了
  个寂寞（已有数量断言兜底）。

---

## 九、加一条规则要动哪几处（缺一个就不自洽）

1. `hard_deny_cmd` 加 `hd_re_xxx` 正则 + `if [[ "$c" =~ $hd_re_xxx ]]` 分支，
   `hd_msg` 必须写"替代方案 + 哪一档能放开"（拒绝即教学，不然模型反复试）。
2. `auto_enable` 的能力清单（`printf` 那几行）加一行，让用户签字前看得见。
3. `AUTO_SYS`（注入给模型的规则）若改变了模型该守的约定，同步改文案。
4. `auto_test.sh` 的 `HD` / `NP` 表加用例（**正反都要**：新规则会不会误杀高频写法）。
5. `bugList` 记一条（格式 `[v]序号. 现象<优先级>【done】根因+修复`）。

---

## 十、待办 / 已知边界

- [ ] 6.4 的四项（关分页器 env、等输入探测、交互命令事前拦、超时按档位取值）。
- [ ] 无 OS 级沙箱。bash 里做不到 seccomp/pledge，硬墙是正则+路径前缀。
      想再上一档只有 macOS `sandbox-exec`（本机已有）或 Linux `bwrap`，
      把"判错"变成"物理做不到"。建议等 auto 真出事再上。
- [ ] 审批器看不到上下文、终审器只看得到 `reason`，两者都可能被"理由写得很像样"
      骗过。兜底是 checkpoint + 审计 + 配额，不是模型自觉。
- [ ] 越界写永久硬墙、auto 允许长会话（每 50 次放行重新显示累计数）——这两条
      是**我替你拍的默认**，想改在 `hard_deny_path` P3 / `auto_note_allow`。
- [ ] `-fs` 无"另存为"语义（只按标题存），要另存得改 `handle_cmd_line`。
- [ ] 安全待办：`API_KEY` 旧值已进 git 历史与 `~/.ct/history` → **去服务商轮换**；
      `~/.cache/wchat-tests/vision_probe.sh:68` 另有一个明文 mimo key（644）待清。