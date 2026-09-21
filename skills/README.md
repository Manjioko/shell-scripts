# skills/ —— 待安装的 SKILL.md 草稿

这里是**草稿区**:模型可以写(在工作目录内),但不生效。生效要人工挪过去:

```bash
cp -R skills/wchat-gates-runbook skills/wchat-hardwall-exit-map skills/ct-home-layout \
      ~/.ct/skills/            # 全部安装
# 或只装其中几条;装完新开会话才会被注入
```

> 为什么不让模型自己装:`~/.ct/skills/` 是 dot 目录,写入被 H5 拦 —— 防的是"运行时自己长技能"。
> 也别用软链接到别处:装载器对技能目录里的符号链接一律跳过(注入侧与读全文侧两处都堵)。

## 装哪几条(建议顺序)

| 技能 | 什么时候会救你 | 建议 |
|---|---|---|
| `wchat-gates-runbook` | 改完代码不知道要跑哪些闸门、红了先怀疑什么、加断言后基线数怎么重固化 | **第一批** |
| `wchat-hardwall-exit-map` | 命令被 H1–H14/P1–P3 拦下,照红条"替代:"走而不是换写法绕 | **第一批**(模型收益最大) |
| `ct-home-layout` | 有人想"顺手清理 ~/.ct"(已发生过一次:5 个技能被删,不可恢复) | **第一批** |
| `wchat-auto-precheck` | 开 `-auto`/`-st` 前确认工作根、档位、白名单、安装授权、回滚点 | 第二批 |
| `silent-failure-triage` | 没有报错也没有输出;套件跑到一半不出结论行 | 第二批 |
| `reproduce-before-theory` | 查根因时防"理论合理但没人复现" | 第二批 |
| `wchat-skill-writing` | 以后新增/修改任何技能之前 | 第二批 |

前 3 条足够先跑一两周。判断标准很硬:新会话里用自然说法问一句,看它有没有被 `read_skill` 调用过。**长期不触发的技能只有两种下场 —— 改 description,或者删掉。**

## 装之前先校验(30 秒)

```bash
for p in skills/*/SKILL.md; do
  d=$(awk -F'description: ' '/^description: /{print $2; exit}' "$p")
  n=$(awk -F'name: '        '/^name: /{print $2; exit}' "$p")
  b=$(printf '%s' "$d" | wc -c | tr -d ' ')
  printf '%-30s name=%-30s desc=%3dB %s\n' "$(dirname "$p")" "$n" "$b" \
    "$( [[ -n "$n" && $b -le 200 ]] && echo OK || echo '⚠ 名字为空 或 描述会被截(上限按字节宽度算)' )"
done
```

关键坑:`SKILL_DESC_MAX=200` 走 `clip_line_width`,它按**字节宽度**计数(汉字算 3)⇒ **中文描述实际只有约 66 字**,超了从尾部砍。所以最重要信息放前 30 字,别把"不适用"写在最后。

## 配套脚本(已存在)

闸门清单已收成一条命令:`bash tests/run_gates.sh`(`--list` 看清单,`--main` 加主套件)。
`wchat-gates-runbook` 技能里写的就是它的判读规则 —— **知识留在技能里,重复劳动留在脚本里**,这条分界就是这次拆出来的。
