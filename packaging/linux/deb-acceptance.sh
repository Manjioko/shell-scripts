#!/bin/sh
# deb 验收的**容器内**部分。由 test-deb.sh 调起,不要直接跑。
#
# 为什么单独一个文件:整段塞进 `docker run ... sh -c "..."` 会撞上三层引号
# (宿主机 sh → docker → 容器内 sh),项目里已经因为这类事踩过 4 次,每次现象
# 都像"产品坏了"。落成文件后引号只有一层。
set -e
DEB="$1"
NAME=$(basename "$DEB")

echo '=== 0. 装前:确认真的没有 wchat ==='
if [ -e /usr/bin/wchat ]; then
    echo '✗ 镜像里本来就有 wchat,测不出东西'; exit 1
fi
echo '  ✓ 无 wchat'

echo '=== 1. dpkg -i ==='
dpkg -i "$DEB" > /tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; }
tail -3 /tmp/install.log

echo '=== 2. 依赖到位(离线,无 --index) ==='
/usr/lib/wchat/venv/bin/python -c 'import openai, httpx, prompt_toolkit, rich, PIL; print("  ok 五个依赖可导入")'
if [ -x /usr/lib/wchat/venv/bin/python ]; then echo '  ok venv 已由 postinst 建好'; else echo '✗ venv 没建'; exit 1; fi
if [ -d /usr/lib/wchat/wheels ]; then echo '  ok wheel 随包落地'; else echo '✗ 没有 wheels'; exit 1; fi

echo '=== 3. 从非仓库目录跑,验证不依赖 cwd ==='
cd /tmp
wchat --version || { echo '✗ --version 失败'; exit 1; }
wchat -h > /tmp/help.txt 2>&1 || { echo '✗ -h 失败'; cat /tmp/help.txt; exit 1; }
if grep -q '使用方法' /tmp/help.txt; then echo '  ok -h 有使用方法'; else echo '✗ -h 无内容'; exit 1; fi

echo '=== 4. 离线跑一个真业务命令(不联网) ==='
if wchat usage report > /tmp/usage.txt 2>&1; then
    echo '  ok wchat usage report 可运行'
else
    echo '✗ usage 失败'; cat /tmp/usage.txt; exit 1
fi

echo '=== 5. ctmodel 兼容壳指向同一实现 ==='
ctmodel 2>&1 | head -2
wchat model 2>&1 | head -2

echo '=== 6. 装了什么(文件清单抽查) ==='
dpkg -L wchat | grep -E '/usr/bin/(wchat|ctmodel)$|/usr/lib/wchat/wheels$' | head -6
echo "  文件数: $(dpkg -L wchat | wc -l)"

echo '=== 7. 卸载后不留 venv ==='
dpkg -r wchat > /tmp/rm.log 2>&1 || { cat /tmp/rm.log; exit 1; }
if [ -d /usr/lib/wchat/venv ]; then echo '✗ venv 还在(prerm 没生效)'; exit 1; else echo '  ok venv 已删'; fi
# ⚠ 必须用 `[ -e 路径 ]` 判,不能用 `command -v`:dash 会缓存命令路径,
#   文件删了 `command -v` 仍报旧路径 —— 第一版就是这么假红的。
if [ -e /usr/bin/wchat ] || [ -e /usr/bin/ctmodel ]; then
    echo '✗ 可执行还在'; exit 1
fi
echo '  ok 可执行已移除'
echo
echo 'DEB-ACCEPTANCE-OK'

