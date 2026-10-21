"""团子龙聊天智能体 —— 包入口。

代码根目录常量集中在这里定义，其余模块统一 import：
    from agent import CODE_ROOT, DATA_DIR
"""

from __future__ import annotations

from pathlib import Path

#: 代码根目录（即包含 agent/、mcp_server/ 的这一层）
CODE_ROOT: Path = Path(__file__).resolve().parent.parent

#: 运行时数据目录
DATA_DIR: Path = CODE_ROOT / "data"

__version__ = "0.1.0"
__all__ = ["CODE_ROOT", "DATA_DIR", "__version__"]
