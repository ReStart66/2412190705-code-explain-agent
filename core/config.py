"""极简配置加载（零第三方依赖）。

自己解析 .env 文件，避免引入 python-dotenv。支持：

    KEY=VALUE
    # 注释行
    KEY="带引号的值"

已存在的系统环境变量不会被覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """读取 .env 并写入 os.environ，返回本次读到的键值对。"""
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        return {}

    loaded: dict[str, str] = {}
    text = env_path.read_text(encoding="utf-8", errors="replace")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 去掉成对的引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)

    return loaded
