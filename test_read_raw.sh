#!/bin/bash
# 测 stty raw 下 read -t -N 的超时行为
exec 2>&1
stty raw
t0=$EPOCHREALTIME
read -r -t 0.15 -N 1000000 chunk
rc=$?
t1=$EPOCHREALTIME
echo "rc=$rc chunk=[$(printf %q "$chunk")] 耗时=$(awk "BEGIN{print $t1-$t0}")"
stty sane
