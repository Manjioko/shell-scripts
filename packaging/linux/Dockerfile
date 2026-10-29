# Linux 验证镜像:模拟"源码直跑"与"装完就跑"两种形态。
#
# 与 brew 的形态刻意对齐:依赖装进 /work/.venv(相当于 brew 的 libexec/venv),
# 入口用一个 wrapper 调 venv 的 python —— 只有在这条路上验证过,才谈得上做 deb。
#
# 用法:
#   docker build -f packaging/linux/Dockerfile -t wchat-linux:24.04 \
#       --build-arg BASE=ubuntu:24.04 packaging/linux
#   docker run --rm wchat-linux:24.04
#
# 构建上下文故意是 packaging/linux(很小),源码用挂载传进去,避免把 30MB
# dist/、.git 一起塞进镜像层。
ARG BASE=ubuntu:24.04
FROM ${BASE}

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    WCHAT_API_LOG=0 \
    WCHAT_NO_SETUP=1 \
    PYTHONUNBUFFERED=1

# 系统依赖:python3 本体 + venv + 最小构建工具。
# Debian/Ubuntu 的 python3 默认不带 venv(python3-venv 是单独的包)—— 少了它
# 自举会在 "python3 -m venv" 处失败,而报错是一句 ensurepip 的提示,很难第一眼看懂。
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        python3 python3-venv python3-pip \
        ca-certificates git curl locales jq util-linux procps perl file \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# 依赖装进 venv:与 brew 形态一致(不依赖发行版的 python3-xxx 包,
# 因为发行版里的 openai 版本对不上我们要求的 >=2.0,<3)
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /work/.venv \
    && /work/.venv/bin/pip install -q --disable-pip-version-check --no-warn-script-location \
       -r /tmp/requirements.txt \
    && rm -rf /root/.cache/pip

# 说明:**这里不再造 /opt/homebrew 软链**。
#
# 早期版本为了让测试夹具跑起来,曾 `ln -s /bin/bash /opt/homebrew/bin/bash`。那是隔离
# 手段,不是修复:它把"夹具假设我在 macOS"这件事藏起来了,于是 Linux 上永远是假绿。
# 现在夹具统一走 `tests/lib_bash.sh` / `tests/_bashbin.py` 解析 bash,`tests/fixtures/wchat_py`
# 也是相对软链 —— 所以镜像里**必须是真实 Linux 形态**(没有 /opt/homebrew),闸门红不红
# 才是可信信号。谁再需要那条软链,说明他引入了新的 mac 路径假设,`可移植性` 闸门会先红。

# wrapper:和 Formula 生成的 bin/wchat 同形(解释器由 wrapper 指定,脚本只是参数)
RUN printf '#!/bin/sh\nexec /work/.venv/bin/python /work/wchat "$@"\n' > /usr/local/bin/wchat \
    && chmod 0755 /usr/local/bin/wchat

CMD ["bash", "-lc", "echo 镜像就绪; python3 -V; /work/.venv/bin/python -V; wchat -h | head -3"]