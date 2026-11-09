#!/usr/bin/env python3
"""图片引用账本:一次会话里哪张图已经挂进上下文、引用号是什么、回收了没有。

`cli._call_llm`(把 ref→url 映射写进请求)、`toolrun._run_tool`(read_image 去重/重挂)
都要读写同一份账本,所以它必须有一个**单一归属**,不能继续挂在 `cli` 的模块全局里。

⚠ 账本用**原地修改**(`dict[...] = ` / `.clear()` / `list[0] = `)而不是重新绑定:
两个模块各自 `from ... import _IMG_REFS` 拿到的是同一个对象,原地改两边都看得见;
一旦某处写成 `_IMG_REFS = {}`,那一处就与另一处**永久分叉**,而且不报错。
"""
import os


_IMG_REFS = {}


_IMG_URLS = {}


_IMG_SEQ = [0]


def register_image_url(path, url, side=0):
    """登记一张待挂载图片,返回引用号(供 messages 里存 [[IMGREF:id]])。"""
    _IMG_SEQ[0] += 1
    ref = "r%d%d" % (_IMG_SEQ[0], os.getpid())
    _IMG_URLS[ref] = url
    try:
        _IMG_REFS[os.path.realpath(path)] = {
            "id": _IMG_SEQ[0], "ref": ref, "gone": False,
            "recheck": False, "side": side}
    except Exception:
        pass
    return ref


def reset_image_refs():
    """-n 新会话:历史被清空,图片引用登记也必须清零(否则首次读图会被误判"已在上下文")。"""
    _IMG_REFS.clear()
    _IMG_URLS.clear()
    _IMG_SEQ[0] = 0


# 兼容别名:历史上有代码(与测试)按 `_register_image_url` 这个名字取,实现只此一份。
_register_image_url = register_image_url
_reset_image_refs = reset_image_refs


def imgjson_parts(out):
    """解工具返回的 `IMGJSON:` 串 → (文本, [ref…]);不是这种返回则 None。

    **为什么要收成一个函数**:`cli.run_turn` 与 `session.orchestrate` 各有一份同样的
    解析逻辑(历史原因),加"一次返回多张图"(`refs`)时必然漏一处 —— 漏的那一处会把
    整个 IMGJSON 当普通文本塞给模型,图片永远不出现,而且不报错。与"三处必须同源"
    是同一条教训(见 mcp_boot._install_patches 的注释)。
    """
    if not isinstance(out, str) or not out.startswith("IMGJSON:"):
        return None
    import json as _json
    try:
        d = _json.loads(out[len("IMGJSON:"):])
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    refs = d.get("refs")
    if not isinstance(refs, list):
        refs = [d.get("ref")] if d.get("ref") else []
    refs = [r for r in refs if isinstance(r, str) and r]
    return (d.get("text") or "图片已读取"), refs


def with_markers(text, refs):
    """把引用号拼成正文里的 `[[IMGREF:…]]` 标记(下游 request.expand_refs 展开)。"""
    if not refs:
        return text
    return text + "\n" + "\n".join("[[IMGREF:%s]]" % r for r in refs)
