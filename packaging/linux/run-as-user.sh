#!/bin/sh
# 以**普通用户 + 真实 /home** 跑**单个**套件(Linux 容器内),原样输出全部断言。
#
# 与 gates-as-user.sh 的分工:那个跑整套闸门表(只打印结论行 + 末 3 行),
# 这个在定位单道红时用 —— 你要看的是"哪一条断言、得什么、期望什么"。
#
# 用法(仓库根):
#   sh packaging/linux/run-as-user.sh tests/py_pathrule_test.sh
#   sh packaging/linux/run-as-user.sh tests/interrupt_e2e.py     # .py 自动用 python3
set -e
SUITE="${1:?用法: run-as-user.sh <tests/xxx.sh|tests/xxx.py> [闸门过滤器]}"
shift 2>/dev/null || true
case "$SUITE" in
    *.py) RUN="python3 $SUITE $*" ;;
    *)    RUN="bash $SUITE $*" ;;
esac
docker run --rm -v "$PWD":/src:ro -v "$PWD/.scratch/emptybrew":/opt/homebrew:ro \
    wchat-linux:24.04 sh -c "
set -e
useradd -m -s /bin/bash runner 2>/dev/null || true
cp -R /src /tmp/w
chown -R runner:runner /tmp/w
su runner -c 'export PATH=/work/.venv/bin:\$PATH HOME=/home/runner; cd /tmp/w && $RUN' 2>&1
"