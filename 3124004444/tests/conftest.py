"""pytest 公共配置。

关键点：每个测试用独立的临时数据目录（环境变量 AGENT_DATA_DIR），
避免测试互相污染，也不会把产物写进仓库。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CODE_ROOT = Path(__file__).resolve().parent.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向 tmp_path，并建好 files/out/state 子目录。"""
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    from agent import sandbox

    sandbox.ensure_dirs()
    return tmp_path


@pytest.fixture()
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    """返回一个可自由加载指定工具的独立注册表（不影响全局单例）。"""
    from agent.registry import ToolRegistry

    registry = ToolRegistry()
    return registry
