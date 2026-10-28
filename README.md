# wchat

终端 AI 助手。B 阶段迁移后，**入口与业务逻辑全部是 Python**（bash 仅保留在 `tests/` 作测试基线）。

## 安装

```bash
python3 wchat_py/installdeps.py        # 可选:显式安装依赖(prompt_toolkit / rich / pillow)
./wchat                 # 首次若缺依赖会自动建 ~/.ct/venv 并安装(一次),之后直接启动
```

> 想跳过自举(CI/测试)：`WCHAT_NO_BOOTSTRAP=1 ./wchat`；想强制走 venv：`WCHAT_BOOTSTRAP_FORCE=1`。

## 运行

```bash
./wchat                 # 交互
./wchat -h              # 帮助
./wchat -st "问题"      # 单轮
./wchat -auto=full      # auto 模式(需输入大写 AUTO 确认)
./wchat -mcp            # 启用 MCP(需本机 mcporter / npx)
```

配置在 `~/.ct/config`，模板见 `configs/config.example`。

## 模型切换

```bash
python3 configs/ctmodel.py            # 查看当前模型
python3 configs/ctmodel.py qwen       # 切换
python3 configs/ctmodel.py qwen -n    # 预览 diff
python3 configs/ctmodel.py undo       # 回滚
```

## 测试

```bash
bash tests/b_acceptance.sh        # B 阶段验收(应为 11/11)
bash tests/run_gates.sh --main    # 主闸门(应为 run=45 pass=45)
bash tests/auto_test.sh e2e       # 行为 e2e
bash tests/brew_dist_test.sh      # 发行包验收(35 条:可复现/无字节码/装后能跑/Formula 结构)
```

## Homebrew（tap 在仓库内）

本仓库自带 tap：`Formula/wchat.rb`（安装期建 venv、依赖随包，运行期零联网）。

**安装方法与日常使用见 [`docs/brew-package.md`](docs/brew-package.md)**（装包、首次运行、
配模型、排障、发新版）。

```bash
brew install Manjioko/shell-scripts/wchat    # 一条命令,自动 tap
brew update && brew upgrade wchat            # 以后升级
wchat -config                                # 填一次接口地址与 API key(逐项问,不用排版)
wchat                                        # 开始用
```

发新版只跑一条命令（打包 → 打 tag → 推 GitHub → 建 Release → 更新 Formula）:

```bash
python3 packaging/release.py 0.3.0    # 或 patch/minor;--dry-run 演练
```

```bash
python3 packaging/homebrew/make_release.py 0.2.0
# 产物：dist/wchat-0.2.0.tar.gz、packaging/homebrew/Formula/wchat.rb
#       （可复现：同源码重复打包 sha 不变；不含 __pycache__/*.pyc）
```

- 本地试装（Homebrew 7 要求 Formula 在 tap 内）：
  ```bash
  brew tap-new local/wchat
  mkdir -p "$(brew --repository local/wchat)/Formula"
  cp packaging/homebrew/Formula/wchat.rb "$(brew --repository local/wchat)/Formula/"
  brew install local/wchat/wchat
  ```
  （会装 `python@3.12`；首次运行 `wchat` 在 `~/.ct/venv` 自举依赖）
- 发布到 tap：把 tarball 做成 release 附件，用
  `python3 packaging/homebrew/make_release.py 0.2.0 --url <release-url>` 重新生成 Formula，
  放进 `homebrew-wchat/Formula/wchat.rb`；
- Formula 安装后 `bin/wchat` 包装 `libexec/wchat`；首次运行缺依赖会在 `~/.ct/venv` 自举
  （见上文 `WCHAT_NO_BOOTSTRAP` / `WCHAT_BOOTSTRAP_FORCE`）。

## 目录

顶层只有三类东西:**一个入口**(`wchat`)、**一个实现包**(`wchat_py/`)、**非代码资产**
(`configs/ skills/ docs/ tests/ packaging/ Formula/`)。bash 时代散落在仓库根的
`input_helper.py` / `render_helper.py` / `setup.py` 已收进包内,顶层不再有可执行脚本。

| 路径 | 说明 |
|---|---|
| `wchat` | 唯一入口 shim(9 行):自举 → 装配 → `wchat_py.cli.main` |
| `wchat_py/` | 全部实现。分层见下表 |
| `configs/` | 配置模板、`ctmodel.py`(兼容壳,实现在 `wchat_py/setup.py:cmd_model`)、`mcporter.example.json` |
| `skills/` | 随包技能。首次运行补进 `~/.ct/skills/`(**只补缺、不覆盖**) |
| `tests/` | 闸门与测试;`tests/legacy_wchat.sh` 仅为 **bash 基线 oracle**,产品不引用 |
| `docs/` | 设计与迁移文档(`migration-to-python-B.md` 为 B 阶段结项) |

### `wchat_py/` 分层

改动前先认清自己在哪一层 —— **下层不许反向依赖上层**(`tests/layout_test.py` 会拦)。

| 层 | 模块 | 职责边界 |
|---|---|---|
| 入口 | `cli.py` | 命令行 → 一次会话:参数解析、`-h`、`main`、`run_turn`/`_call_llm` |
| 会话 | `session.py` `repl.py` `msg.py` `msgstore.py` `compaction.py` `compact_run.py` `history.py` `state.py` `cmdline.py` | 消息历史、轮次闭环、压缩、交互循环 |
| 工具 | `tools.py` `tools_spec.json` `toolrun.py` `tools_run.py` `filefmt.py` `toolcall.py` | 工具注册表 → 参数校验/横幅/分发 → 具体实现 |
| 安全 | `hardwall.py` `hd.py` `hd_re.py` `pathrule.py` `pathutil.py` `authz.py` `authparse.py` `approve.py` `auto_enable.py` | 硬墙判据、路径裁决、**授权账本** |
| 模型 I/O | `llm.py` `request.py` `prompts.py` `webs.py` `vision.py` `img.py` `imgstate.py` | 请求建体、SEAM 事件流、图片引用账本 |
| 终端 | `render.py` `term.py` `termpty.py` `termwin.py` `shellio.py` `textutil.py` `mdrender.py` `inputbox.py` | raw/pty/流箱/宽字符截断/markdown/输入框 |
| 运行期 | `runtime.py` `config.py` `bootstrap.py` `installdeps.py` `setup.py`(子命令:setup/model/mcp/skills) `audit.py` `apilog.py` `version.py` `mcp_boot.py` `mcp_bridge.py` `mcp_client.py` `skill.py` `seed.py` `_contract.py` `_run.py` | 配置装配、解释器兜底、依赖安装、记账、MCP、技能 |

三条硬约束(都有闸门钉住):

1. **`cli.py` 有行数上限**,已经切出去的函数(`_run_tool`/`_path_guard`/`_runtime_fallback`…)
   不许回流 —— 它曾经是 2078 行的上帝模块。
2. **`toolrun/approve/runtime/imgstate` 顶层不许 import `cli`**。跨层调审批要经
   `toolrun._seam()` **在调用时**取,否则测试的 `cli._auth_think = fake` 打桩会静默失效。
3. **共享账本只能原地修改**(`_GRANTED_PATHS`/`_IMG_REFS`/`_APPROVED_CMDS`)。写成
   `global X; X = {}` 会让两个模块各自持有一份,而且不报错 —— 现象是"授权记了却查不到"。
