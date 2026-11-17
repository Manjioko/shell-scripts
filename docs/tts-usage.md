# 语音合成(TTS)使用与配置指南

> 面向使用者:**怎么在两供应商(mimo / DeepSeek)之间配置和切换**。技术实现见 `docs/tts-tool.md`。
> 能力入口是工具 `text_to_speech`(模型可自主调用)。所有配置都写在 `~/.ct/config`(或在环境变量里)。

## 一、它是什么

`text_to_speech` 把一段文本合成语音、保存为 **wav** 文件,返回文件路径。支持两家供应商,同一套工具/配置变量,靠
`TTS_BASE_URL` 的域名自动识别是哪家——**你只改配置,不改代码**。

| 能力 | mimo | DeepSeek |
|---|---|---|
| 预置音色 | 有(冰糖/茉莉/苏打/白桦/Mia…) | 有(`stella` 女 / `tide` 男) |
| **声音复刻**(用你自己的声音样本) | ✅ 支持 | ❌ 不支持 |
| 风格/情绪/语速指令 | ✅ 支持(`style` 参数) | ❌ 不支持(忽略) |
| 请求模型名(TTS_MODEL) | 必填 | 不需要 |

## 二、配置变量一览(都写进 `~/.ct/config`,一行一个 `KEY="value"`)

| 变量 | 说明 |
|---|---|
| `TTS_BASE_URL` | **供应商的完整 TTS 端点**。决定用哪家(host 含 `deepseek` 就用 DeepSeek,否则用 mimo)。见下方各例 |
| `TTS_API_KEY` | 该供应商的 key。**不写也行**:当 `TTS_BASE_URL` 与主对话 `BASE_URL` 同域时,自动复用 `WCHAT_API_KEY` |
| `TTS_VOICE` | 音色名。mimo:`冰糖`/`茉莉`/`苏打`/`白桦`/`Mia`/`Chloe`/`Milo`/`Dean`;DeepSeek:`stella`/`tide` |
| `TTS_MODEL` | **仅 mimo 需要、且必填**。普通:`mimo-v2.5-tts`;声音复刻:`mimo-v2.5-tts-voiceclone` |
| `TTS_VOICE_SAMPLE` | **仅 mimo 声音复刻**用。你的声音样本**本地路径**(`.wav`/`.mp3`) |
| `TTS_FORMAT` | 仅 `wav`(默认) |
| `TTS_TIMEOUT` | 单次请求秒数(默认 120) |

> ⚠ key 的优先级:`TTS_API_KEY` **显式写了就一定用它**。所以**切供应商时**要么一起改 `TTS_API_KEY`,要么**删掉它**
> 让程序按同域自动复用主对话 key——否则会把上一家的 key 发到新供应商(→ 401)。

## 三、三种典型配置(照抄即可)

### 场景 1:mimo 普通预置音色
```
TTS_BASE_URL="https://api.xiaomimimo.com/v1/chat/completions"
TTS_API_KEY="<mimo 的 key>"
TTS_MODEL="mimo-v2.5-tts"
TTS_VOICE="茉莉"
```

### 场景 2:mimo 声音复刻(用你自己的声音)
```
TTS_BASE_URL="https://api.xiaomimimo.com/v1/chat/completions"
TTS_API_KEY="<mimo 的 key>"
TTS_MODEL="mimo-v2.5-tts-voiceclone"
TTS_VOICE_SAMPLE="/home/你/.ct/SAMPLE/我的声音.wav"
```
- 样本要求:`wav`/`mp3`,一般 15~60 秒清晰人声即可;文件不要太大(base64 后 ≤ 10MB,原始 ≲7.5MB)。
- 复刻模式下 `TTS_VOICE` 不生效(用的是样本音色)。
- 想换声音:换 `TTS_VOICE_SAMPLE` 指向的文件即可。

### 场景 3:DeepSeek(无复刻,只有两个音色)
```
TTS_BASE_URL="https://api.deepseek.com/v1/tools/tts"
TTS_API_KEY="<deepseek 的 key>"     # 或删掉此行,若主对话 BASE_URL 也是 deepseek 则自动复用
TTS_VOICE="stella"                  # 或 tide
# 不需要 TTS_MODEL
```

## 四、如何切换供应商

只改 `TTS_BASE_URL`(+ 视情况改 `TTS_VOICE`;mimo↔deepseek 时注意 `TTS_API_KEY`/`TTS_MODEL`,见上)。
两家不要的变量会被忽略(mimo 的 `TTS_MODEL`/`TTS_VOICE_SAMPLE` 在 DeepSeek 下不影响)。

## 五、怎么用

配置好后,在 wchat 里直接说:
- "把这段话读成语音:/把下面这段合成语音:…"
- "把这段语音放到桌面" → 输出到 Windows 桌面(`/mnt/c/...`)属工作根外写,会弹一次「工作目录外写」确认,**点"是"** 放行(记住该目录)。

## 六、常见问题排查

| 现象 | 原因 / 处理 |
|---|---|
| `错误:未配置 TTS_MODEL…` | 用的是 mimo 却没写 `TTS_MODEL`。mimo 必填;DeepSeek 不需要。 |
| `HTTP 404` | `TTS_BASE_URL` 不对。必须写**完整端点**(mimo `…/v1/chat/completions`;deepseek `…/v1/tools/tts`)。 |
| `HTTP 400 ... missing field 'text'` | 地址写成了别家的端点(比如把 DeepSeek 地址填在 mimo 形状下)。确认 `TTS_BASE_URL` 是哪家。 |
| `HTTP 401` | key 不对/发错家。检查 `TTS_API_KEY` 是不是当前供应商的;或删掉它走同域复用。 |
| `TTS_MODEL=…voiceclone 是复刻模型,但未配置 TTS_VOICE_SAMPLE` | 复刻模型必须给样本。补 `TTS_VOICE_SAMPLE` 或把 `TTS_MODEL` 换成普通模型。 |
| `声音复刻样本读取失败` | 样本路径不存在 / 不是 wav·mp3 / 太大。修正后重试。 |
| 报错后 AI 说"不重试" | 这是设计:失败会如实解释给用户、由你决定补充配置还是放弃,**不会擅自重试**。 |

## 七、边界与注意

- **声音复刻只有 mimo 有**;DeepSeek 只有 `stella`/`tide` 两个预置音色,且**不支持风格/语速指令**(`style` 被忽略)。
- **隐私**:复刻每次生成都会把你的声音样本发给 mimo(复刻固有代价);样本只在本机内部读取,不进对话记录/日志。
- **计费**:mimo TTS 限时免费(会变);DeepSeek 每万字符 3 元(限时 1.5 元,汉字算 2 字符)。
- 生成的永远是 **wav**(两家都支持;不产出 mp3/mp4)。

