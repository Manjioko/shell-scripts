# Linux / WSL 可移植性验证（当前阶段：容器内实测）

这里的东西**只用于验证**，不是发行物。目标是回答两个问题：

1. 代码在 Linux 上能不能跑（不是"看起来能跑"，是闸门真绿）；
2. 要打包成 deb 还差什么。

## 怎么用

```bash
# 构建（上下文很小，源码用挂载传进去）
docker build -f packaging/linux/Dockerfile -t wchat-linux:24.04 \
    --build-arg BASE=ubuntu:24.04 packaging/linux

# 冒烟探针：compileall / import / -h / 版本判据 / 依赖齐全（任一失败整体非 0）
docker run --rm -v "$PWD":/src:ro wchat-linux:24.04 bash -lc \
    'cp -R /src /tmp/w && cd /tmp/w && bash packaging/linux/probe.sh'

# 全量闸门
docker run --rm -v "$PWD":/src:ro wchat-linux:24.04 bash -lc \
    'cp -R /src /tmp/w && cd /tmp/w && export PATH=/work/.venv/bin:$PATH && bash tests/run_gates.sh'
```

⚠ **不要**再往镜像里塞 `/opt/homebrew/bin/bash` 之类的软链。那会把"夹具假设 mac"藏起来，
让 Linux 上的结果永远是假绿。镜像刻意保持**真实 Linux 形态**（没有 /opt/homebrew），
一旦有人（包括夹具）引入 mac 路径假设，`可移植性` 闸门会先红。

## 已验证可用（Ubuntu 24.04 / aarch64 / Python 3.12.3 / bash 5.2.21）

- `compileall` 全包通过；`import wchat_py.cli`；`wchat -h`；`_run.py filefmt read`；
- `MIN_PYTHON` 判据 `(3,9)` 通过；
- 依赖全部用 manylinux wheel 装进 `/work/.venv`，**不需要编译**：
  `openai 2.54.0 / httpx 0.28.1 / pillow 13.0.0 / prompt_toolkit / rich`。
  这否掉了"发行版 python3-openai 版本对不上"这条顾虑 —— 自带 venv + wheel 是可行路线。

## 本轮修掉的两个**夹具**问题（都不在产品里）

| 症状（Linux 上） | 真因 | 修法 |
|---|---|---|
| `写熔断 21 红`、`权限状态 3 红`、`工具运行` 全红 | 34 个测试文件写死 `/opt/homebrew/bin/bash`；Linux 上子 shell 静默失败 → 断言拿到全空 | 统一到 `tests/lib_bash.sh`（导出 `$WCHAT_BASH`）与 `tests/_bashbin.py`（`bash_bin()`） |
| `硬墙语料` 6 条 DIFF（H6-5/H6-8/H11-3/H11-6/H12-2/H12-3），像"Linux 上判据偏了" | `tests/fixtures/wchat_py` 是**绝对路径软链** → 换机器/容器里断链 → 夹具静默走降级（fail-closed），`hd_write_targets` 返回 1 → auto 下所有重定向命令判 P3 | 软链改相对 `../../wchat_py`；新增 `可移植性` 闸门钉住"不许绝对" |

两条都加了回归保护（`bash tests/portable_paths_test.sh`，7/7，含两次变异验证）。

## 仍未绿的道（分类，不是"环境噪音"）

> 数字随改动变化，以 `bash tests/run_gates.sh` 的结论行为准；这里只记**归因**。

| 归因 | 闸门 | 说明 |
|---|---|---|
| **夹具按 macOS 造** | `内联图片` | 被测的是 bash 版实现，它缩图用 `sips`（macOS 专有）。Linux 上没有 → 交付件不缩（2600px）。Python 版用 Pillow，正常 |
| **需要真实流量语料** | `流量回放` | 读 `~/.ct/auto/audit.log`；容器里没有 → 报"没有真实流量就不许报回放全绿"。这是**正确**的拒绝，应当视作 SKIP |
| **以 root 跑** | `技能目录`（4 条）/ 部分路径断言 | 判据是"他人属主目录被退回"，root 下写谁都能成 → 前提不成立。应以普通 uid 跑 |
| **既有：向量前提依赖 HOME 布局** | `全表断言`（2 条）/ `硬墙双路` | 向量用 `$HOME/bin`、`${HOME}/bin/y` 当"家目录下普通目录"，但 `bin`/`root` 自己在 H11 受保护名单里；root 容器 HOME=/root 时同一命令被判 H11（那是**正确**行为）→ 期望写 NP/P3 就假红。属既有问题，需单独一轮改向量，别顺手改期望值糊过去 |
| **待查** | `MCP通道`（37 条）/ `路径归一`（1）/ `路径裁决`（2）/ `Ctrl-C中断`（1） | 尚未定位到根因，按 `reproduce-before-theory` 先拿到最小复现再动 |

## 打包路线（建议顺序）

1. Docker 跑通 ✅（本轮）
2. pipx / uv 分发（自带 venv，绕开 PEP 668）
3. deb（先 Ubuntu 24.04，自带 manylinux wheels；`WCHAT_NO_BOOTSTRAP=1` 避免二次建 venv）
4. 扩 Debian 12 / Ubuntu 22.04
5. WSL 专项（剪贴板 `powershell.exe`、文件选择器、`/mnt/c` 性能）

安装布局建议 `/usr/lib/wchat/{wchat, wchat_py/, venv/}` —— `runtime._runtime_fallback()`
里"同级 venv"那条候选（`<包目录>/venv/bin/python`）天然覆盖这个形态，不用改代码。

