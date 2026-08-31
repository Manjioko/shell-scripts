# ===== harness 主逻辑(由 build 脚本拼接到提取的函数之后)=====
# 场景:
#   A  :双击 Ctrl+C —— 命令忽略 TERM 持续输出,第一下杀不掉,第二下应立即收尾
#   A2 :单击 Ctrl+C —— 正常命令,窗口应正常收尾且不置 input_aborted(本轮继续)
#   B  :窗口结束后 —— 进程替换 < <(sleep 100) 应回到主 shell 进程组,
#       Ctrl+C 能杀掉它让 read 返回(修复前 monitor 泄漏会导致永久阻塞)
case "${SCENARIO:-A}" in
    A)
        input_aborted=0
        # TERM/INT 全部忽略,持续每 0.5s 输出一行:第一下 Ctrl+C 杀不掉,
        # 且不断有数据(旧实现的 2 秒静默强制收尾也被持续输出挡住)
        run_with_window 'echo started; trap "" TERM INT; for i in $(seq 1 30); do echo tick; sleep 0.5; done'
        echo "WINDOW_RETURNED input_aborted=$input_aborted monitor=$([[ $- == *m* ]] && echo ON || echo OFF)"
        ;;
    A2)
        input_aborted=0
        run_with_window 'echo started; sleep 30'
        echo "WINDOW_RETURNED input_aborted=$input_aborted monitor=$([[ $- == *m* ]] && echo ON || echo OFF)"
        ;;
    B)
        input_aborted=0
        run_with_window 'echo wstart; sleep 0.2'
        echo "WINDOW_RETURNED monitor=$([[ $- == *m* ]] && echo ON || echo OFF)"
        # 验证 job control 恢复初始态:修复后 monitor=OFF,与程序启动时一致,
        # bash 会给 & 后台子进程置 SIG_IGN(SIGINT),^C 杀不掉它(BG_FIRED
        # 照常出现);若 set -m 泄漏(修复前),后台子进程继承 SIG_DFL,^C 会把
        # 它杀掉,BG_FIRED 缺失 → FAIL
        ( sleep 1.0; echo BG_FIRED ) &
        sleep 3            # 驱动器在此睡眠期间发 ^C
        echo "POST_INTERRUPT"
        sleep 1.5          # 保持存活,让 BG_FIRED 落盘
        echo "HARNESS_MAIN_END"
        ;;
esac
echo "HARNESS_DONE"
