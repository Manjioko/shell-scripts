# TTS 工具(文本转语音)方案

> 状态:方案(v1 最小可运行)。目标是把 mimo 的 TTS 能力做成 wchat 的一个**工具**,把"要不要把某段话念成语音"的决定权交给模型;供应商可替换(mimo 打头阵)。
> 本文是实现的**唯一依据**,按「实现步骤」逐条落地,每步都有验收方式。

## 1. 目标与范围

- **形态**:一个新的**工具** `text_to_speech`(模型自主调用),不是"朗读模式"。
- **v1(本次)只做**:
  - 预置音色(`mimo-v2.5-tts`);
  - **非流式**,生成 **wav** 落盘,返回文件路径(**不播放**);
  - 音色可通过 `~/.ct/config`(或环境变量)**随时改**;
  - 供应商可替换:`TTS_BASE_URL` / `TTS_API_KEY` 独立于主对话配置,mimo 只作**默认值**。
- **v1 不做**(留后续):流式(pcm16)、播放、voiceclone / voicedesign、字幕、SSML。

## 2. mimo TTS 接口要点(依据官方文档 v2.5-TTS)

- 接口:**就是 OpenAI `chat.completions`**,多一个 `audio={...}`;返回 **`choices[0].message.audio.data` = base64 音频**(不是文本)。
- endpoint:`https://api.xiaomimimo.com/v1/chat/completions`(项目约定 BASE_URL 是**完整端点**、以 `/chat/completions` 结尾;与 setup.normalize_base_url 一致)。
- 模型:`mimo-v2.5-tts`(预置音色;当前最新只有 v2.5,v2.6 尚不可用)。
- `audio` 参数:`format`(`wav` / `pcm16`)、`voice`(预置音色 ID,见下)、voicedesign 才有 `optimize_text_preview`。
- 预置音色 ID:`mimo_default`、`冰糖`、`茉莉`、`苏打`、`白桦`、`Mia`、`Chloe`、`Milo`、`Dean`。
- 计费:限时免费(会变,别写死在文案里当承诺)。

### ⚠ 必踩的坑:角色语义反转

要合成的**目标文本必须放在 `assistant` 角色**;`user` 角色放**风格指令**(自然语言),预置音色时 **`user` 可选**。这跟正常对话 messages 正好相反——**绝不能**把当前对话的 messages 塞进去,必须新建独立的 messages:

```
messages = (
  [ {"role":"user", "content": <风格指令>} ,        # 有风格指令才加
    {"role":"assistant", "content": <要合成的文本>} ]
)
```

## 3. 配置设计

全部走环境变量(与项目既有 `FLASH_MODEL`/`WEB_SEARCH_PROVIDER`/`IMG_*` 一个路子);`~/.ct/config` 里任何 `KEY=value` 行会被 `runtime._load_ct_config` 读进环境,所以用户可在 config 里写 `TTS_VOICE` 等**随时改**。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TTS_MODEL` | `mimo-v2.5-tts` | TTS 模型名 |
| `TTS_VOICE` | `mimo_default` | 预置音色 ID;可改 `冰糖`/`茉莉`/`苏打`/`白桦`/`Mia`… |
| `TTS_FORMAT` | `wav` | v1 仅 `wav` |
| `TTS_BASE_URL` | `https://api.xiaomimimo.com/v1/chat/completions` | **完整的 POST 端点**(原样使用,代码不拼路径)。换供应商只改这一行(如 OpenAI: `https://api.openai.com/v1/audio/speech`)。仅"只填到 `/v1`"(官方 base_url 写法)时兜底补 `/chat/completions` |
| `TTS_TIMEOUT` | `120` | 单次请求秒数 |
| `TTS_API_KEY` | **(见下,不写死)** | TTS 专供 key |

### key 的处理(安全铁律,不能破)

项目有条硬约定(bugList #39):**明文 key 绝不进仓库**。所以 `TTS_API_KEY` **不写默认字面量**。解析顺序:

1. `TTS_API_KEY`(环境或 `~/.ct/config` 里的行)→ 用它;
2. 否则:**当 `TTS_BASE_URL` 与主对话 `BASE_URL` 同 host** 时,复用 `WCHAT_API_KEY`/`API_KEY`
   (即"把主对话切到 mimo 组"时,TTS 自动用同一把 mimo key);
3. 否则:工具**返回明确的配置指引**(不是崩),提示在 `~/.ct/config` 加
   `TTS_API_KEY="..."` 或 `export TTS_API_KEY=...`。

> 这样"现在就能用 mimo"的路径有二:①在 `~/.ct/config` 加一行 `TTS_API_KEY=<mimo key>`(音色同理加 `TTS_VOICE=冰糖`);②把主对话切到 mimo 组。**key 不会出现在代码/文档/git 里。**

## 4. 工具设计

- 名称:`text_to_speech`
- schema(`tools_spec.json` 追加一条;注意 `tools=` 字节会进请求体,影响前缀缓存——一次性变化,可接受):

```
name: text_to_speech
description: 把给定文本合成为语音并保存为音频文件(mimo TTS)。用于用户要求"把这段读出来/生成语音/配音"等。合成的是参数 text 的原文,不会朗读对话。默认音色取配置 TTS_VOICE,可传 voice 覆盖。返回生成的音频文件路径(供后续播放/引用)。
parameters:
  text     (string, required)  要合成的文本
  voice    (string, optional)  音色 ID,默认用 TTS_VOICE
  style    (string, optional)  自然语言风格指令(如"轻快上扬、语速稍快"),默认空
  out_path (string, optional)  输出路径,默认 <WORK_ROOT>/tts_out/tts-<时间戳>.wav
  format   (string, optional)  仅 "wav"(v1)
```

- **落盘路径与权限**:默认 `<WORK_ROOT>/tts_out/tts-<ts>.wav`(在 WORK_ROOT 内 → 过 `_path_guard("rw")`,不触发越界确认,也不污染 ~/.ct)。若模型/用户给 `out_path`,同样过 `_path_guard("rw")`(越界要么确认要么拒绝,和 write_file 一致)。
- **返回值(给模型)**:`✅ 已生成语音: <path>(<N> 字节 · 约 <X> 秒 · 音色=<voice> · 模型=<model>)`。便于模型告诉用户文件在哪、可再要求播放。
- **失败归一**:401→"TTS key 无效/缺失(检查 TTS_API_KEY)";超时/网络→明确"生成失败:…";响应里没有 audio.data→"返回格式异常"。

## 5. 模块划分

- 新增纯逻辑模块 `wchat_py/tts.py`(对齐 `webs.py`/`vision.py`:纯函数、可单测、无副作用):
  - `build_body(model, text, voice, style="", fmt="wav") -> dict` —— 组 chat 请求体(**角色反转**在上面固定)。
  - `parse_audio(resp: dict) -> bytes` —— 取 `choices[0].message.audio.data` 并 base64 解码;缺失/异常抛带信息的错误。
  - `default_out_path(work_root, ts) -> str` —— 默认落盘路径。
  - `fmt_result(path, nbytes, voice, model) -> str` —— 组装给模型的返回文案(秒数按 wav 24kHz/mono/16bit 估:`秒≈bytes/(24000*2)`;仅估算)。
  - `resolve_key() -> str` —— 按 §3 的 1/2/3 顺序解析 key(空串=没有)。
  - CLI 子命令(给闸门/调试用):`build_body`(读 stdin 文本)、`parse_audio`(stdin 响应 JSON→stdout 字节数)。
- 接线三处(和 mcp 教训一样,别漏):
  1. schema 层:`tools_spec.json` 追加;`tools.build` 直接吐原文(无需改,**但** `py_tools_test.sh` 的工具数基线要 +1)。
  2. 执行层:`toolrun._run_tool` 加 `if name == "text_to_speech":` 分支(urllib POST,仿 web_search 那句;写盘仿 write_file 的 `_path_guard`)。
  3. 横幅/校验层:`toolrun.known_tools()` 读 `tools_spec.json` **自动**认得(不用改寄存器),但 `register_known` 那套检查要确认新名字已在 spec 里。
- 默认值落地:`runtime._apply_runtime_defaults()` 里 `setdefault` 上表各项(用户 config/env 优先)。

## 6. 实现步骤(按序,每步可验)

1. **runtime 默认值**:`_apply_runtime_defaults` 加 6 个 `TTS_*` 的 `setdefault`。验收:`python3 -c "from wchat_py import runtime; import os; runtime._apply_runtime_defaults(); print(os.environ.get('TTS_BASE_URL'), os.environ.get('TTS_VOICE'))"` 出默认值。
2. **tts.py**:实现 `build_body/parse_audio/default_out_path/fmt_result/resolve_key` + CLI。
   验收:`tests/tts_test.py`(见 §7)全绿。
3. **tools_spec.json**:追加 `text_to_speech` schema(放在 run_shell_command 前或 web 工具附近,位置尽量别打乱既有顺序以少动缓存)。
   验收:`python3 -c "import json;print(len(json.load(open('wchat_py/tools_spec.json'))))"` 比原来 +1;`bash tests/py_tools_test.sh` 更新两处计数(§7)。
4. **toolrun 分支**:POST + 解码 + 落盘 + 返回。验收:桩响应下 `tts_test` 的 e2e 用例走通(不联网)。
5. **闸门接入**:`tests/tts_test.py` 加进 `run_gates.sh`(新闸门,按 `+=` 追加;结论行 `RESULT total=… pass=… fail=0`)。
6. **文档**:即本文;`tests/README.md` 基线说明补一行。
7. **smoke(可选,需真 key)**:配好 `TTS_API_KEY` 后 `wchat` 里让模型调 `text_to_speech`,或用 CLI 直连生成一个 wav。

## 7. 测试与闸门(`tests/tts_test.py`,全离线)

- `build_body`:角色反转(user=style、assistant=text;无 style 时只有 assistant)、`audio.format/voice` 正确、model 正确。
- `parse_audio`:正常 base64→bytes 往返;缺 `audio.data` → 抛错带信息;坏 base64 → 抛错。
- `resolve_key`:①显式 TTS_API_KEY 优先;②同 host 复用 WCHAT_API_KEY;③都无→空串。
- `default_out_path`:在 WORK_ROOT 下、文件名含时间戳、`.wav`。
- `fmt_result`:含路径/字节/音色/模型。
- e2e(桩 urlopen):给定假响应 `{"choices":[{"message":{"audio":{"data":<b64>}}}]}`,经 `toolrun._run_tool("text_to_speech", …)` 真跑,断言**写出文件**且字节等于解码结果、返回含路径;无 key 时返回配置指引(不崩)。
- 基线同步:`tests/py_tools_test.sh` 两处工具数 `11→10` 改 `12→11`、`11→12` 改 `12→13`(因为 spec 多一条);`tests/README.md` 补 `tts_test`。

## 8. 风险 / 备注

- **端点语义(关键设计,2026-11 用户提出并采纳)**:`TTS_BASE_URL` 是**完整 POST 端点、原样使用**,
  **代码绝不在路径上做拼接**。理由:不同供应商的 TTS 端点路径不同(mimo `/v1/chat/completions`、
  OpenAI `/v1/audio/speech`),在代码里拼 `/chat/completions` 换供应商必错(旧实现会把
  `.../v1/audio/speech` 拼成 `.../v1/audio/speech/v1/chat/completions` → 404)。所以路径差异
  **完全收敛到 config 里的一行**,错误可控、可读。唯一兜底:`TTS_BASE_URL` 只到 `/v1`(官方
  `base_url=.../v1` 那种没写到端点的写法)时补 `/chat/completions`,免得照官方示例填就 404;
  任何带真实路径的端点一律原样。404 时错误文案会带上当前端点并提示"该填完整端点"。
- **输出到工作根之外(如 Windows 桌面 `/mnt/c/...`)**:out_path 走 `_path_guard("rw")`,正常(非 auto)
  模式会弹一次「工作目录外写」确认,放行后记住该目录;auto 档需 `--allow-write` 白名单否则 P2 拒。
  这与 write_file 一致,属预期。

- **端点/密钥独立**:TTS 在 mimo 域名,当前主对话是 deepseek → 默认走"同 host 复用"或显式 `TTS_API_KEY`;主对话 key 不会误发到 mimo(host 不同不复用)。
- **前缀缓存**:加工具会一次性改变 `tools=` 字节(缓存从打到全价一次),之后稳定。
- **版本漂移**:仅 v2.5-TTS;若以后出 v2.6,改 `TTS_MODEL` 即可(已解耦)。
- **音频时长估算**:非流式 wav 按 24kHz/16bit/mono 估秒数,只是提示,不精确。
- **播放不在 v1**:WSL2 音频到 Windows 的播放器差异大,留后续单独评估。

## 9. 待确认

- key 来源是否接受 §3 的设计(不在仓库写死,靠 `TTS_API_KEY` / 同 host 复用 / config 行)。若坚持"默认就用某把 mimo key",请提供为环境/config 注入方式,**不写进代码或文档**。