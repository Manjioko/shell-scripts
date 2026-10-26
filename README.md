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
bash tests/run_gates.sh --main    # 主闸门(应为 run=41 pass=41)
bash tests/auto_test.sh e2e       # 行为 e2e
```

## 目录

| 路径 | 说明 |
|---|---|
| `wchat` | Python 入口 shim（`wchat_py.cli`） |
| `wchat_py/` | 业务实现（入口/工具/硬墙/终端/渲染/MCP 等） |
| `configs/` | 配置模板、`ctmodel.py`、mcporter 清单 |
| `setup.py` | 依赖安装 |
| `tests/` | 闸门与测试；`tests/legacy_wchat.sh` 仅为 **bash 基线 oracle**，产品不引用 |
| `docs/` | 设计与迁移文档（`migration-to-python-B.md` 为 B 阶段结项） |