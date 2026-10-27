# wchat 安装与使用（Homebrew 私有 tap）

面向上手：装包 → 首次运行 → 配好模型 → 日常用法 → 出问题怎么查 → 怎么发新版。
打包/发版的内部细节在文末「发新版」一节，日常使用可以不看。

---

## 1. 前置条件

| 项 | 要求 | 说明 |
|---|---|---|
| 系统 | macOS（Apple Silicon / Intel） | Homebrew 私有 tap 走这条；Linux 见 §7 |
| Homebrew | 已安装 | `brew --version` 有输出即可 |
| Python | 由 Formula 自动装 `python@3.12` | **不需要**自己装 Python |
| 网络 | 首次运行需要 | 首次会建 venv 并 `pip install` 依赖（约 20–60 秒） |
| 模型服务 | 一个 OpenAI 兼容端点 + API key | 见 §3 |

代码本身兼容 **Python 3.9+**，但 Formula 固定用 `python@3.12`，避免撞系统自带 Python
的权限/包冲突问题。

---

## 2. 安装

### 2.1 从 GitHub 装（正式方式）

```bash
brew install Manjioko/shell-scripts/wchat
```

一条命令就够：Homebrew 会自动 tap `Manjioko/shell-scripts`（拉的是仓库默认分支
`main` 上的 `Formula/wchat.rb`），然后装依赖、建 venv、装 wchat。

> 仓库名不是 `homebrew-*` 前缀，所以**不能**写 `brew install wchat` 或
> `brew install Manjioko/shell-scripts`（前者要在 homebrew-core 里，后者少一层 formula 名）。
> 完整写法必须是 `owner/repo/formula`。
>
> 想要裸名（`brew install wchat` / `brew upgrade wchat`）：自动 tap 之后裸名就能用了，
> 或先手动 `brew tap Manjioko/shell-scripts` 一次。

升级（发新版后）：

```bash
brew update && brew upgrade wchat
wchat --version
```

### 2.2 本地试装（还没建远端 tap 时）

```bash
cd /path/to/wchat
python3 packaging/homebrew/make_release.py 0.2.0     # 生成 dist/*.tar.gz 与 Formula
brew tap-new local/wchat
mkdir -p "$(brew --repository local/wchat)/Formula"
cp packaging/homebrew/Formula/wchat.rb "$(brew --repository local/wchat)/Formula/"
brew install local/wchat/wchat
```

### 2.3 装完自检

```bash
wchat -h          # 应打印「使用方法」
brew test wchat   # Formula 自带的冒烟测试
```

`wchat` 被装成三部分（Homebrew 的标准布局）：

```
$(brew --prefix)/bin/wchat           ← 3 行 wrapper,exec 下面那个 python 脚本
$(brew --prefix)/opt/wchat/libexec/  ← 真正的代码:wchat + wchat_py/ + requirements.txt
$(brew --prefix)/opt/wchat/libexec/doc/  ← 本文件与 README
```

---

## 3. 第一次运行：配模型

### 3.1 一条命令配好(`wchat -config`)

第一次用只需要这件事:

```bash
wchat -config
```

它会**一项一项问你**(每项都写清了长什么样、从哪拿),你只要粘一个值:

```
  配置 wchat  → ~/.ct/config(权限 600)
  逐项填写;只想改某一项时,其余项直接回车保留原值。

  BASE_URL
    服务商给的接口地址。要写到 /v1/chat/completions 结尾;只写 https://<域名>/v1 也行,会自动补齐
    现在: (未设置)
    › https://api.deepseek.com/v1

  WCHAT_API_KEY
    服务商控制台建的一串(通常 sk- 开头)。输入不回显,粘上回车即可
    现在: (未设置)
    ›

  FLASH_MODEL
    服务商文档里的对话模型名,如 deepseek-chat / qwen-plus
    现在: (未设置)
    › deepseek-chat

  已写入 ~/.ct/config(600)
  验证中…  ✅ 配置成功 连通了(deepseek-chat)

  现在直接运行 wchat 开始用。
```

四条要点:

1. **不需要你排版**。一项就是一个值,值里带 `/`、`=`、空格都行 —— 不用自己拼
   `KEY=值`、不用管换行;
2. **key 不回显**(传统做法),也永远不会打印到屏幕上或日志里;
3. **写盘前逐项校验**，报错会指到具体哪处不对(含空格、两键粘一起、引号不配对、
   模型名带空格、URL 不是 http…);
4. **写完当场验证能不能连通**（真实请求）。不通就告诉你 401 / 404 / 模型名错 /
   连不上，并说明怎么改 —— 已填的值不会丢，重跑不用重敲。

其它写法：

```bash
wchat -config "BASE_URL=https://api.deepseek.com/v1 WCHAT_API_KEY=sk-xxx FLASH_MODEL=deepseek-chat"
wchat -config --check "..."        # 只校验,不写盘
wchat -config --no-verify "..."    # 先不验连通(离线/内网)
wchat -config -h                   # 帮助
```

### 3.2 没配置时 `wchat` 会怎么表现

**只提示，不进会话**（退出码 2，并在末尾写明原因）。这样不会出现"半配置状态进了
循环，随便敲一句就报 `API 请求失败: Connection error.`"：

```
$ wchat

  ✗ 还不能开始用

    · 还缺 BASE_URL —— 服务商给的接口地址
    · 还缺 WCHAT_API_KEY —— 服务商控制台建的那串
    · 还缺 FLASH_MODEL —— 服务商文档里的对话模型名

  下一步:

    wchat -config

  它会一项一项问你(每项都说明了长什么样),填完当场验证能不能连通。

  退出码 2：缺少必要配置
```

退出码 2 的具体原因（脚本里可以用它对账）：

| 原因文本 | 什么情况 |
|---|---|
| `缺少必要配置` | 三项里有缺的 |
| `配置格式不正确` | 有值但形态不对 |
| `配置无法加载(文件权限)` | `~/.ct/config` 权限不是 600/400 |
| `无法连通配置` | 填得对，但请求打不通（key/模型/网络） |
| `已取消,未修改` | 交互时全部留空或 Ctrl+C |

非交互场景（管道 / CI / `< /dev/null`）不会卡住，会打印一行可直接复制执行的命令，
同样退出码 2。

### 3.3 手工配置(等价)

也可以不用 `-config`，直接写文件：

```bash
mkdir -p ~/.ct
cat > ~/.ct/config <<'EOF'
BASE_URL="https://api.example.com/v1/chat/completions"   # 注意:要写到 /chat/completions
WCHAT_API_KEY="sk-xxxx"
FLASH_MODEL="example-flash"
PRO_MODEL="example-pro"
EOF
chmod 600 ~/.ct/config        # ★ 必须 600(或 400),否则 wchat 拒绝加载
```

三条读取规则（照 master 的行为，踩过才写的）：

1. **权限必须是 600/400**。文件里是明文 key，同机他人可读等于把 key 交出去；
   权限不对时 wchat 会打印 `✗ ... 权限为 644(应为 600),已拒绝加载` 并**不读这个文件**；
2. **已经拿到 key 就不读文件**：环境里已有 `API_KEY`/`WCHAT_API_KEY` 时，整个文件跳过。
   想用文件里的凭据，就别在 shell 里 export 旧 key（也别写在 `.zshrc`）；
3. **文件不覆盖已有环境变量**：临时试别的端点用
   `BASE_URL=... wchat`，不需要改文件。

模板见包内 `configs/config.example`（`$(brew --prefix)/opt/wchat/libexec/configs/`）。
（手工写文件也可以，但**更容易错**：漏引号、两行粘成一行、权限忘改都会被
`wchat` 明确拦下并告诉你怎么改 —— 所以推荐直接用 `wchat -config`。）

`BASE_URL` 必须写成**完整端点**（含 `/chat/completions`）。SDK 的 `base_url` 是根路径，
wchat 内部会做转换；写成根会被再拼一层，表现为 404。

### 3.4 首次运行的"自举"(依赖,不是凭据)

`wchat` 启动时先做一次依赖检查：

1. 系统 Python 里 `openai`/`prompt_toolkit`/`rich` 都在 → 直接跑，**零安装**；
2. 缺任何一个 → 在 `~/.ct/venv` 建虚拟环境、`pip install -r requirements.txt`，然后
   re-exec 进这个 venv。多个终端并发启动由文件锁串行化，只有第一个真正装。
3. `~/.ct/venv/.ready` 里记着 `requirements.txt` 的哈希 + Python 版本；匹配就跳过检查，
   所以**只有依赖变了才会重装**。

相关开关：

| 变量 | 作用 |
|---|---|
| `WCHAT_NO_BOOTSTRAP=1` | 完全跳过自举（CI/测试用；缺依赖会直接报错） |
| `WCHAT_BOOTSTRAP_FORCE=1` | 即使系统依赖齐全也强制走 venv |
| `WCHAT_VENV=/some/path` | 换 venv 位置（默认 `$CT_HOME/venv`） |
| `CT_HOME=/some/path` | 换整个配置根（默认 `~/.ct`） |

Pillow 视为可选：缺它只影响图片缩放（会退回 macOS 自带 `sips`），不会因此触发建 venv。

### 3.5 模型档案切换

`~/.ct/config` 用 `# @model <名字>` 标记"哪组赋值生效"，配套的 `ctmodel` 负责改标记、不手改
（它随包安装，装完就有这个命令）：

```bash
ctmodel              # 看当前生效的是哪家
ctmodel qwen         # 切到某个档案(切换前自动备份 config.bak)
ctmodel qwen -n      # 只看 diff,不落盘
ctmodel -l           # 列出所有档案
ctmodel check --fix  # 校验配置(顺手修权限 600)
ctmodel undo         # 回滚
```

从源码仓库直接跑时用 `python3 configs/ctmodel.py`（同一个程序）。

---

## 4. 日常用法

```bash
wchat                  # 交互模式(REPL)
wchat -st "解释这个报错"   # 单轮:答完即退
wchat -t               # 开思考模式
wchat -p               # 用 pro 档模型(-dp = 思考 + pro)
wchat -l               # 列历史并选择加载;  -f = 忽略历史,开新会话
wchat -f "问题"        # 新会话 + 首轮内容
wchat -mcp             # 启用 MCP(需要本机 npx / mcporter)
wchat -h               # 全部选项
```

### auto 模式（会自己执行命令，务必先读）

```bash
wchat -auto=full       # 启动时要求手输大写 AUTO 确认
wchat -noauto          # 本次会话关闭 auto
```

auto 档下模型可以真的跑命令、改文件。安全边界是三层：

1. **硬墙**（H1–H14 等）：任何档位都不允许 AI 自己批准，命中即拒并把规则号回传给模型；
2. **审批器**：逐条判该不该执行；
3. **终审器**：对高风险动作再判一次，放行/拒绝各写一条审计。

审计与备份都在 `~/.ct/auto/`：

```bash
tail -f ~/.ct/auto/audit.log     # 每行一条 JSON:时间/规则/裁决/命令
ls ~/.ct/auto/backup/            # 回滚点
```

`MAX_TOOL_ROUNDS`（默认 **250**）是单轮用户输入内的工具调用总闸；撞顶会打印提示并结束
本轮，不会静默停下。`AUTO_MAX_APPROVALS`（默认 200）是自动放行配额。完整参数表见
`docs/auto-mode.md`。

---

## 5. ~/.ct 目录里都有什么

| 路径 | 内容 | 能删吗 |
|---|---|---|
| `~/.ct/config` | 凭据/模型配置 | 能（会丢配置） |
| `~/.ct/ctmodel` | 模型档案（`@model` 标记） | 能（会丢档案） |
| `~/.ct/history/` | 对话历史 | 能（会丢历史） |
| `~/.ct/skills/` | 技能 | **别删**（会丢自己写的技能） |
| `~/.ct/auto/audit.log` | auto 审计日志 | 能（但要留证就别删） |
| `~/.ct/auto/backup/` | auto 回滚备份 | 能 |
| `~/.ct/venv/` | 自举出来的虚拟环境 | 能（下次启动会重建，费一次安装时间） |

---

## 6. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| `缺少 openai 依赖:请 pip3 install openai` | 用了 `WCHAT_NO_BOOTSTRAP=1` 但依赖不全 | 去掉该变量让它自举，或 `python3 setup.py` |
| 首次启动卡在「📦 安装依赖」 | 正在下载（或网络不通） | 等；不通就检查代理，或先手动 `pip install -r .../requirements.txt` |
| 请求 404 | `BASE_URL` 写成了根路径 | 补成 `.../v1/chat/completions` |
| 请求 401/403 | key 没生效 | `~/.ct/config` 里键名是 `WCHAT_API_KEY`；环境变量优先于配置文件，检查是否有旧值残留 |
| 回复一直不开始 | 服务端静默挂死 | 有停滞兜底（`WCHAT_LLM_STALL`，默认 120s）会断开报错；可调小 |
| `wchat -h` 就报 import 错 | 包不完整 | `brew reinstall wchat` |
| 命令行分页器卡住 | `git`/`less` 等分页器 | 工具已注入 `GIT_PAGER=cat PAGER=cat`；自写命令加 `--no-pager` |
| 图片功能异常 | 缺 Pillow | 重跑自举，或 `pip install pillow` |

自检顺序（出问题先跑这三条）：

```bash
wchat -h                                    # 包是否完整
python3 -c "import openai, prompt_toolkit, rich; print('deps ok')"
bash tests/brew_dist_test.sh                # 在源码仓库里:发行包端到端验收
```

---

## 7. 升级、卸载、非 macOS

```bash
brew upgrade wchat                 # 升级
brew uninstall wchat               # 卸载(代码)
rm -rf ~/.ct                       # 卸载(配置/历史/技能/venv) —— 想留历史就别删
```

Linux / WSL2 目前不走 brew：直接用仓库里的 `python3 setup.py` 装依赖，然后 `./wchat`。

---

## 8. 发新版（维护者）

### 8.1 一条命令发版

```bash
./release.sh 0.3.0            # 发 0.3.0
./release.sh patch            # 补丁号 +1(当前 0.2.0 → 0.2.1)
./release.sh minor            # 0.2.0 → 0.3.0
./release.sh 0.3.0 --dry-run  # 演练:只打包和打印,不写 git、不推、不发布
./release.sh 0.3.0 --full-gates   # 连主闸门(45 道)一起跑,最稳
```

脚本会按固定顺序做完 8 步，**任何一步失败就停**（不会留下"tag 推了但包没传"的中间态）：

| 步 | 做什么 | 失败会怎样 |
|---|---|---|
| 1 | 检查 git 干净、gh 已登录、远程是 GitHub | 直接退出，不动仓库 |
| 2 | 跑发行包闸门（35 条）；`--full-gates` 再加主闸门 | 闸门红 → 拒绝发布 |
| 3 | 写 `wchat_py/version.py`（版本号唯一出处） | — |
| 4 | 打包 `dist/wchat-<ver>.tar.gz`（可复现：同源码同 sha） | — |
| 5 | 生成 Formula，url 指向 Release 资产地址 | — |
| 6 | commit(version+Formula) → 打 tag `v<ver>` → push 分支与 tag | push 失败 → 停，可重跑 |
| 7 | `gh release create` 上传 tarball | 已有同名 release 则覆盖上传资产 |
| 6.5 | 把 Formula **单独**推到 `main`（用 worktree，不打扰当前分支） | 被分支保护挡 → 警告，Release 仍发成 |
| 8 | curl 校验资产 URL（HTTP 200/302） | 不 200 → 警告但不算失败（CDN 有延迟） |

> 第 6.5 步是"别人 update 就能用"的关键。`brew tap` 拉的是仓库**默认分支**，
> 而 Formula 只依赖 Release 资产、不依赖源码，所以可以单独推这一个文件——
> 不必等整个开发分支合并进 main。要改推别的分支用 `--tap-branch <名字>`，
> 不想推就 `--tap-branch ""`。

发完会打印：版本、tag、资产 URL、sha256，以及别人怎么装/升级。

### 8.2 ⚠ 关于默认分支

`brew tap <user>/<repo>` 拉的是仓库**默认分支**（通常是 `main`），所以 `Formula/wchat.rb`
必须在 `main` 上。发版脚本的第 6.5 步会自动把 Formula 单独推到 `main`（只推这一个文件，
不动源码），因此**不需要**为了发版去合并开发分支。

源码什么时候合进 `main` 是另一件事，由你自己决定。

### 8.3 仓库必须是 public

历史上这个仓库是 private，实测结论：**私有仓库的 release 资产对匿名请求一律 404**，
而 Homebrew 下载 `url` 用裸 curl（不带 Authorization），所以 `HOMEBREW_GITHUB_API_TOKEN`
也不管用。现象是 `brew tap` 成功（走 git 凭据）但 `brew install` 报 404，极易误判成网络问题。

现已转为 public，匿名可下载（`curl -I` 返回 200），上面 2.1 的命令可用。

如果以后又改回 private，`release.sh` 第 1 步会再次报警并给出三条绕行方案
（转 public / 建公开 tap 仓库 / 仅手动安装）。

### 8.4 别人怎么装、怎么升级

> 前提：仓库是 **public**（见 8.3）。

```bash
# 首次安装(一条命令,自动 tap)
brew install Manjioko/shell-scripts/wchat

# 之后每次升级(你发完 release 后他们执行)
brew update && brew upgrade wchat
wchat --version
```

`brew upgrade` 为什么能拿到新版：`brew update` 会把 tap 仓库（含 `Formula/wchat.rb`）
拉到最新——新 Formula 的 `version`/`sha256`/`url` 都变了，于是判定"有新版本"并重装。
所以**发版脚本第 6 步必须把 Formula 一起提交并推上去**，只传 tarball 是不够的。

给用户看版本：

```bash
wchat --version     # wchat 0.3.0
```

### 8.5 手工发版（没有 gh 或想自己控制时）

```bash
python3 packaging/homebrew/make_release.py 0.3.0 --url https://github.com/<you>/<repo>/releases/download/v0.3.0/wchat-0.3.0.tar.gz
# 把 dist/wchat-0.3.0.tar.gz 手动上传到该 release，再把 Formula/wchat.rb 提交
```

### 8.6 打包规则

- **不含** `__pycache__` / `*.pyc` / `.DS_Store`；残留旧 `.pyc` 可能盖住新源码，装出来
  和仓库不一致，这类问题极难查；
- 文件 mtime 与 gzip 头固定为常量，**同内容 → 同 sha256**（可复现构建）；
- 必装清单：`wchat`、`wchat_py/`、`input_helper.py`、`render_helper.py`、`requirements.txt`、
  `configs/config.example`、`configs/ctmodel.py`、`README.md`、本文件。

发布前自查：

```bash
tar tzf dist/wchat-0.3.0.tar.gz | grep -cE '__pycache__|\.pyc'    # 必须是 0
tar tzf dist/wchat-0.3.0.tar.gz | head                            # 顶层目录名应是 wchat-0.3.0/
grep sha256 Formula/wchat.rb                                      # 与 shasum -a 256 一致
```

### 8.7 关于 `brew test`

`brew test wchat` 跑的是 Formula 里的 `test do` 块（断言 `wchat -h` 打印使用方法、
`ctmodel` 能读出当前模型）。依赖随包安装，所以**测试本身不需要联网**；
但 Homebrew 跑 test 前会先用 bundler 解析自己的 Ruby 依赖，这一步要访问 rubygems.org，
内网/断网环境会卡住或失败——那是 Homebrew 自身的问题，不是包的问题。
断网时改用：

```bash
wchat -h && wchat --version && (cd /tmp && ctmodel)
```