#!/usr/bin/env bash
# 发版薄壳:真正的逻辑在 packaging/release.py(便于单测与复用)。
#   ./release.sh 0.3.0            发 0.3.0
#   ./release.sh patch            补丁号 +1
#   ./release.sh 0.3.0 --dry-run  演练
set -euo pipefail
cd "$(dirname "$0")"
exec python3 packaging/release.py "$@"

