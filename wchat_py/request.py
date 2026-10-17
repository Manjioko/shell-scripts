#!/usr/bin/env python3
"""W12-4b：请求体构建（唯一出处，取代 wchat.sh 里 jq 版的 `make_request_body`）。

**字段顺序 = jq 的插入顺序**（model / messages / thinking / reasoning_effort / stream /
tools / stream_options）：parity 期要求与 jq 输出**逐字节相同**，而 `jq -n` 是按插入顺序
输出的，所以顺序本身就是契约的一部分（改了顺序 = 改了字节 = parity 红）。

只做**纯构建**：不读文件、不联网、不打日志。图片引用展开、刀1 观测（落盘状态）属于
W12-4b-2，见 docs §8.5。
"""
import json
import re
import sys

IMAGE_TYPE = "image_url"


def has_image(messages) -> bool:
    """与 jq 侧 `_JQ_HAS_IMAGE` 同义：messages 里有没有 image_url 部件。"""
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for it in c:
                if isinstance(it, dict) and it.get("type") == IMAGE_TYPE:
                    return True
    return False


def thinking_type(v):
    """`thinking` 可能以两种形状到达：对象 `{"type": ...}`（bash 建的请求体）或字符串。"""
    if isinstance(v, dict):
        return v.get("type") or "disabled"
    return v or "disabled"


def build_body(model, messages, thinking, tools, with_image=None):
    """构建请求体。`with_image` 仅测试用（不传则由 messages 推导）。

    注意两条**必须与 jq 侧逐字对齐**的判据：
      1. `thinking` 无条件包成 `{"type": <字符串>}`（多包一层会被服务端 serde 当枚举变体，422）；
      2. `reasoning_effort`：**带图且关思考** ⇒ `none`，否则 `high`
         （qwen 网关"带图 + disabled + high"会 400；实测见原 make_request_body 注释）。
    """
    t = thinking_type(thinking)
    img = has_image(messages) if with_image is None else bool(with_image)
    return {
        "model": model,
        "messages": messages,
        "thinking": {"type": t},
        "reasoning_effort": "none" if (t == "disabled" and img) else "high",
        "stream": True,
        "tools": tools or [],
        "stream_options": {"include_usage": True},
    }


_IMGREF_RE = re.compile(r"\[\[IMGREF:([A-Za-z0-9_]+)\]\]")


def ref_ids(text):
    """按出现顺序取**全部** IMGREF id 并去重。

    jq 侧用 `scan` 而不是 `capture` —— `capture` 对一段文本**只返回第一个**匹配,
    一条消息放多张图(用户一次甩来几张面板是常态)时只会展开第一张,现象是"只看到第一张"
    而不是报错。这里等价于 `scan | flatten | unique`(按首次出现保序)。
    """
    seen, out = set(), []
    for m in _IMGREF_RE.finditer(text):
        i = m.group(1)
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _drop(text, ids):
    for i in ids:
        text = text.replace("[[IMGREF:%s]]" % i, "")
    return text


def _trim(text):
    return text.strip(" \t\n")


def expand_refs(messages, ref_map, gone_pre, gone_hint):
    """把 `[[IMGREF:id]]` 标记换成真正的 image_url 部件(等价于 bash 的 `$_JQ_EXPAND_REFS`)。

    语义(逐条对齐 jq,顺序不能动):
      1. 只处理 **content 是字符串** 的消息,其余原样;
      2. 没有 id、或 id 全都不在 `ref_map` 里 ⇒ 原样(不产生任何改动,保住"无引用时逐字节相同");
      3. `ref_map[id]["url"]` 有值 = 活着 ⇒ 展开成 image_url 部件并**前置**,
         原文 `_drop` 掉全部已知 id 再 `_trim`,作为 text 部件**后置**;
         若有已回收的图,text 末尾追加 `\n<gone_pre>(N 张本次不在上下文),需要时重新调用 read_image`;
      4. 已知 id 全都已回收 ⇒ content 换成 `<gone_pre>\n<剩余原文>\n<gone_hint>`;
      5. 非字符串 content、非 dict 消息:原样。
    """
    out = []
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, str):
            out.append(m)
            continue
        ids = ref_ids(c)
        known = [i for i in ids if isinstance(ref_map, dict) and i in ref_map]
        if not ids or not known:
            out.append(m)
            continue
        live = [i for i in known if (ref_map.get(i) or {}).get("url")]
        gone = [i for i in known if not (ref_map.get(i) or {}).get("url")]
        m2 = dict(m)
        if live:
            parts = [{"type": "image_url", "image_url": {"url": ref_map[i]["url"]}} for i in live]
            txt = _trim(_drop(c, known))
            if gone:
                txt += "\n%s(%d 张本次不在上下文),需要时重新调用 read_image" % (gone_pre, len(gone))
            parts.append({"type": "text", "text": txt})
            m2["content"] = parts
        else:
            m2["content"] = "%s\n%s\n%s" % (gone_pre, _trim(_drop(c, gone)), gone_hint)
        out.append(m2)
    return out


def image_parts(messages) -> int:
    """数展开后 messages 里 image_url 部件的**总个数**(给 img_token_sample 用)。

    这一步替代 bash 侧 `jq '[..|objects|select(has("image_url"))]|length'` 读请求体文本:
    切换后请求体里只剩 [[IMGREF:id]] 标记,那个 jq 会数出 0 并**静默跳过图片计费标定**。
    """
    n = 0
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for it in c:
                if isinstance(it, dict) and it.get("type") == IMAGE_TYPE:
                    n += 1
    return n


def cmd_body(argv) -> int:
    """stdin 收 `{"model","messages","thinking","tools"}`，stdout 出请求体 JSON。

    与 jq 版一样：**stdout 只出正文**（调用方是 `$( )`），任何诊断走 stderr。
    """
    try:
        req = json.loads(sys.stdin.read())
    except Exception as e:
        sys.stderr.write("request: 输入 JSON 解析失败: %s\n" % e)
        return 2
    body = build_body(req.get("model", ""), req.get("messages", []),
                      req.get("thinking", "disabled"), req.get("tools", []))
    sys.stdout.write(json.dumps(body, ensure_ascii=False, indent=2))
    return 0


_COMMANDS = {"body": cmd_body}


def main(argv) -> int:
    if not argv or argv[0] not in _COMMANDS:
        sys.stderr.write("request: 未知子命令 %r\n" % (argv[0] if argv else ""))
        return 2
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))