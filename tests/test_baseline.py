"""Phase 0 baseline:套件可 import、子模組存在。後續 chunk 補實作測試。"""

import importlib

import waste_for_agents


def test_version() -> None:
    assert waste_for_agents.__version__ == "0.1.0"


def test_submodules_importable() -> None:
    for name in (
        "waste_for_agents.store",
        "waste_for_agents.diff",
        "waste_for_agents.scheduler",
        "waste_for_agents.server",
        "waste_for_agents.sources.base",
        "waste_for_agents.sources.twinkle",
    ):
        importlib.import_module(name)
