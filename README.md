# wchat

终端 AI 助手。B 阶段迁移后，**入口与业务逻辑全部是 Python**（bash 仅保留在 `tests/` 作测试基线）。

## 安装

```bash
python3 setup.py        # 可选:显式安装依赖(prompt_toolkit / rich / pillow)
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
./release.sh 0.3.0          # 或 ./release.sh patch;--dry-run 演练
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

| 路径 | 说明 |
|---|---|
| `wchat` | Python 入口 shim（`wchat_py.cli`） |
| `wchat_py/` | 业务实现（入口/工具/硬墙/终端/渲染/MCP 等） |
| `configs/` | 配置模板、`ctmodel.py`、mcporter 清单 |
| `setup.py` | 依赖安装 |
| `tests/` | 闸门与测试；`tests/legacy_wchat.sh` 仅为 **bash 基线 oracle**，产品不引用 |
| `docs/` | 设计与迁移文档（`migration-to-python-B.md` 为 B 阶段结项） |