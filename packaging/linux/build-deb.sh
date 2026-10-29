#!/bin/sh
# 构建 wchat 的 .deb 并产到 dist/。**在宿主机上运行**（需要 docker）。
#
# 为什么分两步容器:
#   ① wchat-deb:24.04 —— **有网**,只负责把 wheel 下到 /wheelhouse;
#   ② 把 wheelhouse 与源码一起挂给运行期容器,在里面跑 make_deb.py(那里有 dpkg-deb)。
# 运行期容器实测**没有出网**(curl pypi 超时),所以 pip 只能发生在 ①。
#
# 用法(仓库根):
#   sh packaging/linux/build-deb.sh 0.2.7
set -e
VER="${1:?用法: build-deb.sh <x.y.z>}"
WHEELS="$PWD/.scratch/wheelhouse"
mkdir -p "$WHEELS"

echo "▶ 1/2 取 wheel(有网,构建期)"
docker run --rm -v "$WHEELS":/out wchat-deb:24.04 sh -c 'cp /wheelhouse/*.whl /out/'
ls "$WHEELS" | wc -l | sed 's/^/  wheel 数: /'

echo "▶ 2/2 打 deb(容器内有 dpkg-deb)"
docker run --rm \
    -v "$PWD":/src \
    -v "$WHEELS":/wheelhouse:ro \
    wchat-deb:24.04 \
    sh -c "cd /src && python3 packaging/linux/make_deb.py --version '$VER' --wheelhouse /wheelhouse"

echo
echo "产物: dist/wchat_${VER}_*.deb"

