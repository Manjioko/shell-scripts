#!/usr/bin/env bash
# 结构闸门包装:run_gates 只按 `bash <file>` 执行,故这里 exec Python 测试。
exec python3 "$(dirname "$0")/tools_complete_test.py" "$@"

