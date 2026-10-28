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
