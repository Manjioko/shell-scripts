"""版本号唯一出处。

发布脚本(packaging/release.py)只改这一行;`wchat --version` 与 Formula 的
version 都从它读,避免"包是 0.3.0、Formula 还写 0.2.0"这种对不上。
"""
__version__ = "0.2.6"

