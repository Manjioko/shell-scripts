---
name: ct-home-layout
description: 碰 ~/.ct 下任何东西前必读:各子目录是什么、哪些可写、哪些绝对不能删(技能、对话历史、审计日志)。
---

# `~/.ct` 目录地图(这个目录里有不可恢复的东西)

**触发条件**:任何要碰 `$CT_HOME`(默认 `~/.ct`)的操作 —— 列目录、写文件、清理磁盘、"删掉旧的"、改配置。

**一句话规矩**:这里**只有 `tmp/` 和 `auto/backup`、`auto/snapshot` 的旧会话**是可以动的;`skills/`、`history/`、`auto/audit.log`、`config` **一律不许删、不许覆盖**。本项目已经因为一次"顺手清理"丢过 5 个技能文件,不可恢复。

## 结构(实跑所见)

| 路径 | 是什么 | 能不能动 |
|---|---|---|
| `config` | 接口密钥、模型配置。**P1 级凭据** | 只读都不该试;改它请人工执行 |
| `history/` | 全部对话正文(实测 90+ 条)。**P1 判据专门护它**,因为泄露面是"说过的话" | **不可删**。任何 `rm ~/.ct/history*` 都会撞 H3/H5 |
| `skills/` | 技能:`<name>/SKILL.md`(frontmatter `name` + `description`) | **不可删**。往里写要人手工落盘(模型写会被 H5 拦,这是有意的:防止运行时自己长技能) |
| `auto/audit.log` | auto 模式的每决策一行 JSON(`ts/sess/tier/ev/tool/root/pol/target/rule/verdict/reason/trunc/tlen`) | **只追加**。删掉 = 无人值守的可追溯性归零 |
| `auto/backup/<session>/` | 每次覆盖写之前的**首覆**备份(`.bak`,路径压平:先转义 `+` 再把 `/` 换 `+`) | 可删旧会话的;当前会话那份别删 |
| `auto/snapshot/<session>/` | 非 git 仓库的整树 rsync 快照,默认留最近 3 份 | 同上 |
| `auto/writes-<session>.log` | 路径级熔断的写计数(每会话一个) | 当前会话删了 = 熔断清零,别动 |
| `tmp/` | 私有临时目录(0700 自检后使用) | **可以清**,这是它存在的目的 |

## 为什么"想清理"这个念头本身是红旗
`~/.ct` 通常只有几 MB 到几十 MB。要清空间应该去清 `~/Library/Caches`、包管理器缓存、Docker 镜像 —— 而不是一个装着"对话正文 + 审计 + 技能"的目录。**如果你正准备 `rm -rf ~/.ct/…`,停下来:先把它换成一句"我要删 X,这里面有不可恢复的历史/审计",问人。**

## 会话身份怎么看
`auto_session_id = YYYYmmdd-HHMMSS-<PID>`,`audit.log` 每行有 `sess`;`auto_report_exit`(会话结束)会打印本次的审计路径、备份数、**可复制的回滚命令**。想知道"刚才那次 auto 到底改了什么":
```bash
tail -50 ~/.ct/auto/audit.log          # 一行一决策,verdict=allow/deny
```
按规则号或工具筛:
```bash
grep -o '"rule":"[^"]*"' ~/.ct/auto/audit.log | sort | uniq -c | sort -rn | head
```

## 回滚:git 仓库与非 git 目录不同
- 工作根是 git 仓库 ⇒ 回滚点记的是 HEAD:`git diff <sha>` 看改动,`git checkout <sha> -- .` 复原。**开工前工作树有未提交改动会被明确警告**(`auto_report_exit` 里那句 ⚠):回滚会连带丢掉它们。
- 非 git ⇒ `rsync -a --delete ~/.ct/auto/snapshot/<session>/ <工作根>/`。**快照有体积上限**(超过就降级为 read 档并说明),别假设一定存在 —— 会话结尾的"回滚点"那一行才作准。
