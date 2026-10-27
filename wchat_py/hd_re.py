hd_re_dev_sink = '[0-9]*>{1,2}&?[ \\t\\n\\r\\f\\v]*["\']?/dev/(null|zero|full|stdout|stderr|tty|fd/[0-9]+)'
hd_re_disk = '(^|[^A-Za-z0-9_.-])(dd[ \\t\\n\\r\\f\\v][^;&|]*of=/dev/(rd|sd|nvme|disk|hd)|mkfs(\\.[a-z0-9]+|[^A-Za-z0-9]|$)|diskutil[ \\t\\n\\r\\f\\v]+(erase|reformat|partitionDisk|ck)|fdisk|newfs|diskpart|chflags[ \\t\\n\\r\\f\\v]+[^ \\t\\n\\r\\f\\v]*[ \\t\\n\\r\\f\\v]+/)'
hd_re_dl_exec = '(curl|wget)[^;&|]*(-o[ \\t\\n\\r\\f\\v]+|--output[ \\t\\n\\r\\f\\v]+|-O[ \\t\\n\\r\\f\\v]+)[^;&|]*(&&|[ \\t\\n\\r\\f\\v])[ \\t\\n\\r\\f\\v]*(sh|bash|zsh|dash|ksh|source)[ \\t\\n\\r\\f\\v]+/[!-~]*'
hd_re_dotfile = '(^|[ \\t\\n\\r\\f\\v;&|><]|-i[ \\t\\n\\r\\f\\v]*|[A-Za-z0-9]-i)[ \\t\\n\\r\\f\\v]*"?(~|/Users/[^/ \\t\\n\\r\\f\\v]+|/home/[^/ \\t\\n\\r\\f\\v]+|\\$HOME)/\\.(bashrc|bash_profile|bash_login|zshrc|zprofile|zshenv|profile|gitconfig|global\\.gitignore|vimrc|gvimrc|inputrc|tmux\\.conf|screenrc|ct)([ \\t\\n\\r\\f\\v/]|$)'
hd_re_dotfile_cfg = '(~|/Users/[^/ \\t\\n\\r\\f\\v]+|/home/[^/ \\t\\n\\r\\f\\v]+|\\$HOME)/\\.config/([A-Za-z0-9-]+/(config|credentials)|gh/hosts)'
hd_re_force_push = '(^|[^A-Za-z0-9_.-])git[ \\t\\n\\r\\f\\v]+push[^;&|`]*(--force|[ \\t\\n\\r\\f\\v]-f([ \\t\\n\\r\\f\\v]|$))|git[ \\t\\n\\r\\f\\v]+push[^;&|`]*(--delete|--mirror)'
hd_re_history_rewrite = '(^|[^A-Za-z0-9_.-])git[ \\t\\n\\r\\f\\v]+(filter-branch|clean[ \\t\\n\\r\\f\\v]+-[a-z]*x)'
hd_re_keychain = '(^|[^A-Za-z0-9_.-])security[ \\t\\n\\r\\f\\v]+(dump-keychain|find-(generic|internet)-password|unlock-keychain)|keychain-access'
hd_re_keyfile = '(^|[ \\t\\n\\r\\f\\v/@<"])((id_rsa|id_ed25519|id_ecdsa)([ \\t\\n\\r\\f\\v/"]|$)|[!-~]*/(server|[A-Za-z0-9_.-]+)\\.(key|pem|p12|pfx)([ \\t\\n\\r\\f\\v/"]|$))'
hd_re_killhost = '(pkill|killall)[ \\t\\n\\r\\f\\v].*(wchat|bash|zsh|node|python|Terminal|iTerm|loginwindow|Finder|Dock)|kill[ \\t\\n\\r\\f\\v]+-(9|KILL)[ \\t\\n\\r\\f\\v]+-1|:\\(\\)[ \\t\\n\\r\\f\\v]*\\{'
hd_re_pipe_exec = '(curl|wget|fetch)[^;|]*\\|[ \\t\\n\\r\\f\\v]*(sudo[ \\t\\n\\r\\f\\v]+)?(sh|bash|zsh|dash|ksh|python[0-9.]*|perl|ruby|node)([^A-Za-z0-9_]|$)'
hd_re_power = '(^|[ \\t\\n\\r\\f\\v;&|])(shutdown|reboot|poweroff|halt)([ \\t\\n\\r\\f\\v]|$)|pmset[ \\t\\n\\r\\f\\v].*sleepnow|launchctl[ \\t\\n\\r\\f\\v]+(bootout|unload|kill)|systemsetup[ \\t\\n\\r\\f\\v]'
hd_re_priv = '(^|[^A-Za-z0-9_.-])(sudo|doas|pkexec|runuser|gksudo)([ \\t\\n\\r\\f\\v]|$)'
hd_re_proc_sub = '(sh|bash|zsh)[ \\t\\n\\r\\f\\v]*<\\(.*(curl|wget)'
hd_re_secret_dir = '(~|/Users/[^/ \\t\\n\\r\\f\\v]+|/home/[^/ \\t\\n\\r\\f\\v]+|\\$HOME)/\\.(ssh|aws|gnupg|subversion|docker|kube)([ \\t\\n\\r\\f\\v/]|$)|(~|/Users/[^/ \\t\\n\\r\\f\\v]+|/home/[^/ \\t\\n\\r\\f\\v]+|\\$HOME)/\\.config/(gh|gcloud|azure)([ \\t\\n\\r\\f\\v/]|$)|\\.npmrc|\\.netrc|_netrc|\\.pypirc|\\.htpasswd|\\.pgpass|spnego-machine|(~|/Users/[^/ \\t\\n\\r\\f\\v]+|/home/[^/ \\t\\n\\r\\f\\v]+|\\$HOME)/\\.ct/(config|history|mcporter\\.json)(\\.bak)?([ \\t\\n\\r\\f\\v/]|$)|(\\./|[^/A-Za-z0-9])\\.(ssh|aws|gnupg|subversion|docker|kube)/|(\\./|[^/A-Za-z0-9])\\.config/(gh|gcloud|azure)/|(\\./|[^/A-Za-z0-9])\\.ct/(config|history|mcporter\\.json)(\\.bak)?|(~|/Users/[^/ \\t\\n\\r\\f\\v]+|/home/[^/ \\t\\n\\r\\f\\v]+|\\$HOME)/\\.mcporter([ \\t\\n\\r\\f\\v/]|$)|(\\./|[^/A-Za-z0-9])\\.mcporter/|/\\.?mcporter/credentials\\.json([ \\t\\n\\r\\f\\v]|$)'
hd_re_secret_ref = '(\\$\\(cat[^)]*(\\.env|key|secret|token|password|passwd|credential|\\.netrc|\\.npmrc)|[<@][^ \\t\\n\\r\\f\\v]*(\\.env|secret|token|password|passwd|credential|\\.netrc|\\.npmrc|\\.pypirc|\\.htpasswd|\\.pgpass|\\.git-credentials|\\.p8|[!-~]*\\.(key|pem))|(^|[ \\t\\n\\r\\f\\v/~])\\.env([ \\t\\n\\r\\f\\v]|$)|(^|[ \\t\\n\\r\\f\\v/~.$])(\\.npmrc|\\.netrc|\\.pypirc|\\.git-credentials|id_rsa|id_ed25519|[!-~]*/[A-Za-z0-9_.-]*\\.(env|pem|p12|key))([ \\t\\n\\r\\f\\v]|$))'
hd_re_shadow = '/(etc|private/etc)/(passwd|shadow|master\\.password|sudoers|ntpwd)|\\.git-credentials'
hd_re_su = '(^|[ \\t\\n\\r\\f\\v;&|])su([ \\t\\n\\r\\f\\v]+-[ \\t\\n\\r\\f\\v]*[A-Za-z_][A-Za-z_]*|[ \\t\\n\\r\\f\\v]+[A-Za-z_][A-Za-z_]*|[ \\t\\n\\r\\f\\v]*$)'
hd_re_sys_redirect = '(>>?|(^|[ \\t\\n\\r\\f\\v])tee([ \\t\\n\\r\\f\\v]+-a)?[ \\t\\n\\r\\f\\v]+)[ \\t\\n\\r\\f\\v]*/(etc|usr|bin|sbin|System|Library|private/etc|private/usr|private/bin|private/sbin|private/System|private/Library|private/var/root|private/var/log|private/var/spool/cron|private/var/db|private/Applications|private/proc|private/sys|private/run|private/boot|private/lib|private/lib64|private/root|private/srv|private/media|private/mnt|var/root|var/log|var/spool/cron|var/db|Applications|proc|sys|run|boot|lib|lib64|root|srv|media|mnt)/'
hd_re_sys_redirect_wide = '(>>?|(^|[ \\t\\n\\r\\f\\v])tee([ \\t\\n\\r\\f\\v]+-a)?[ \\t\\n\\r\\f\\v]+)[ \\t\\n\\r\\f\\v]*/(etc|usr|bin|sbin|System|Library|private/etc|private/usr|private/bin|private/sbin|private/System|private/Library|private/var/root|private/var/log|private/var/spool/cron|private/var/db|private/Applications|private/proc|private/sys|private/run|private/boot|private/lib|private/lib64|private/root|private/srv|private/media|private/mnt|var/root|var/log|var/spool/cron|var/db|Applications|proc|sys|run|boot|lib|lib64|root|srv|media|mnt)/[!-~]*'
hd_re_upload = '(^|[^A-Za-z0-9_.-])(curl|wget|nc|ncat|scp|sftp|rsync|aws|gsutil)([ \\t\\n\\r\\f\\v]|$)'
hd_re_upload_arg = '(-T[ \\t\\n\\r\\f\\v]+|--upload-file|--data(-binary|-raw|-urlencode)?([ \\t\\n\\r\\f\\v]|=)|-d[ \\t\\n\\r\\f\\v]+|--form|-F[ \\t\\n\\r\\f\\v]+|--post-data|(-X|--request)[ \\t\\n\\r\\f\\v]+(POST|PUT))'
hd_re_wipe_flag = 'rm[ \\t\\n\\r\\f\\v].*--no-preserve-root'
hd_re_wipe_root = '(^|[^A-Za-z0-9_.-])rm[ \\t\\n\\r\\f\\v]+(-[a-zA-Z]+[ \\t\\n\\r\\f\\v]+)*[^;&|`$]*[ \\t\\n\\r\\f\\v]*(/([ \\t\\n\\r\\f\\v\'"]|/?\\*|$)|~/?$|~/|\\$HOME(/|$)|/Users|/home|/System|/Library|/private|/etc|/usr|/var|/bin|/sbin|/opt)'
hd_re_write_verb = '(^|[ \\t\\n\\r\\f\\v;&|])(rm|rmdir|mv|cp|install|rsync|tee|touch|mkdir|chmod|chown|chgrp|ln|dd|truncate|shred|patch|unzip|tar)([ \\t\\n\\r\\f\\v]|$)|sed[ \\t\\n\\r\\f\\v]+-i|perl[ \\t\\n\\r\\f\\v]+-[a-z]*i|git[ \\t\\n\\r\\f\\v]+(checkout|clean|reset|apply|stash|restore)([ \\t\\n\\r\\f\\v]|$)'


import re

_FLAGS = re.ASCII


def match(name, text):
    pat = globals().get(name)
    if pat is None:
        raise KeyError("未知规则: %r" % name)
    return re.search(pat, text, _FLAGS) is not None