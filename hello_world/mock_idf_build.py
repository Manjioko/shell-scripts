#!/usr/bin/env python3
"""高保真模拟 ESP-IDF idf.py build 的 ninja 输出行为(本机无真实工具链)。

复现要点(与真实 idf.py build 一致):
1. 进度行用 \\r 覆盖刷新(ninja 在 tty 下行为),无 \\n;
2. 每行带 ANSI 颜色码(1;32m 绿),真实 idf.py 在 pty 下必带颜色;
3. 行内含超长完整路径(/home/wzk/.espressif/... 数百字符);
4. 输出断续(编译每个文件之间有停顿)→ 触发窗口 read 超时路径;
5. 周期性混入带 \\n 的输出(Partition 检查等)与结尾 flash 提示。

用法: python3 mock_idf_build.py [阶段数] [color|nocolor]
"""
import sys
import time
import random

STAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 535
COLOR = (len(sys.argv) <= 2) or (sys.argv[2] != "nocolor")

GREEN = "\033[1;32m" if COLOR else ""
RESET = "\033[0m" if COLOR else ""

# 模拟 esp-idf 编译输出的超长路径(与真实 build 目录结构一致)
COMPONENTS = [
    "esp-idf/esp_system", "esp-idf/esp_libc", "esp-idf/cxx", "esp-idf/freertos",
    "esp-idf/esp_hw_support", "esp-idf/esp_common", "esp-idf/esp_rom",
    "esp-idf/newlib", "esp-idf/heap", "esp-idf/log", "esp-idf/soc",
    "esp-idf/esp_mm", "esp-idf/spi_flash", "esp-idf/esp_partition",
    "esp-idf/esp_driver_gpio", "esp-idf/esp_driver_uart", "esp-idf/vfs",
    "esp-idf/esp_netif_stack", "esp-idf/esp_event", "esp-idf/esp_timer",
]
SRCS = [
    "src/port/esp32s3/system_internal.c.obj", "src/esp_timer_impl_lac.c.obj",
    "src/task_snapshot.c.obj", "freertos_kernel/tasks.c.obj",
    "freertos_kernel/queue.c.obj", "freertos_kernel/timers.c.obj",
    "src/newlib_init.c.obj", "src/malloc.c.obj", "src/log_level.c.obj",
    "src/esp_err.c.obj", "src/panic.c.obj", "src/cpu_util.c.obj",
    "src/memory_utils.c.obj", "src/startup.c.obj", "port/esp32s3/clk.c.obj",
]
KINDS = [
    "Building C object", "Building CXX object",
    "Generating project_elf_src_esp32s3.c",
    "Preprocessing linker script /home/wzk/.espressif/v6.0.1/esp-idf/components/esp_system/ld/esp32s3/memory.ld.in -> /home/wzk/shell-scripts/hello_world/build/esp-idf/esp_system/ld/memory.ld",
]

out = sys.stdout
out.write('Executing "ninja all"...\n')
out.flush()
time.sleep(0.2)

random.seed(42)
for i in range(1, STAGES + 1):
    kind = random.choice(KINDS)
    path = "/home/wzk/shell-scripts/hello_world/build/" + random.choice(COMPONENTS) + \
        "/CMakeFiles/__idf_" + random.choice(COMPONENTS).split("/")[-1] + \
        ".dir/" + random.choice(SRCS)
    # 真实 ninja:tty 下用 \r 覆盖进度,行尾无 \n
    out.write("\r" + GREEN + "[%d/%d] %s %s" % (i, STAGES, kind, path) + RESET)
    out.flush()
    # 断续停顿:真实编译每个文件之间有耗时,驱动窗口 read 走超时路径
    time.sleep(random.uniform(0.002, 0.04))
    # 周期性带 \n 的输出(Partition 检查/阶段完成提示)
    if i % 60 == 0:
        out.write("\n\n%s[%d/%d] 阶段检查完成,继续编译...%s\n" % (GREEN, i, STAGES, RESET))
        out.flush()

out.write("\n\n%sProject build complete. To flash, run:%s\n" % (GREEN, RESET))
out.write("idf.py -p PORT flash\n")
out.write("or idf.py -p PORT flash monitor\n")
out.flush()
