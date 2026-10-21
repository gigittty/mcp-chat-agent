"""团子龙聊天智能体 —— 包入口。

代码根目录常量集中在这里定义，其余模块统一 import：
    from agent import CODE_ROOT, DATA_DIR
"""

from __future__ import annotations

import os
from pathlib import Path

#: 代码根目录（即包含 agent/、mcp_server/ 的这一层）
CODE_ROOT: Path = Path(__file__).resolve().parent.parent

#: 运行时数据目录（可用环境变量 AGENT_DATA_DIR 覆盖，测试与多实例部署都用得上）
DATA_DIR: Path = CODE_ROOT / "data"


def data_dir() -> Path:
    """返回当前生效的数据目录。

    优先级：环境变量 AGENT_DATA_DIR > 默认的 data/。
    每次调用都重新读取，因此测试里 monkeypatch.setenv 即可生效。
    """
    override = os.environ.get("AGENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DATA_DIR


__version__ = "0.1.0"
__all__ = ["CODE_ROOT", "DATA_DIR", "data_dir", "__version__"]
