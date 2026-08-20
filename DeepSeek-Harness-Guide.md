# DeepSeek Harness (dsh) 本地安装与使用指南

> 本文件由安装过程自动沉淀，记录本机已完成的安装、启动方式和日常用法，方便随时查阅。
> 生成日期：2026-08-18

---

## 1. 项目信息

- 项目名：DeepSeek Harness（命令行工具叫 `dsh`）
- 官方仓库：https://github.com/deepseek-ai/deepseek-harness
- 官网：https://deepseek.com/harness
- npm 包：`@deepseek-ai/dsh`
- 许可证：MIT
- 架构：**万物皆插件（Everything is a plugin）**，基于 Cordis 组合框架
- 状态：**Developer preview**，官方明确说明迭代很快，**会有破坏性兼容变更**

---

## 2. 本机已完成的安装

| 项目 | 内容 |
|---|---|
| 安装方式 | `npm install -g @deepseek-ai/dsh`（全局安装） |
| 安装版本 | `0.1.0-rc.7` |
| 可执行文件 | `/Users/jwan/.nvm/versions/node/v22.22.2/bin/dsh` |
| 源码副本 | `/Users/jwan/bin/deepseek-harness`（`git clone --depth 1` 的只读源码，供查阅文档/源码） |
| Node.js | v22.22.2（nvm） |
| npm | 10.9.7 |
| pnpm | 11.8.0 |

验证安装：

```sh
dsh --version   # 0.1.0-rc.7
dsh --help
```

---

## 3. 快速启动（Web UI，最常用）

在任意想作为工作目录的地方启动：

```sh
dsh web
```

默认地址：**http://127.0.0.1:3080**（浏览器打开即可）。

自定义端口/地址：

```sh
dsh web --port 8080
dsh web --host 0.0.0.0 --port 3080
```

> `dsh web` 等价于 `dsh --profile web`。启动命令会打印实际 URL。

---

## 4. 首次使用步骤（Web UI）

1. 打开浏览器访问 http://127.0.0.1:3080
2. 配置模型：**本机已填好 DeepSeek API Key**（2026-08-18 从 `~/.zshrc` 的 `ANTHROPIC_AUTH_TOKEN` 读取并写入 `~/.dsh/.credentials.yaml` 的 `DEEPSEEK_API_KEY`，无需再填）。
   - 换 key / 新增提供方：**Settings → Models** 操作即可；也可以添加 Anthropic/OpenAI 等目录提供方，或自定义 OpenAI 兼容端点（公司网关、自建服务等）。
   - 手动换 key：编辑 `~/.dsh/.credentials.yaml`，保持文件权限 `0600`、目录 `0700`，保存后服务会热加载。
3. 选择工作区：点 **Choose workspace**，添加并选中你要让 agent 操作的项目目录（建议就是启动 `dsh` 时所在的目录）。
   - 未选 workspace 之前，会话输入框不可用。
4. 发起任务：新建会话，例如输入：
   > Summarize this repository and identify its main packages.

Agent 可以读取/编辑工作区文件、执行命令、委派子任务、维护计划；涉及需要审批的操作时 Web UI 会先询问你。

---

## 5. CLI 常用命令速查

```sh
# 启动 Web UI（默认 http://127.0.0.1:3080）
dsh web
dsh web --port 8080            # 换端口
dsh web --help                 # 查看 web 应用自身的参数

# 无界面/一次性任务（headless）：执行一个任务，打印最终答案后退出
dsh --profile headless "run the tests"
dsh --profile headless "总结这个仓库并找出主要包"

# 插件管理（在指定 profile 下转发给 pnpm）
dsh plugin --profile web add <package>

# 启动自定义 profile
dsh --profile <name>

# 查看组合后的配置（不启动）
dsh --dump-config
dsh --dump-default-config
```

参数顺序规则：launcher 自己的参数在前，第一个它不认识的 token 之后全部交给 profile 内的应用。

```sh
dsh --profile web --port 8080      # --port 属于 web 应用
dsh --profile headless "任务描述"    # "任务描述" 属于 headless 应用
dsh --help                          # launcher 自身的 help
```

---

## 6. 配置与数据目录

默认 `DSH_HOME` 为 **`~/.dsh`**（未显式设置环境变量时）。

| 内容 | 位置 |
|---|---|
| API 凭据（只写存储） | `~/.dsh/.credentials.yaml` |
| 用户设置 | `~/.dsh/settings.yaml` |
| profiles | `~/.dsh/profiles/<name>` |
| 用户级 patch | `~/.dsh/cordis.patch.yml` |
| 附件 | `~/.dsh/attachments/v1` |

---

## 7. 更新与卸载

更新到最新版：

```sh
npm install -g @deepseek-ai/dsh@latest
dsh --version
```

卸载：

```sh
npm uninstall -g @deepseek-ai/dsh
```

如需删除本地数据：备份后删除 `~/.dsh`（注意：里面含凭据和会话数据）。

---

## 8. 从源码运行（备用方式）

源码已在 `/Users/jwan/bin/deepseek-harness`，如果将来想从源码构建运行：

```sh
cd /Users/jwan/bin/deepseek-harness
git pull                    # 更新源码
pnpm install
pnpm run build
pnpm dsh web                # 从仓库运行 TypeScript 入口
```

> 日常使用直接用全局 `dsh` 命令即可，不需要从源码构建。

---

## 9. Python SDK（可选）

官方还提供 Python SDK（`deepseek-harness-sdk`），适用于程序化调用。前提：Python 3.10+、Git、Linux x64/arm64 或 macOS 14+ (arm64)。

```sh
cd /Users/jwan/bin/deepseek-harness
python -m venv .venv
. .venv/bin/activate
python -m pip install deepseek-harness-sdk

export DEEPSEEK_API_KEY=sk-your-key-here
# 可选：export DEEPSEEK_BASE_URL=http://127.0.0.1:8000/v1
python examples/jsonrpc-agent/minimal.py \
  --workspace /absolute/path/to/workspace \
  --session-root /absolute/path/to/sessions \
  --session-id example-001 \
  "Inspect the repository and fix the failing tests."
```

SDK 用法见 `deepseek-harness/docs/user/guide/python-sdk.md`。

---

## 10. 常见注意事项

- 当前为 **developer preview**，升级后旧配置/命令可能有破坏性变更，升级前留意官方 CHANGELOG 或 README。
- DeepSeek 官方 chat-completions 路由是纯文本模型；视觉模型需要自定义 provider 并配置 `input: [text, image]`。
- 密钥是只写的：保存后页面只显示脱敏描述符，不会回显明文。
- 修改模型配置后无需重启服务，下一次请求即生效。
- 默认绑定 127.0.0.1，只有本机可访问；用 `--host 0.0.0.0` 可暴露到局域网（注意安全）。
- 端口被占用时：用 `--port 0` 让系统分配空闲端口，启动日志会打印实际端口。

---

## 11. 文档索引（本地源码内）

- 项目 README：`/Users/jwan/bin/deepseek-harness/README.md`
- Web UI 指南：`/Users/jwan/bin/deepseek-harness/docs/user/guide/index.md`
- 模型配置：`/Users/jwan/bin/deepseek-harness/docs/user/guide/providers.md`
- Python SDK：`/Users/jwan/bin/deepseek-harness/docs/user/guide/python-sdk.md`
- CLI 说明：`/Users/jwan/bin/deepseek-harness/apps/cli/README.md`
- 架构文档：`/Users/jwan/bin/deepseek-harness/docs/architecture.md`
- 开发指南：`/Users/jwan/bin/deepseek-harness/docs/development.md`
- 配置目录（生成的插件配置目录）：`/Users/jwan/bin/deepseek-harness/docs/config-catalog.md`