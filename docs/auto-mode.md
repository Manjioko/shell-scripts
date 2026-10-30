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
| `is_granted` | 2298 | 外部路径授权名单命中判定（含子树；空名单提前返回，见第八节 bash 3.2 坑） |
| `shell_note_grants` | 2332 | **人工 y 之后把外部路径落进名单**（缺口 B；`_sng_one` 内嵌其中，跟着一起被抽函数） |
| `hd_mask_readonly` | 2864 | 只读动词引号参数脱敏（H2/H3/H4 的二次确认用；配 `hd_is_readonly_verb`） |
| `hd_is_msg_verb` / `hd_mask_msg` | 2932 / 2940 | **H6 专用**脱敏：只脱 `echo`/`printf` 的引号参数（通用白名单里的 cat/grep 等其引号参数就是文件，脱了等于给凭据墙开引号绕过口） |
| `hard_deny_cmd` | 2999 | **硬墙 H1–H11** + H12 写目标扫描（`hd_recheck`、`hd_recheck_msg` 内嵌其中） |
| `hard_deny_path` | 3154 | **路径红线 P1–P3**（P1 与 H6 成对同步；P2 内含临时目录豁免） |
| `auto_final_judge` | 3256 | 终审器（带 reason 复审 N） |
| `auto_note_deny` / `auto_note_allow` | 3303 / 3309 | 连拒降级 / 放行配额计数 |
| `auto_takes_shell` | 3322 | **单点判定**：auto 是否接管 shell 的 N |
| `path_access` | 3373 | 文件工具越界入口（6 个调用点收敛到这里；次序 ①墙 ②名单 ③读 ④显式名单 **④.5 临时目录** ⑤拒 ⑥人批） |
| `auto_allow_hit` / `auto_tmp_write_ok` | 2511 / 2534 | 显式 `--allow*` 名单命中 / 临时目录写三态判定（0 放行 1 非临时 2 末段是符号链接必拒） |
| `inject_auto` | 2738 | 把规则注入 `messages[0]`（压缩安全） |
| `auto_checkpoint` | 2754 | git HEAD 或 rsync 快照 |
| `auto_enable` | 2808 | 能力清单 + 签字 + 回滚点（tty 必需） |
| `auto_report_exit` | 2880 | 退出小结与回滚指令 |

> 行号只是定位提示，改动后会漂；以 `grep -n '^func() {' wchat.sh` 为准。

其他入口：`parse_options`（863，`-auto` / `--allow`）、`handle_cmd_line`
（`-auto=` / `-noauto` / `-fs` / `-md`）、分发循环在 `tool_run_and_parse` 里
（轮次上限、`content=""` 清零、审计）。

---

## 四、硬墙清单（改它之前先看第七节）

### shell 命令 `hard_deny_cmd` → `H1..H14`

> **判据吃的是原样多行的命令**。调用点曾把 `${command//$'\n'/ }`（一行版，只给展示/审计/
> 终审用）喂进 `hard_deny_cmd`，于是所有"按行/按段"的判据一起退化：`hd_install_tail` 的
> "动词必须段首"失效（只读 python heredoc 被判装包，H14 审计命中 6/6 全是误拦）、
> `hd_noncode_payload` 没有正文行可剥（表里 T22 那条缓解在产品路径上从未生效）、
> `hd_h11_scan` 的"第 N 行"坐标恒为第 1 行。行边界的正规处理归 `hd_fold_lines`
> （`\`+换行折空格、裸换行前补 `;`、含 `<<` 不折），不要在它上游再做一次更粗的折叠。
> 匹配域：H2/H3/H4 是**整串正则**，命中后还要过一道 `hd_recheck`（把「只读动词的带引号
> 参数」脱敏后再匹一次，仍命中才算数）——否则 `grep -n 'rm ' ~/.ct/auto/audit.log` 这种
> 只读命令会被 H3 误杀。误杀的代价不是白烧一次调用，而是**教模型换写法绕过扫描器**，
> 反噬安全性，所以修的是匹配域而不是放宽字符类。
> 依赖关系必须 fail-closed：`hd_recheck` 定义在 `hard_deny_cmd` **函数体内部**（跟着它
> 一起被按名抽取），且缺 `hd_mask_readonly` 时直接 `return 0`（照拒）；H12 在缺
> `hd_write_targets` 时对含 `>` 的命令一律拦（编号沿用 P3，不造第二套号）。

| 号 | 拦什么 | 为什么不能靠审批器 |
|---|---|---|
| H1 | `sudo` / `doas` / `su` | 提权后沙箱形同虚设 |
| H2 | `curl … \| sh`、`bash <(curl …)`、`eval "$(wget …)"`；**落盘再执行**：`curl -o <路径> <url> && bash <绝对路径>` | 内容不可预知，事后无法回滚。落盘分支原先写成 `-o <值>` 后**紧跟**执行段，而真实命令中间隔着 URL → 近乎永不命中；以前被"落盘那半会被 P2 拒"掩盖，**临时目录现在豁免了，组合不再被拆开**，所以这行是真修好。残留缺口（刻意）：不跨 `;` 连接（`curl -o /tmp/x.sh url; sh /tmp/x.sh` 仍漏）——放开 `;` 会让"同串里两件不相干的事"大面积误伤 |
| H3 | `rm -rf ~` / `$HOME` / `/*` / `/usr` / `--no-preserve-root`；家目录父级 `/Users` 与 `/home` **同时列** | 一条命令毁所有。只写 `/Users` 时 Linux 上 `rm -rf /home`（删掉全部用户家目录）连 H3 都不命中，且 `/home` 刻意不在 P2 名单 → 掉到可被 `--allow-write` 放开的 P3。代价：`/home` 与 `/Users` 同为无边界前缀，`rm -rf /homebrew/x` 一并判 H3（锁在 T13 标 `[设计如此]`） |
| H4 | `dd of=/dev/*`、`mkfs`、分区 | 文件系统级 |
| H5 | 写/删 `~/.bashrc` `.zshrc` `.gitconfig` `.ssh` `.aws` `.gnupg` `~/Library/LaunchAgents`、`~/.config/<app>/config` | 持久改变环境，不可回滚。家目录的**绝对形态**统一由 `hd_home` 锚点提供：`~`／`$HOME`／`/Users/<name>`／`/home/<name>` 四写并集，H5/H6 共用它，所以"漏了某个平台"只可能发生在这一处（改完必须看 T13 的 5 条静态守卫是否还绿） |
| H6 | 访问凭据：私钥/凭据**目录**（`~/.ssh` `~/.aws` `~/.gnupg` `~/.docker` `~/.kube` `~/.subversion` `~/.config/{gh,gcloud,azure}` `~/.ct/{config,history}`）＋凭据**文件名（只判信任边界外，见第九节第 6.1 条）**：`.npmrc` `.netrc`/`_netrc` `.pypirc` `.htpasswd` `.pgpass` `spnego-machine` `.git-credentials` `.pem/.key/.p12/.pfx` `id_*` ＋ `security dump-keychain`；**他人家目录同样判**（`/home/bob/.ssh/…`——Linux 上锚点原先只有 `/Users`，文本层完全不命中，而路径侧 P1 靠 `$HOME` **只保护自己的家目录**，别人的密钥因此双侧漏判） | 泄露即永久；内容会进上下文并随请求发给服务端。**与 P1 同口径**（改一侧必须同步另一侧，见第九节第 6 条）。**`.env` 不在本条**：工作目录内的 `.env` 属开发常规，只由外发规则 H10 兜 |
| H7 | `git push --force`、`push :分支`、`filter-branch`、`clean -fdx`、`-no-verify` | 改写已发布历史，牵连他人 |
| H8 | `shutdown` / `halt` / `poweroff` / `systemctl stop` | 会连带杀掉正在运行的 wchat 自己 |
| H9 | `pkill` / `killall` / `kill -9 -1` / 对 `$$`、`ppid` 动手 | 同上（进程炸弹） |
| H10 | `curl --upload-file` / `-d @文件` 上送 `.env`、`*.key` | 外发，不可回滚 |
| H11 | 重定向/`tee` 写入系统路径，名单是**两平台并集**：`/etc` `/usr` `/bin` `/sbin` `/System` `/Library` `/private` `/Applications` `/var/root` + `/proc` `/sys` `/run` `/boot` `/lib` `/lib64` `/srv` `/media` `/mnt` `/root` | 持久改变系统，不在 checkpoint 覆盖范围。`/root`（Linux 的 root 家）与 macOS 侧已有的 `/var/root` **成对**：漏掉它 `echo k > /root/.ssh/authorized_keys` 就只剩一条可放开的 P3。刻意**不含 `home`/`Users`**——用户目录按"两侧同为 P3"处理，写进这里会造成单侧加严（shell 撞 H11、文件工具只落 P3） |
| （H12 层） | auto 下重定向/写动词的目标越界 | **规则号沿用 P1/P2/P3**，不复造第二套编号 |
| H13 | 写目标在审批层**不可解析**：`defaults write/delete`、`plutil -replace/-insert/-convert`、`osascript`、`launchctl load/unload`、`sqlite3` 带 DML、`git config --global/--system`、`security add-*/delete-*`、`networksetup`、`tccutil reset`、`htpasswd`、`xattr -w`、`codesign --sign`、`installer -pkg` | 命令里根本没有可判的路径（`defaults write com.x k v` 改的是 `~/Library/Preferences` 里的键）：H12 的越界判据提不出目标、快照/备份也覆盖不到。按"有写语义但目标不可知"处理，与 `hd_has_write` 同一思路：不确定归到拦的那一侧。刻意不做逐动词路径白名单——那正是"每个特例都要人记住"的债 |
| H14 | **安装面**：段首是包管理器（brew/pip/npm/pnpm/yarn/gem/cargo/go/uv/apt/dnf/yum/apk/composer，或解释器的 `setup.py install`/`-m pip install`）且出现 `install`/`add`/`tap`（裸 `i` 只认紧跟管理器的第一个实参位）；**换源形状有授权也拒**（`--registry`/`--index-url`/`-i http…`/`PIP_INDEX_URL=`/`brew tap <URL>`） | 装完落在 `WORK_ROOT` 之外（H12 提不到目标）、以你的 uid 跑第三方 postinstall、且不在快照回滚范围内。唯一出口是人**启动时** `--allow-install 包名…` 点名（配额 `AUTO_MAX_INSTALLS`）。三条口径钉住误杀面（本轮审计 H14 命中 6 次全是误拦后定的）：① **必须段首**，禁止"提不出名字就退回全文正则"的兜底（那等于把段首作废，`git commit -m "brew install …"` 会当场变红）；② **heredoc 定界符之后是载荷，不算本段参数**（但正文各自成段照常按代码判，`bash - <<SH` 里真写 `pip install` 照拦）；③ 名字进话术前过字符集与长度，提错时不许把用户正文印进审批卡与只追加的审计 |

### 文件路径 `hard_deny_path` → `P1..P3`

| 号 | 判据 |
|---|---|
| P1 | 读写凭据：目录 `$HOME/.ssh` `.aws` `.gnupg` `.docker` `.kube` `.subversion` `.config/{gh,gcloud,azure}` `.azure` `.ct/{config,history}`，**以及他人家目录下的同一份名单**（`/home/bob/.ssh`、`/Users/bob/.aws`——由 `hd_cred_subdirs` + `hd_foreign_cred_path` 提供，只认"家目录直属层级"、不碰自己的 `$HOME`）；**文件名（只判信任边界外：归一后落在 `$WORK_ROOT` 内的项目级 `.npmrc` 等按普通配置放行，见第九节第 6.1 条；`WORK_ROOT==$HOME` 或 `/` 时该豁免失效）** `.npmrc` `.netrc`/`_netrc` `.pypirc` `.htpasswd` `.pgpass` `.git-credentials` `spnego-machine` `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`；扩展名 `.pem` `.key` `.p12` `.pfx` | 与 H6 成对同步。旧版只按 `$HOME/` 前缀列，且 `[[ "$np" == id_rsa ]]` 拿整串比绝对路径是**死分支**（嵌在 `.ssh` 外的私钥全靠这条兜底）；`.npmrc`/`.netrc`/`.pypirc` 当时**完全不在名单里**，于是 shell 里 `cat ~/.npmrc` 恒拒、`read_file ~/.npmrc` 却直接放行 —— 没有真沙箱时，一侧放行就等于没有保护 |
| （刻意代价） | 凭据按**文件名**命中，不区分是否真读内容 | `ls -l ~/.npmrc`（只看元数据）、甚至命令文本里出现 `~/.npmrc` 都会撞 H6——**连搜自己的规则都会被拦**（改这条时请用不带点或拆开拼的写法，如 `grep npmrc`、`grep 'git.credits'`）。按"宁拒不误放"保留，锁在表里标 `[设计如此]` |
| （刻意豁免） | `.env` 在工作目录内可读；目录外的 `id_rsa.pub` 可读 | 前者是开发常规、真正风险在外发（H10）；后者公钥不是机密，拦它只会逼模型绕路。但 `~/.ssh/` **目录墙优先**，其内的 `.pub` 两侧都拦 |
| P2 | 写系统路径（**两平台并集**）：macOS 侧 `/System /Library /private /Applications /Volumes /var/root`、共用 `/etc /usr /bin /sbin /opt /dev`、Linux 侧 `/proc /sys /run /boot /lib /lib64 /srv /media /mnt /var /root`（`/root` 与 macOS 侧已有的 `/var/root` 成对，漏它等于让"写 root 的 authorized_keys"只剩一条可放开的 P3）（工作目录内的写**排在它前面**）。刻意**不列用户目录**：`/Users` 与 Linux 的 `/home` 同构，都不进 P2，越界写归 P3（否则同一动作两平台红线等级不同）。漏列的后果不是漏放而是**降级**——`写 /proc/sys` 会掉到 P3，而 P3 在 full 档 + `--allow-write` 下可放开，P2 永不放开 |
| P3 | auto 下写工作目录之外且不在 `--allow-write` 名单 |
| 临时目录写 | `/private/tmp`、`/var/tmp`、`/dev/shm`(仅 Linux)、`$TMPDIR`（物理前缀，同一份 `auto_tmp_prefixes` + 字面量兜底） | P2 里先豁免（编译器/heredoc 大量写它，拦下来只会卡死任务）；文件工具侧由 `auto_tmp_write_ok` 给同口径放行（以前 shell 能写、`write_file` 要人点名 = 口径分裂，只会把人逼去 heredoc）。**工具侧多一道 shell 做不到的检查：末段是符号链接一律拒** —— `norm_path` 只归一已存在的**父目录**、不解析末段链接，而落盘是 `> "$resolved"` 会跟随链接，判定看到 `/tmp/build.log` 而字节可以落在任何地方（1777 的 `/tmp` 里预放 `build.log -> ~/.npmrc` 即绕过 P1/P2）。这条防护与平台无关，新加的 `/dev/shm` 豁免同样带着它（同为 1777）。写前仍落 `auto_backup`，审计标签 `auto_allow_tmp`（与 `auto_allow_rw` 区分、计入放行配额），**不写进授权名单**：缓存会让第二次写从第 1 步直接返回、跳过链接复检 |
| TMPDIR 接管 | 启动时若 `$TMPDIR` 缺失、不可用、或归一后就是 `/tmp`／`/var/tmp`，换成 `$HOME/.ct/tmp`（`chmod 700`）并 `export` | 自家有十余处 `mktemp "${TMPDIR:-/tmp}/…"`。macOS 上 `$TMPDIR` 天然是 0700 私有目录，**不插手**；Linux 上（容器/CI/非登录 ssh）`$TMPDIR` 经常根本没设 → 全部落到 1777 的 `/tmp`，而同机任何用户都能列举它、能对 1777 目录抢注文件名。落在这里的东西包括审批 prompt 全文、审计切片、会话正文、请求体 dump——含令牌的内容等于对本机广播，而**这是豁免链主动放行的一条外泄路径**（临时目录在 P2 里先豁免，路径墙不会拦），所以不是理论风险。只在缺失/公共时接管，改的是 `$TMPDIR` 一个变量而不是那十余个调用点，子进程（curl/npm/gradle 都尊重 `TMPDIR`）同步受益。**顺序硬约束**：接管必须判在 `auto_tmp_prefixes` 构建**之前**——晚一步就会出现"`$TMPDIR` 已是 `~/.ct/tmp`、豁免集合里还是 `/tmp`"，于是自家每次 `mktemp` 都撞 P2（P2 连 `--allow-write` 都放不开），等于自杀；这条顺序由 T13 的行号守卫钉住 |

误杀代价按通道不对称,不能一概说"偏严即可":

- **审批器**(Pass 1 / 终审)误杀 = 模型多换一次路或多一次人工确认,可接受,偏严合理。
- **H12 硬墙**误杀 = 模型拿到一句它自己无法纠正的代码级红线,换写法与重试都无效,
  只会连续烧轮次直到撞上限(实测:一条 `grep … | wc -m | sed 's/^/  /'` 的纯只读
  调试管道被判 P2「写入系统路径」,命中目标是被切词器炸出来的孤立 `/`,同一形状
  复现 3 次并直接导致模型停摆)。

所以 H12 的判据必须**只认确有写语义的目标**(重定向目标 / `-C`·`-O` 类选项值 /
按动词分类的操作数),绝不能"命令含 `>` 就全量扫像路径的东西"。收窄时每条被放行的
形态都要留反例:原 `test_h12_write_targets.sh` 的三张表(NP 反表 / DENY 正表 / 提取器
形态表)就是这个用途 —— **该文件从未进 git,现已从工作区删除**(PASS=15 的结论当时成立,
但脚本没了)。下次再动 H12 之前先把它重建回来,别裸改。

`cat /etc/hosts` 这类纯读命令不算硬墙（读系统文件交审批器）。

---

## 五、配置项与默认值（全部可用环境变量覆盖，不必改代码）

| 变量 | 默认 | 含义 |
|---|---|---|
| `CMD_TIMEOUT` | **120** | 单条 shell 命令墙钟上限（秒），`0` 关闭。auto 的兜底，但**人工模式同样生效**（见 6.3）。它是**默认值**：模型可以在调用里传 `timeout`（秒）自己放宽，适用于构建/全套测试/装依赖这类已知会慢的命令 |
| `CMD_TIMEOUT_MAX` | **3600** | 模型传的 `timeout` 的**封顶值**。为什么要封顶：auto 无人值守时没人掐表，一个手滑的 `timeout: 999999` 就能让会话挂一整天。超封顶会按封顶执行并**在屏幕上说明**（模型看得到，才知道该改） |
| `MAX_TOOL_ROUNDS` | 250 | 单轮用户输入内工具调用上限（原为 25：实测一轮正经任务读+改+测+迭代就常撞顶，逼出草稿式收尾；失控防护交给 `AUTO_DENY_STREAK`/`AUTO_MAX_APPROVALS`） |
| `AUTO_MAX_APPROVALS` | 200 | 每会话自动放行配额，超了退回人工（防失控循环 + 防烧钱） |
| `AUTO_DENY_STREAK` | 3 | 连续被拒 N 次 → 本轮降级人工（模型撞墙空转的信号） |
| `AUTH_MODEL` | `$pro_model` | 审批器/终审器模型。**刻意与执行模型解耦** |
| `AUTH_CTX_KEEP` | 6 | 送审链保留 system + 最近 N 组（防变贵变慢 + 防历史裁定锚定） |
| `AUTO_SNAPSHOT_MAX_KB` | 204800 | 非 git 仓库快照体积上限，超了**降级 read 档**而不是不开 auto |
| `WCHAT_AUTO_ASSUME_YES` | 关 | CI/无 tty 下代替 `AUTO` 签字（副作用：两次 Ctrl+C 急停失效） |
| `AUTO_SHELL_PATHS` | 1 | 置 0 会**同时**关掉 H12 的写目标扫描与 `/dev/*` 特征设备豁免（不只是"少几条豁免路径"）：开 0 后 `make build > /dev/null`、`cmd 2>/dev/null` 这类常态写法会直接撞 P2，只用于对照排查，不要在日常 auto 下使用 |

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
`run_with_window`（1192）把命令套进**伪终端**执行：macOS `script -q /dev/null`，
Linux `script -qec`，都没有则 python `pty.fork` 兜底。目的是让子命令以为自己
面对终端，恢复行缓冲，从而能**实时**渲染输出。

### 6.2 副作用：`git log` 这类命令会挂在分页器上（已修，见 6.4）
子命令看见 tty → git 自动开 `less` → **命令永不退出**。此时：

- **模型解不开**：工具列表里没有"发按键"的能力，它要等命令结束才拿回控制权。
- **人未必解得开（待实测）**：窗口循环每轮代读真实 tty 的按键并转发进子命令 stdin
  （1378 行），理论上按 `q` 能出来。但按同构结构（pty + `<>` 常开输入 FIFO）复刻的
  探针里，往 in_fd 写 `q` 后子命令没退出——探针没有真 tty 可代读，不能定论，
  **需要在真实会话里验一次**。
- **兜底**：`CMD_TIMEOUT` 到点 → `kill -TERM -- -pgid` + `kill_tree` → 走与
  Ctrl+C 同一条收尾路径（最多再等 2s，EOF 不来也收尾），并把事实写进工具输出
  （1529 行）。

实测坐实（bash 5.3 / `script` bsd 家族 / 仓库 111 条提交）：不注入关分页 env 时
`git log --oneline -n 500` **只吐首屏 39 行**（= rows 40 − 1）后一直挂；注入后 111
行全量秒回。所以修复前"模型只能等超时"成立，不是推测。

代价：一整轮最多 25 次工具调用，理论上模型反复撞同一个坑能耗掉数十分钟——
`AUTO_DENY_STREAK` 管不到它（超时不是"被拒"），只有轮次上限封顶。修复前的超时提示
只说"改用非交互参数"，对分页器**不指路**（没提 `--no-pager`/`-n`），模型大概率原样
重试再烧一次 120s；该方向现已补进 1529。本节保留原诊断作为历史记录。

### 6.3 `-st` 会话模式不受影响
现在这个会话里工具命令由 claude-code 自己的 Bash 执行，stdout 是**管道**不是
tty → git 自动不分页。会踩 6.2 的是 `-t` / auto 这条自建工具链。

### 6.4 已实现：工具子进程内一律关分页器与交互编辑器
`run_with_window` 组装命令时前置一串 export（1288 定义 `pager_off`，1289 注入 pty
三分支共用的 `wrapped_cmd`，1335 覆盖无 pty 兜底分支）：

    GIT_PAGER=cat PAGER=cat GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true GIT_TERMINAL_PROMPT=0

- `GIT_PAGER` 优先级高于用户 `core.pager`（配了 delta 也一并压掉，代价是分页输出
  无高亮）；`PAGER` 兜住 man 等；`GIT_EDITOR`/`GIT_SEQUENCE_EDITOR=true` 堵掉同族
  挂死（`git rebase -i`、漏 `-m` 的 commit、merge 消息编辑器）。
- `GIT_TERMINAL_PROMPT=0`：需要账号密码的 https 操作**立刻失败**，而不是弹个提示
  挂满 120s。取舍已知——代跑 push/fetch 需要凭据时会硬失败，判定为失败优于挂死。
- 作用域只有工具子进程：`run_in_background` 不走 `run_with_window`（服务器进程会
  继承 FIFO 写端，4343 行注释），长驻服务不会继承这串 env。
- 想临时恢复分页：命令里自带 `GIT_PAGER=less ...` 即可覆盖（子命令内显式赋值优先）。
- 回归：`test_pty_pager.sh` —— 静态断言两个注入点没被将来的重排静默丢掉，行为实测
  「不注入只拿首屏 / 注入后全量」，自建 61 条提交的临时仓库，不依赖用户仓库状态。

### 6.5 已实现：终端对子进程查询的应答不得混进按键流
现象：在 Ghostty 里跑完 `gh pr view` / `vim` / `mdcat` 之类的命令后，窗口里或退出后的
shell 提示符上冒出一串 `^[]11;rgb:2828/2c2c/3434^[`。根因不在我们打印了什么，而在**应答
去哪了**：子进程向终端发查询（`\e]11;?\e\` 问背景色、`\e[6n` 问光标、`\e[?u` 问键盘
增强），终端**必定回一条应答**，而应答写的是控制 tty —— 也就是 `run_with_window` 用
`3<&0` 喂给子命令 stdin 的那个 fd。旧中继把 tty 上读到的字节一律当"用户按键"转发，于是
应答被当成按键打回给子命令（echo 关着时它混进答案，用户按 y 像没反应），窗口退出后剩下的
尾巴又被 shell 的 readline 回显成上面那串。

口径（`tty_strip_responses` 1398，接线在 1662 init / 1685 逐键过滤 / 1696 TTL / 收尾 drain）：

- **只丢确认是应答的形态**：OSC(`]`)/DCS(`P X ^ _`) 整段到 BEL 或 ST；`R` 带参数（DSR 光标
  报告）；`c` 带 `?`/`>` 前缀（DA1/DA2）；`u` 带 `?`（kitty 标志应答）；`y` 带 `$`（DECRPM）；
  以及 XTVERSION / XITS 之类 DCS 应答。
- **误吞按键比漏放一条应答严重得多**，所以每条判据都写成"确认是应答才丢"，默认落回转发；
  判不动的尾部留在 `tty_hold` 等下一片（应答都是毫秒级整段到达，走不到老化），超过
  `TTY_HOLD_MAX=3`（1379）个轮询周期还没等齐就**原样转发** —— 这条兜底保证最坏情况只是
  屏幕上多一串乱码，绝不会是"用户按的键没了"。
- bracketed paste 区间（`\e[200~ … \e[201~`）**一律不过滤**：粘贴正文里的 `ESC ] …` 是数据，
  剥它就是篡改用户输入。
- 同一个过滤器也接在 `ask_read_line`（791）上：应答落在确认提示的 stdin 里时，旧写法会把它
  拼进答案，答案变成一长串转义序列、按"非 y"拒绝 —— 不泄密，但用户看到的是"我按了 y 它不理"。
- 窗口收尾必须 `tty_drain_responses`：把队列里残留的应答吃掉，否则它飘到下一个读者（人工确认
  提示、乃至退出后的 shell 提示符），现象就是"命令早就跑完了还在冒乱码"。

未实现，别以为有：**本程序自己绝不发查询**（守卫⑦定长匹配 `]11;?` / `[6n` / `[?u` 的字面量）。
因此这里没有"请求↔应答配对"这层——只剥应答、不消费请求方。将来谁要主动查背景色/光标，必须
连同配对与超时一起补，否则终端应答会合法地进入数据流，每轮多一次往返。

回归：`test_hardwall.sh` T17（30 条：应答丢弃 / 真按键保留 / 分片重组 / 粘贴保护 / TTL 老化 /
静态接线守卫），并已用四个变异体反证其有效性 —— 删掉中继过滤、把 OSC 改成透传、植入一条
查色请求、把 kitty 判据放宽成"参数含 `;` 即应答"，每种都恰好打红对应的那一条。

### 6.6 仍然没有的（**未实现**，别以为有）
- 没有交互式命令黑名单（`vim`/`less`/`git log` 事前不拦）。关分页 env 只治住"程序
  自己起分页器"这一类；`vim`、`npm create` 的交互问答、`ssh` 口令等仍只能靠超时兜底。
- 没有"疑似在等输入"探测（输出末尾像 `?` / `[y/N]` / `:` 时提醒用户）。
- 应答剥离是**按形态识别**，不是按"谁发的请求"识别。真应答与用户手敲的转义序列在字节层面
  同形，靠 TTL 老化区分时间尺度（应答 ms 级、人敲 s 级）；`TTY_HOLD_MAX` 调大能更保守，
  代价是应答出现在滞留窗口内时会先被缓存一两个周期。
- `CMD_TIMEOUT=120` 对**人工模式也生效**（302 行，不分档位）——`npm run build` 超
  两分钟会被杀。**2026-11 起它只是默认值**：模型可以传 `timeout` 自己放宽（封顶
  `CMD_TIMEOUT_MAX=3600`），被夹紧时会收到明确说明。要长期改默认值仍改这个变量。
- **硬墙族（H2/H3/H4）仍是整条命令串匹正则**，`hd_split_words` 的引号感知只修了
  H12。`hd_re_wipe_root`（2481）的中间段 `[^;&|`$]*` 不排除引号：实测只读的
  `grep -n 'rm ' ~/.ct/auto/audit.log` 被 H3 拒（引号内的 `rm` 与命令别处的 `~/`
  被中间段连上了）。硬墙宁可误杀不可漏放，但这条白烧一次工具调用；真要修得连正表
  一起回归（`rm -rf ~/`、`rm -rf /Users/x`、`sudo rm -rf /` 必须仍然拦）。

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
16. **"参数含 `;` 的 CSI u 就算应答"来判终端应答** —— 被推翻。kitty 增强键盘的**真按键**形态是
    `\e[13;27u`（事件码`;`修饰码，无 `?`），与"标志应答" `\e[?u` 只差一个私有前缀；按分号判会把
    方向键与 Ctrl 组合整批静默吞掉，而这种改法在只看"应答有没有泄漏"的用例里**全绿**。T17 的
    `kitty 带修饰按键保留` 一条专门钉它（变异体验证过：放宽判据只打红这一条）。
17. **同一行 `local` 里引用刚赋的变量**（`local s="$x" n=${#s}`）—— 被推翻。bash 处理 `local`
    词表时后面的词看不到前面的赋值，`set -u` 下当场 unbound；若这个函数还是被 harness `eval`
    进来的，报错位置指向**装载它的那一行**（本表 87 行），离真正写错的代码隔了几千行，靠报错
    位置根本找不到。必须拆成两行写。
18. **`sed`/`grep` 的字符类里放全角标点**（`[:：]`、`[是为如]`、`[」』"”。！!，,。、]`）—— 被推翻。
    它们只在 UTF-8 locale 下如人所愿；`LC_ALL=C`（用户没设 LANG、或在 Alpine/容器里）时按**字节**
    建类：要么静默匹配半个汉字、把整串切成乱码（会话标题就中招过），要么直接把 grep 打成
    `brackets ([ ]) not balanced` 报错退出。一律改成交替式 `(是|为|如)`，两种 locale 同结果。

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
- 仓库内轻量回归（不依赖网络与桩网关，`cd ~/bin && bash <文件>`）：
  - `test_hardwall.sh` —— 硬墙族 H1~H4/H6 + H12 写目标提取器 + `resolve_path` 归一 +
    `shell_note_grants` 记账 + 凭据口径成对断言 + 临时目录写/符号链接（T1~T9，当前
    PASS=136 FAIL=0；T9 共 22 条）。反表
    （只读命令不得误杀）与正表（真破坏必须拦）**成对**出现，脱敏类改动必须两张表同时绿
    才算修好；T6 专测"辅助函数缺失即照拒"，T7 专测"人工 y 之后必须收敛、且不得改变
    判定强度"，T8 专测"同一个文件走 shell 与走文件工具必须同结论"，T9 专测"临时目录写
    两侧同口径 + 末段是符号链接一律拒"（含用 `$TMPDIR` 里真建的链接做实证，以及
    "path_access 拒绝时必须写回 `content`、且不许落盘到链接目标"两条）。
    T7 里三条不变量最值得抄：**记账前后同一命令的墙判定不许变**（`path_access` 的
    次序是 ①墙 ②名单 ③按模式，墙永远在名单前面，所以记账不可能放水）；`$HOME` 本身、
    `/`、`/dev/*`、URL 不许进名单；重复放行不许堆条目。
    尾部有**断言总数守卫**（`EXPECT_TOTAL`）：`pass+fail` 不等于表内条数即报红。因为
    对旧版本跑表时会出现"一行输出都没有"的断言（被测函数不存在 → command not found →
    `ok`/`bad` 压根没被调用，既不红也不绿）——没有这道守卫，表就成了"看起来全绿"的假绿。
  - **bash 3.2 的 `set -u` 会把「展开空数组」判成 unbound variable 并当场打死整个脚本**
    （4.4 起才不报；本机 `/bin/bash` 是 3.2.57）。现象是一行输出都没有、退出码都拿不到，
    排查时极易误判成"被屏蔽了"。生产两处已修（`is_granted` 加 `${#arr[@]} -eq 0` 提前
    返回，`hard_deny_path` 的 `auto_tmp_prefixes` 改用 `${arr[@]+…}`）；写测试 harness 时
    同样中招：按名单 `eval` 抽顶层赋值要连小写 `auto_*` 一起抽（漏了 `auto_tmp_prefixes=()`
    就是这条），且空名单用例里自己展开 `"${granted_files[@]}"` 也要用 `+…` 形态。
    负对照（证明这张表有判别力，改硬墙后都要跑一次）：
    ```bash
    git show HEAD:wchat.sh > /tmp/hw.sh && bash tests/test_hardwall.sh /tmp/hw.sh   # 104/32
    ```
    前者=已含脱敏/归一化/记账/凭据那批修复的版本，32 红恰好全落在本次的临时目录断言
    （T8 16 + T9 15 + 总数守卫 1）。对 `a0bcd52^`（脱敏之前）跑则是**没有汇总行**：旧版
    `hard_deny_path` 在 `set -u` 下踩空变量，脚本在 T9 集成断言处当场猝死（前 109 条已
    产生判定）——"根本没有总数"与"总数对不上"是同一类信号，都说明被测版本缺东西。
    注意：这批改动提交后 `HEAD` 就成了正确版本（跑出 136/0），届时做负对照要换更早的 rev。
    本轮（应答剥离）的负对照实跑：对修复前的 `HEAD:wchat.sh` 跑出 25 红，且**每条都带
    "被测版本没有 tty_strip_responses"这种原因文案**——缺实现必须喊出来，不能只少几条。
  - **静态守卫一律"先植桩自证有牙，再判真实代码"**（本轮新增守卫⑦的写法）。教训来自三条
    一直绿着的假绿：
    1. `pd()` 定义写在调用之后 → 那条断言从来没执行过，`command not found` 只落在 stderr，
       而 `EXPECT_TOTAL` 是带着这条丢失校准出来的 → 表照绿。现已把未定义调用接进
       `command_not_found_handle` 直接判红（探测外部命令的用 `HW_CNF_OK=1` 放行那一次）。
    2. 守卫⑦原写法 `grep -qa -e "${_E}[6n"` 里 `[6n` 是未闭合字符类 → grep 报错退出 2 →
       `if grep` 把"判据坏了"和"确实没命中"都归进 else → 永远判"通过"。现在先用植桩样本验
       判据本身能命中，验不出来就红（这条红优先修，它意味着同类静态守卫都在空过）。
    3. `cjk_adj` 用 `[「」，]` 计数，C locale 下 grep 报错 → 计数落成空 → `(( n == 0 ))` 成立
       → 放过了产品里一处真崩溃：`"$name。"` 被 bash 当成变量名 `name。`（bash 认 ≥0x80 的字节
       为名字字符），`set -u` 下 `read_skill` 传错名字会当场猝死而不是打印"技能不存在"。
    所以跑这张表的判据是 **`FAIL=0` 且 stderr 一行都没有**：
    `bash tests/test_hardwall.sh >out 2>err; wc -l < err` 不为 0 就别当绿。
    **实跑基线（两边都已测）**：macOS `PASS=351`；Alpine 3.20（root、`:ro` 挂载）`PASS=353`。
    （347/349 → 349/351：主套件里测不出真相的两条 H5 移进本表 T3；→ 351/353：再加 T18
    两条 `bg_leak` 提示文案守卫。两批都与平台无关,所以两平台同步 +2。）
    差 2 的来源只有一处：T9 的 `/dev/shm` 真链接块门控成立时产生 2 条判定，不成立时走
    `echo "  SKIP …"`——**SKIP 不产生判定**，所以是净 `+2`。我第一版按"if 分支里数了几条"
    推成 `+3`，实跑红在"执行 N 应为 N+2"上。校准平台增量时必须**连 else 分支一起数**。
  - **主套件（`~/.cache/wchat-tests/auto_test.sh`）的抽函数名单是第二个"测了个寂寞"入口**，
    本轮一次踩到两批，都表现为"产品坏了"：
    1. `hard_deny_path`/`hard_deny_cmd` 新调的 `in_root_exempt`/`hd_foreign_cred_path`/
       `hd_write_targets` 没进名单 → sandbox.sh 里 `command not found` → 判据链走 fail-closed
       兜底 → 规则号错位（现象是"H5 没拦"，真因是名单漏抽）。
    2. 确认链 `ask_drain_input`/`ask_read_line`/`_confirm_yes`/`tty_strip_responses` 也没进名单
       → `ask_line` 永远为空 → 第 6 节三条"该放行"恒红，四条"该拒绝"恒绿。**这种不对称就是
       读不到输入的签名**：读空只会把"该放行"判错，不可能把"该拒绝"判错，反过来红法一律先查链。
    规矩：产品新增被调函数，**抽取名单与"名单即合同"那圈 `grep -q "^fn() {"` 要一起加**，只加
    一处等于继续空过。另：主套件刻意不装写目标提取链，缺链时 H5 是 MISSED（**照放**）而不是照拒，
    它原来那句 WARN 写的是"缺辅助即照拒"，会把人从名单引向产品，已按实测改掉。
  - **`bg_leak` 那句提示在说谎（bugList 46）**：shell 工具报"检测到命令使用了 & 后台符号…请改用
    run_in_background=true 重新启动"，可判据（`wchat.sh:1777`）从没读过命令文本——它只看"主体 pid 已
    消失/成僵尸 + 静默满 3s"。实测两例被拦命令里一个 `&` 都没有。真正的危害是**处置建议错**：主体已跑完、
    输出已完整回传，让我们"重新启动"就是诱导重跑，模型照做过一次、白烧一轮。判据本身是对的（不兜底就是
    写端被孤儿攥住、窗口永久挂死），改的只有文案 + 补了"还攥着写端的是谁"清单。**教训：给模型的提示
    只能陈述测到的事实**——它没有"再看看上下文"的余地，一句错的归因会被当成事实执行。
  - **主套件已随仓库纳管**：`~/bin/tests/`（`auto_test.sh` + `stub_gateway.py` + 两个 vision 套件），
    `~/.cache/wchat-tests` 改为其符号链接，老路径老命令照旧可用。搬完踩实一条：脚本内定位被测脚本
    必须 `pwd -P`——走符号链接时逻辑 `pwd` 会把 `cd ..` 算成 `~/.cache`，`REAL` 直接指空。
  - Alpine 实跑另暴露三条"只在开发机上绿"的断言（都已修，写法记下来别再犯）：
    1. **往当前目录写文件**（TTL 转发文件用 `$WORK_ROOT/…`）：容器里 `:ro` 挂载当场
       `Read-only file system`，且连带 `t17fd: unbound` 让表猝死。现在这类临时文件一律
       `mktemp` 自带；同时沙箱判定新增一支——`[[ ! -w "$PWD" ]]` 时把工作根挪到
       `$HOME/.hw-workroot.$$`（与"脚本目录就是家目录"复用同一套挪动逻辑）。
    2. **命令文本里写 `$HOME`**（`deny_rule "grep -n 'rm ' $HOME/.ct/auto/audit.log"`）：
       root 容器下它变成 `/root/.ct/…`，`/root` 在 P2 名单里，规则号整个换掉（H3→NP、
       P3→H11）。判"敏感"要用两平台同源的锚点（`/opt/hw/…` 靠系统名单敏感、恒 H3），
       判"越界写"要用**不存在的顶层路径**（`/data/hw/…` 恒 P3）。这条与夹具第 9 条同源。
    3. **`~ 下非隐藏写`的正确规则号本身随布局变**：HOME 若在系统名单内（root 容器
       `/root`），它是 P2（不可放开），不是 macOS 的 P3/放行。表里加了 `HW_HOME_SYS`
       探针——**直接问被测函数**（临时把 `WORK_ROOT` 挪开再调 `hard_deny_path`），
       绝不在测试里抄第二份系统路径名单（名单一改就静默漂移）。
  - 端到端（真终端）用 `tty_e2e_check.sh`，不联网、不改文件：
    ```bash
    bash tty_e2e_check.sh /Users/jwan/bin/wchat.sh      # 必须在 Ghostty/iTerm 里跑
    ```
    A 段自动判"应答泄漏"（向真终端发 DA1/DA2/DSR/kitty 查询，看剥离后还剩什么）；
    B 段判"误吞按键"（按上/下/Ctrl-R/Esc，期望序列还在）。两条都要 PASS，`SKIP B`
    表示你没按键、不构成结论。非 tty 下直接拒跑（exit 2）——管道里跑它会同时得到
    假绿 A 与假红 B。

  - `test_pty_pager.sh` —— 关分页 env 与退出码（PASS=7）。
  - `test_history_render.sh` —— 历史渲染 ANSI 断言（PASS=7）。
- 主套件基线：**`unit` 稳定失败 6 项**，且改动前后是**同一批**（既有漂移，见第十节）。
  2024 本轮实测名单（`REAL=<任意版本>` 两边差集为空 = 零新增回归；旧的"4 项"口径已过期）：
  ```
  ✗ 普通越界 y 即通过        ✗ 高危 yes 算确认        ✗ yes 大小写不敏感
  ✗ deny[H5] echo x > ~/.bashrc      ✗ deny[H5] sed -i "s/a/b/" ~/.zshrc
  ✗ path-ok /private/var/folders/...
  ```
  前三条来自伙伴那次"确认交互重写"（`d0c2fbc`）之后主套件的期望没跟着改；后三条是
  H5 dotfile 口径与 macOS firmlink 归一。**判据不是数字，是名单**：改完 `wchat.sh` 跑
  两遍做差集，只允许"减少"或"同一批"，多出来的一律当新增回归处理。跑法：
  ```bash
  REAL=$HOME/bin/wchat.sh        bash ~/.cache/wchat-tests/auto_test.sh unit </dev/null
  git -C $HOME/bin show HEAD~2:wchat.sh > /tmp/base.sh
  REAL=/tmp/base.sh              bash ~/.cache/wchat-tests/auto_test.sh unit </dev/null
  ```
  必须带 `</dev/null`（否则真 tty 下 `path_access` 会阻塞等回车）。
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
6. **改凭据名单要成对改**：命令级 H6（`hd_re_secret_dir` / `hd_re_keyfile` / `hd_re_shadow`
   / `hd_re_secret_ref`）与路径级 P1（`hard_deny_path` 的两个 case + 扩展名判断）必须同
   名单。只改一侧就是制造"shell 拦死、文件工具放行"的口径分裂，等于没有保护；
   `test_hardwall.sh` 的 T8 用成对断言钉这条（同一个文件两条链路必须同结论）。
6.1. **信任边界的权威定义只有 `in_root_exempt()` 一处**（判据侧 `hard_deny_path` 的 P1、
   链路侧 `path_access` 的第 1.5 步、命令侧 H6 的 `hd_creds_all_exempt` 三处共用它）。
   规则：归一后落在 `$WORK_ROOT` 内的路径不触发凭据/系统写这三族墙——按文件名命中的那批
   凭据分支只判边界外，于是项目级 `.npmrc` / `.netrc` 属普通配置文件，读写都放。两条例外：
   ① `WORK_ROOT == $HOME` 或 `/` 时豁免整体失效（边界本身就是家目录/根，等于没边界）；
   ② 末段是符号链接且真身在边界外时不豁免（`link_escapes_root()`，与"末段链接一律拒"同族）。
   命令侧同口径：`cat .npmrc` 与 `read_file .npmrc` 必须同结论；`cat .npmrc ~/.npmrc` 这类
   混合形态按最严判（有一条在边界外就整条拒）。
   踩过的两个坑（都锁进 T8）：用 `grep -oE` 抠命中片段只剩 `.npmrc`，`cat ~/.npmrc` 就被误判
   成边界内；带 `$()`/反引号的词去归一会拼出 `$PWD/$(cat ~/.npmrc)` 这种荒谬路径并判"边界内"
   —— 所以只豁免整段就是字面路径的词，与 `shell_note_grants` 不敢记账的词同一集合。
7. **H6 不许接通用的 `hd_recheck`**。那条脱敏走 `hd_is_readonly_verb` 白名单，里面有
   `cat/head/tail/less/strings/grep/sed/awk` —— 这些动词的带引号参数**就是文件本身**，
   照搬到凭据规则上，`cat '.npmrc'` 就变成放行，等于"加对引号即可绕过凭据墙"。
   H6 只能用 `hd_recheck_msg`（只脱 `echo`/`printf` 的引号参数，参数永远不会当文件用；
   也没收 `git`/`say`，它们带文件操作数）。缺 `hd_mask_msg` 时 `return 0` → 照拒，
   与 `hd_recheck` 同一条 fail-closed 约束，且必须**嵌在 `hard_deny_cmd` 内部**定义。

---

## 十、待办 / 已知边界
- [ ] **`tc_map` 改回 `declare -A`**（`wchat.sh:911` 附近）:当年为 macOS 自带 bash 3.2 留的
      绕行,口径统一 bash 5.x 之后理由已不成立。脚本已声明"只支持 5.x",但这条表达式冗长
      的东西还留着。**前提是先有 e2e 覆盖多轮工具调用历史回灌**——这里错了的表现是工具结果
      与调用对不上号,模型拿到错位上下文,硬墙表那三百多条静态断言抓不到它。
- [ ] **`hd_empty_token` 要不要改成直接收空参数**（`wchat.sh:3037`）:注释里的 3.2 归因已更正
      为"切词位置"问题;真正取舍是"空参数进数组后位置判据会不会错位",要动必须连 T13 的
      49 条一起回归。

- [x] 关分页器 env：**已实现**（6.4，1288/1289/1335，回归 `test_pty_pager.sh`）。
- [ ] 6.6 的四项：等输入探测提示、交互命令事前拦截、超时改按 `auto_mode` 取值、应答与请求的配对（当前口径是本程序不发查询，见 6.5）。
- [x] 终端应答泄漏（`^[]11;rgb:…^[`）：**已实现**剥离（6.5，1398/1662/1685/1696 + 收尾 drain），回归 `test_hardwall.sh` T17。
- [x] 硬墙族（H2/H3/H4）整串匹正则误杀只读命令：**已修**（脱敏 + 二次确认，
      回归 `test_hardwall.sh` PASS=56/0，拿 `git show HEAD:wchat.sh` 做负对照红 16 项）。
      做法：`hd_mask_readonly` 只把「段首是白名单只读动词的带引号参数」就地等长脱敏
      （分界符 `; | & 换行 < > $ 反引号` 原样保留，拼不出新命中），三条规则改成
      `[[ 整串 =~ ]] && { ! declare -f hd_recheck || hd_recheck <同一批正则>; }`。
      两条刻意的保守代价已锁进表里并标注 `[设计如此]`/`[已知缺口]`：未加引号的操作数
      一律不脱敏（`git log --grep=mkfs` 仍判 H4）；H1 提权等其余规则未接 `hd_recheck`
      （`printf 'sudo rm -rf /'` 仍判 H1）——扩过去各一行，等真撞到误杀再动。
- [x] **缺口 B：shell 人工放行不记账 → 同一外部路径永不收敛（已修）**。四个确认入口里
      shell 是唯一"批完什么都不留"的那个：`_shell_confirm_grant` 只 `read` 一个 y，
      下一轮换个文件还得再问。补 `shell_note_grants`：从命令里抽出**工作目录外**的路径
      （展开 `~`/`$HOME` 后归一，`resolve_path` 走读、`hd_write_targets` 走写），按 `r`/`rw`
      落进既有的 `granted_files` 名单，接在"高危确认通过"与"审批器 N → 人工确认通过"两个
      分支后面。三条边界：① 只记工作目录外（目录内的绝不进名单，否则等于给自己发越界牌）；
      ② **按要记的那一档过滤硬墙**，不是两档都查——`~/.cache` 读不拦、写拦（写墙覆盖家目录
      点目录），若因"写被拦"就连 `r` 也不记，人工批过的目录永远收敛不了；这不放水，因为
      墙永远在名单前面；③ `hard_deny_path` 不可用就不记（fail-closed），写成
      `hard_deny_path … && return 0` 时函数缺失返回 127 = "未拦" = 照样记，等于把授权
      发给没验过的路径。同一处不许 `2>/dev/null` 吞报错——本次揪出漏装载就是靠它叫出来。
- [x] **缺口 C：审批器只写"什么情况判 N"、没写"外不是非 N"（已修）**。提示词补正面口径
      「仅 `cd` 到工作目录外执行既有脚本、且无外部写删 → 判 Y」，并要求 `N` 的理由带规则
      编号（`N2:外部写入` / `N0:具体风险`），否则审计日志里无法复盘是哪条口径判的。
      表内两条断言锁着（缺任一即红）。
- [ ] **主套件 `auto_test.sh` 的抽函数名单**（`~/.cache/wchat-tests/auto_test.sh` 第 32-35 行
      `for fn in ...`）：名单里**已有** `path_access` / `hard_deny_cmd` / `hard_deny_path` /
      `auto_allow_hit`，缺的是 `auto_tmp_write_ok`（本次新增，`path_access` 第 4.5 步会调它）
      和前几轮欠的 `hd_write_targets` `hd_split_words` `hd_mask_readonly` `hd_is_readonly_verb`
      `hd_is_msg_verb` `hd_mask_msg` `shell_note_grants`。
      **判断：不必把 `hd_*` 逐个搬过去**——硬墙族的正反表在仓库内 `test_hardwall.sh`（T1~T9），
      两处各测一遍只会让口径漂移；但**必须补两样**：① `auto_tmp_write_ok` 一行（`path_access`
      的调用链要用到，缺了临时目录写会静默退回"照拒"，用例集里没有该放的断言就看不见）；
      ② 一道**装载完整性断言**——把"`hard_deny_cmd`/`path_access` 依赖哪些辅助函数"列成清单，
      逐个 `declare -F` 检查，缺即红。现有的守卫是 `grep -c '() {' >= 15`，它只防"整体改名"，
      防不住"名单不全"。为什么必须防：`hard_deny_cmd` 缺辅助时是 fail-closed（`command not
      found` → 127 → 判不成"未命中"→ 照拒），于是所有"该拒"的用例**继续绿**，误杀回归被完全
      掩盖——沉默的 fail-closed 比报错危险。该文件在工作目录外，`-auto=full` 下我改不了。
      **成品已做好并实测**：`~/bin/.auto_test.patched.sh`（三处改动：① `unit` 段补 bash5
      自举——原来 `BASH5` 只用在 e2e 驱动真脚本那一处，unit 漏了，这才是"3.2 下 4 项假红"
      的真正来源；② 名单补 `auto_tmp_write_ok`；③ `grep -c '() {' >= 15` 换成"名单声明项
      逐个校验，缺即红并指名"+ "名单外间接依赖缺失显式 WARN"）。落盘一条命令：
      `cp ~/bin/.auto_test.patched.sh ~/.cache/wchat-tests/auto_test.sh`
      实测五步：5.x 下 unit 全绿且 WARN 如实报出 7 个未装载的 `hd_*`；往名单里塞
      `no_such_fn_xyz` → 守卫当场红"沙箱缺函数:no_such_fn_xyz…测试不可信"；3.2 入口跑副本
      → 自举成功、4 项假红消失（`✅ 全部通过`）；3.2 入口跑**原件** → 4 项红复现；e2e 段
      经自举后仍 `✅ 全部通过`（参数透传与 `STUB_DIR` 不受影响）。顺带：e2e 驱动的是磁盘上
      的 `wchat.sh`，所以本次临时目录写的改动已经在 e2e 里跑过一遍真链路。
- [x] **主套件"基线 4 项红"是假红，不是口径漂移**：`工作目录内写未误判成越界`、
      `高危 yes 算确认`、`yes 大小写不敏感`、`普通越界 y 即通过`——同一个套件、同一份代码，
      用 `/opt/homebrew/bin/bash`（5.3.15）跑 **✅ auto_test 全部通过**，用 `/bin/bash`
      （3.2.57）跑才红这 4 项。根因是测试自身第 169 行的 `"$(case "$content" in *中文模式*)"`
      写法：3.2 会把 `(*` 当成命令替换的收尾，5.x 才按预期解析。
      **结论：跑表必须用 5.x**（`/opt/homebrew/bin/bash auto_test.sh`），别再拿 3.2 的红
      当产品缺陷去"决定改套件还是改实现"。生产侧本来就在 5.x 上：`wchat -> wchat.sh` 的
      shebang 是 `#!/bin/bash`（macOS 是 3.2），但脚本头部有 bash5 自举，找不到
      `/opt/homebrew/bin/bash` 就直接报错退出（`wchat.sh:80`）——`ps` 里那些
      `/opt/homebrew/bin/bash` 进程就是它自己。
      因此本轮那些 `${arr[@]+…}`、`${#arr[@]} -eq 0` 提前返回的 3.2 兼容改动**不回滚**：
      它们是 `test_hardwall.sh` 能在两个版本下都跑绿的前提（3.2 下 136/0、5.3 下 136/0
      各实测一次），而且自举之前的那几十行确实由 3.2 执行。但**不必再为 3.2 新增兼容工作**。
- [ ] `resolve_path` 对"目标恰好等于 WORK_ROOT"误判越界：**已修**（先把目标自身归一，
      再同时与基准本身及其子树比对；`list_dir` 传绝对路径 cwd 曾因此被拒），表 T5 覆盖。
- [x] 凭据口径分裂（shell 的 H6 恒拒、文件工具的 P1 却放行）：**已按「一致 + 严格」修**
      （回归 `test_hardwall.sh` T8 42 条，当时全表 114/0；补 T9 后全表 136/0。
      当时对 `HEAD` 跑本表红 16 项全在 T8，现口径见第八节的负对照小抄）。
      起因是一条 `npm config get registry; …; cat ~/.npmrc` 在 `-auto=full` 下被 H6 整条拒。
      调查结论：整条拒 + auto 下不升级人工，都是**设计**（硬墙注释写明"AI 与人都无权批准"，
      auto 分支直接 `continue`，只有非 auto 才要 `yes`）；但实现有四处 bug ——
      ① `hd_re_secret_dir` 里 `.npmrc/.netrc/.pypirc` 是**裸词无锚定**，仓库根的项目级
      `.npmrc`（常放 `_authToken`）连 `ls` 都不许；② H6 没接二次确认，`echo "不要 cat ~/.netrc"`
      这类纯文本被误杀（同类假阳性在 H3 已修掉，属"扩 recheck 各加一行"的欠债兑现）；
      ③ 路径侧 P1 根本不认 `.npmrc/.netrc/.pypirc/.git-credentials`，且 `[[ "$np" == id_rsa ]]`
      拿整串比绝对路径是**死分支** —— 结果是 `read_file ~/.npmrc` 直接放行，H6 的全部立意
      被这一条清空；④ `echo x > .npmrc` 撞的理由文案写着"**读取**凭据被禁止"，与命令自相矛盾
      （H5 当年正是为此加了"真的要写才判"的限定）。
      修法选择：**没有真沙箱、又跑在用户真实磁盘上，"一侧放行"就等于没有保护**，所以两侧
      一起收紧而不是把 shell 放宽 —— P1 补凭据文件名单（不限位置）+ 目录补
      `.config/{gcloud,azure}` + 修死分支；H6 接**只用 `echo/printf`** 的 `hd_recheck_msg`
      （绝不能复用 `hd_recheck`：那份白名单含 cat/head/grep/awk/strings，它们的带引号参数
      就是文件本身，照搬等于给出"加对引号即可绕过"的口）；文案改成"访问凭据文件…任何档位
      都不放开，文件工具同判"。
      刻意接受的不便：凭据**文件名出现在命令文本里就拦**（含 `ls -l .npmrc`），所以维护者
      连 `grep '\.npmrc' wchat.sh` 都会被自己的墙拦下 —— 本轮实测被拦两次，改查这类规则时
      要用不带点的模式（`grep npmrc`、`grep 'git.credits'`）。已锁进 T8 的 `[设计如此]` 断言。
- [x] `write_file` 写 `/tmp` 被拒、而 shell 写 `/tmp` 畅通（口径分裂）：**已按 A+B 放行**
      （`auto_tmp_write_ok` + `path_access` 第 4.5 步；回归 `test_hardwall.sh` T9 共 22 条，
      全表 PASS=136/0；对 `HEAD` 跑 104/32）。
      支持放行的理由是**它没有收紧任何东西**：`cat > /tmp/x` 在所有档位一直是通的
      （P2 对临时目录先豁免），把文件工具关在门外只会把我推回 heredoc —— 而工具描述
      明写"不要用 heredoc（引号转义极易出错）",等于用可靠性换一个不存在的安全收益。
      但**没有照 P2 那样按目录前缀裸放行**：实测 `norm_path` 只归一已存在的**父目录**、
      末段是符号链接时不解析（`/tmp/link -> /tmp/link.real` 归一后仍是 `link`），而
      `tool_write_file` 落盘是 `jq -r .content > "$resolved"` 会跟随链接 —— 判定看到的
      名字与字节真正落点可以完全不同。`/tmp`、`/var/tmp` 是 1777，同机任何人预放
      `build.log -> ~/.npmrc`，"允许写 /tmp"立刻变成"允许写 `~/.npmrc`"，P1/P2 一起作废。
      所以豁免只给真实文件，**末段是链接一律拒**（这条洞走 shell 今天仍在：shell 是文本层
      判定，无法逐子句验链接；工具侧能验就先验上，别把两侧都留在漏的状态）。
      配套：三档都放（与 shell 同档语义）；显式 `--allow-write` 名单排在这一步之前，
      用户点名的授权不被隐式豁免抢优先级；写前仍 `auto_backup`；审计打 `auto_allow_tmp`
      与 `auto_allow_rw` 区分并计入放行配额；**不写进 `grant_file`** —— 缓存进名单会让
      第二次写从第 1 步直接返回、跳过链接复检。
      顺带纠正一处直觉：`/tmp` 与 `$TMPDIR` 不等价，本机实测 `/private/tmp`、`/var/tmp`
      是 `drwxrwxrwt`（1777，同机任何人可读、可抢注同名文件），`$TMPDIR`
      （`/var/folders/.../T/`）是 `drwx------`（0700，只有我自己）。"缓存目录"该指后者，
      含令牌的东西落 `/tmp` 就是本机广播。
- [x] 硬墙的 Linux 兼容三处（审 `#39/#42` 时顺出来的，同一块 `/tmp` 放行逻辑）：**已修**
      - **①** `auto_tmp_prefixes` 的候选清单原本是 macOS 形状（`/tmp /var/tmp $TMPDIR`），
        Linux 上事实上的第二个公共临时目录 `/dev/shm`（tmpfs + 1777，JVM/共享内存/构建工具
        默认写它）不在其中 → 命中 P2 的 `/dev/*` 被**永久拒**（P2 连 `--allow-write` 都放不开），
        正是注释里"拦下来只会把任务卡死"要避免的场景。修法：候选清单加 `/dev/shm`，
        取舍交给 `cd` 失败即 `continue`（macOS 上不存在 → 不入集合 → 行为不变），
        **不写 `uname` 分支**——分支意味着判据在非开发机上永远没被执行过。顺手补了数组去重
        （Linux 上 `TMPDIR` 通常未设 → 回落到 `/tmp`，与第一项物理同路径）。
      - **②** P2 系统路径名单只有 macOS 项，Linux 该有的 `/proc /sys /run /boot /lib /lib64
        /srv /media /mnt /var` 一个没列。后果不是"漏放"而是**降级**：这些写入掉到 P3，而
        P3 在 full 档 + `--allow-write` 命中时**可放开**，P2 永不放开——同一类"改内核参数/
        覆盖系统库"的动作在 Linux 上比 macOS 低一整档，且拒绝文案还把人误导去加 `--allow-write`。
        修法：名单补成两平台并集。刻意**不列 `/home`**（它在 Linux 上的角色 = macOS 的 `/Users`，
        两边都不在 P2 名单里，越界写归 P3；单侧加严就是制造新的口径分裂）。
        顺带纠正文档：P2 那行本来就写着含 `/var /boot`，是**代码落后于文档**，本次对齐。
      - **③** T9 整段把 `/private/tmp` 当字面量期望值 → 只在 macOS 有意义，Linux 上必红三条
        （`tw 2 "/tmp"`：Linux 的 `/tmp` 不是链接；归一化期望值：Linux 不加 `/private`），
        其中两条还属"侥幸绿"（测的是本机根本不存在的目录，等于没测）。修法：先算本机
        **物理真身** `$_tw_tmp` 再断言，并加一条元断言禁止表内再出现硬编码 `/private` 正例。
      - 连带修的两个同类陷阱：① 表尾 `EXPECT_TOTAL=179` 也是平台绑定的（Linux 上 `/dev/shm`
        真链接块多 2 条），改成按 `-d /dev/shm && -w /dev/shm` 推导，条件与 T9 里那个块逐字相同；
        ② 新写的"用户目录不进 P2"断言最初用 `/home/other/x` 字面量，在 macOS 上**假红**——
        `/home` 本身是指向 `/System/Volumes/Data/home` 的 firmlink（`ls -ldO` 带 hidden），
        归一后命中 `/System/*` 判成 P2。改成从 `$HOME/..` 推导本平台用户目录父级。
      - **验收**：新增 `T9b`（P2 名单两平台同构）+ T9 内 `/dev/shm` 分支与兜底守卫，共 35 条，
        全部用 `pdn`（钉**规则号**）而不是 `pd`（只分 deny/allow）——② 的表现形式是"照样被拒、
        只是号从 P2 变 P3"，二值断言根本看不出来。新表+新码 **214/0**；新表+`HEAD` 负对照
        **203/11**（① 1 条 + ② 10 条）；`HEAD` 表+`HEAD` 码基线 **179/0**。
        跑表**必须用 bash 5**（`/opt/homebrew/bin/bash tests/test_hardwall.sh`）：`/bin/bash` 是 3.2，
        会因 `read -t 0.2` 与 `${1,,}` 在 T10 假红 6 条，与判据无关。
      - 本机无 docker，Linux 分支用**语义探针**实测而非推演：造一个"非链接、`pwd -P` 不改写"
        的目录冒充 Linux 的 `/tmp` 喂进 `auto_tmp_write_ok`/`hard_deny_path`，确认
        未改写的 `/tmp` 字面量确实被豁免（rc=0）、其中真链接仍 rc=2、`.npmrc` 仍归 P1。
      - **下轮已修**（命令级正则两平台同构 + 私有临时目录，见下一条）。
- [x] 命令级正则的 Linux 形状 + 公共临时目录外泄（④，同一批审出来的续集）：**已修**
      - **家目录锚点抽成 `hd_home`/`hd_home_a`**：`~`／`$HOME`／`/Users/<name>`／
        `/home/<name>` 四写并集，H5(`hd_re_dotfile`/`hd_re_dotfile_cfg`)、H6
        (`hd_re_secret_dir`) 共用。之前只有 `/Users` → Linux 上读他人
        `.aws/.config/gh/.kube/.ct/history` 文本层全漏，而 P1 靠 `$HOME` 只保护自己。
        `/home/` **带尾斜杠**，不误伤 `/homeless`、`/homebrew` 下的普通读。
      - `hd_re_wipe_root` 补 `/home`；`hd_re_sys_redirect` 补
        `/proc /sys /run /boot /lib /lib64 /srv /media /mnt /root`（`/root` 与已有的
        `var/root` 成对，漏它等于让"写 root 的 authorized_keys"只剩可放开的 P3）；
        P2 名单同步补 `/root`。用户目录两侧**都不进** P2/H11，保持同构。
      - **顺手抓到一条死正则**：`hd_re_dl_exec` 要求 `-o <值>` 后**紧跟**执行段,而真实
        写法中间隔着 URL → 实测连表里老正例都判 NP。以前这漏放被"落盘那半会被 P2 拒"
        掩盖着,**上一轮把临时目录豁免之后掩盖失效了**——"下载→写 /tmp→执行"能整条跑通。
        放宽中间段为 `[^;&|]*`、末段收紧成"执行动词 + 绝对路径"(否则
        `curl -o x.json api && npm ci && bash ./build.sh` 这种正常构建被永久拒)。
      - **`+=` 追加形式是 harness 杀手**（我自己刚踩）：表的装载器是
        `grep -E '^(hd_|auto_|AUTO_|WCHAT_)='`，`hd_re_dotfile+=` 里的 `+` 不在字符类内
        → 该行**不被抽取** → 装载到的是半截正则 → 判据"看着存在"、线上永不命中，
        而全表照样绿。已改单行拼接，并加一条静态守卫禁止任何顶层 `hd_*+=`。
      - **静态守卫要断"设计不变式"，不是断字面量**：锚点抽成变量后，我最初那三条
        "`hd_re_dotfile` 行含 `/home/`"当场报红（字面量只剩 `hd_home=` 一处有）。
        改成 `hd_home` 含两平台 + 三条正则都引用 `$hd_home_a`（5 条），反而更贴近意图。
      - **负对照 55/55 精确吻合**：新表+新码 **274/0**；新表+`HEAD` 码 **219/55**，
        而 55 恰等于本轮新增断言条数（274−219）——每条都在旧码红、新码绿，没有一条
        是"新旧都绿"的凑数断言。分账核对通过：`114+30+27+9+6+7+9+12=214`（旧总数），
        `+T13 49 +T14 11 = 274`。
      - **诚实记录的"侥幸绿"**：`cat /home/bob/.ssh/id_rsa` 在**旧码上也没红**——
        它靠 `hd_re_keyfile` 里裸 `id_rsa` 那个分支命中，锚点修复对它无影响。真正
        只有锚点能救的是 `.aws/credentials`、`.config/gh`、`.kube/config`、
        `.ct/history` 四条（负对照里它们全红）。断言保留但标了注释，避免下轮误以为
        "这条已经证明过了"。
      - **本轮新增的残留缺口（刻意接受）**：① `hd_re_dl_exec` 不跨 `;`
        （`curl -o /tmp/x.sh url; sh /tmp/x.sh` 仍漏），放开 `;` 会让同串里两件不相干
        的事大面积误伤；② 同串"下载 + 跑任意绝对路径本地脚本"一并判 H2（误伤面，
        标 `[设计如此]` 锁进表）；③ `/home` 与 `/Users` 同为无边界前缀，
        `rm -rf /homebrew/x` 判 H3。
      - **下轮已修**（文件工具侧他人凭据，见下一条）。
- [x] 文件工具侧读**他人**凭据目录仍漏（上条记的"只剩一半"）：**已修**
      - 判据 `hd_foreign_cred_path` + 共用名单 `hd_cred_subdirs`（12 项）。三条刻意约束：
        **① 不碰自己的 `$HOME`**（那里由原有精确名单管——否则自己的工作目录常在 `$HOME`
        下，`~/.config/<app>` 里放的项目配置会被永久拒）；**② 只认"家目录直属层级"**
        （`/home/bob/proj/.ssh` 两侧都不拦，与命令级锚点同形——宁可一致地窄，不要一侧宽
        一侧窄）；**③ 名单与命令级 H6 同源**，并由一条静态守卫断"路径侧每项都能在
        `hd_re_secret_dir`/`hd_re_dotfile_cfg` 里找到"，防止以后一侧改名另一侧忘。
      - **同一坑第二次踩：归一化会让"以前缀写死的判据"当场失配。** 最初写成
        `case "$p" in /Users/*|/home/*)` —— 而 macOS 上 `/home` 是 firmlink，
        `norm_path` 会把它归一成 `/System/Volumes/Data/home/...`，前缀匹配直接漏；
        `/Users` 恰好不是 firmlink 才没暴露。改成**"包含 `/Users/` 或 `/home/` + 只剥一层
        用户名"**，归一前后、两平台同时命中，并留两条 firmlink 形态断言钉住
        （`/System/Volumes/Data/home/bob/.ssh`、`/System/Volumes/Data/Users/bob/.aws`）。
        上一轮的 `/home` 假红、这一轮的前缀失配，是同一件事的两次表现：**判据里凡出现
        写死的路径前缀，都要问一句"归一化之后它还在不在"**。
      - **`pd` 与 `pdn` 参数顺序不同**（`pd <want> <路径> <mode> <说明>` vs
        `pdn <want> <说明> <路径> <mode>`）：我按 `pdn` 的习惯写 `pd`，说明被当成路径、
        恰好"放行" → 断言**绿灯**、label 打成 `r`。规律：**label 短得不成句的 PASS，
        先怀疑参数错位**。已在断言旁注明两者签名相反。
      - **存在性守卫必须排在成批 `hf` 之前**：`if 未定义函数` 返回假 → 所有**反例**
        （期望 1）全部"通过"、所有正例全部红，18 条表象盖掉 1 条根因。负对照实测
        确实如此（HEAD 码下 7 条反例"侥幸绿"）。
      - `set -u` 下 `${#arr[@]}` 对**未定义**数组也是致命 unbound（旧版跑表会在守卫处
        当场猝死，连汇总行都没有）→ 先 `[[ -n ${arr[@]+x} ]]` 再取长度。
      - **验收**：新表+新码 **300/0**；新表+`HEAD` 码 **229/71**，且 71 可精确对账
        = 上轮 55 + 本轮 16（11 条 hf 正例 + 3 条端到端 pdn + 存在性守卫 + 名单计数守卫）。
        T15 的 26 条**不随平台变**（判据是"包含 + 剥一层"，不依赖本机有没有 `/home`），
        所以 `EXPECT_TOTAL` 的 Linux 分支仍只有 `/dev/shm` 那 2 条。
      - **刻意保留的差异**：① `list_dir /home/bob`（他人家目录**本身**）两侧都不拦——
        它能枚举用户名但不直读机密，命令侧同样不命中，属"一致地窄"；② 路径侧名单多
        `.azure` 一项（单向更严，静态守卫里显式跳过并注释）；③ 他人项目内的 `proj/.ssh`
        两侧都不拦（见约束②）。
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