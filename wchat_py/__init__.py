"""wchat 的 Python 迁移包。

每个模块承载 wchat.sh 里一个内聚功能单元的"活实现";bash 侧通过
`python3 -m wchat_py.<module> <subcommand> ...` 调用(统一入口 `py_run`)。

契约(退出码、stdin/stdout 分工、开关判据)只在 `_contract.py` 写一份,
方案与执行台账见 `docs/migration-to-python.md`。
"""

# 契约版本号:改了退出码语义或 stdout 格式时 +1,便于 bash 侧与闸门对账。
CONTRACT_VERSION = 1

