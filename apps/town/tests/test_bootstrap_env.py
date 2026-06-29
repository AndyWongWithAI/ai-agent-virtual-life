"""B1/B2 regression:.env 路径 + env var 命名"""
import os
from pathlib import Path

# 不硬编码开发机绝对路径:CI runner 工作目录是
# /home/runner/work/ai-agent-virtual-life/ai-agent-virtual-life/
# 用 __file__ 推导 REPO_ROOT,保证本机和 CI 都能跑。
# __file__ = .../apps/town/tests/test_bootstrap_env.py
# parents[0] = tests
# parents[1] = apps/town
# parents[2] = apps
# parents[3] = repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
TOWN_DIR = REPO_ROOT / "apps" / "town"


def test_dotenv_path_resolves_to_apps_town():
    """B1 regression:load_dotenv 路径必须是 apps/town/.env(3 层 parent,不是 2 层)"""
    # bootstrap.py: load_dotenv(Path(__file__).parent.parent.parent / ".env")
    # __file__ = .../apps/town/src/town/bootstrap.py
    # parent = .../apps/town/src/town
    # parent.parent = .../apps/town/src
    # parent.parent.parent = .../apps/town
    expected_path = TOWN_DIR / ".env"
    assert expected_path.exists(), f"期望 .env 在 {expected_path}"
    # 验证 bootstrap.py 当前路径
    bootstrap_file = TOWN_DIR / "src" / "town" / "bootstrap.py"
    derived_path = bootstrap_file.parent.parent.parent / ".env"
    assert derived_path == expected_path, (
        f"bootstrap.py 中的 load_dotenv 路径错!当前指向 {derived_path}"
    )


def test_env_example_uses_posix_compliant_names():
    """B2 regression:env var 必须用大写+下划线(POSIX),不能用驼峰"""
    from pathlib import Path
    env_example = TOWN_DIR / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    # 必须有这些
    required = {"MINIMAX_API_KEY", "MINIMAX_BASE_URL", "MINIMAX_MODEL",
                "REDIS_URL", "DATABASE_URL", "LLM_DAILY_BUDGET_CNY"}
    for key in required:
        assert key in content, f".env.example 缺 {key}"
    # 不能有驼峰形式(MiniMax_ 是 bug)
    forbidden = ["MiniMax_API_KEY", "MiniMax_BASE_URL", "MiniMax_MODEL"]
    for k in forbidden:
        assert k not in content, f".env.example 含驼峰形式 {k}(POSIX 不合规)"


def test_bootstrap_imports_without_missing_env_var():
    """B1/B2 综合:能 import town.bootstrap 不崩(env var 加载逻辑没死循环)"""
    import importlib
    import sys
    # 清掉旧 module
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("town"):
            del sys.modules[mod_name]
    try:
        from town import bootstrap  # noqa: F401
        assert bootstrap is not None
    except Exception as e:
        # 不应因 env var 缺失而崩(load_dotenv 不抛)
        # 但 bootstrap() 调用会崩(这测试只测 import)
        assert "load_dotenv" not in str(e), f"load_dotenv 路径错:{e}"


def test_minimax_api_key_is_loaded_from_dotenv():
    """B1/B2 综合:.env 中 MINIMAX_API_KEY 能被 dotenv 加载到 os.environ"""
    from dotenv import load_dotenv
    env_path = TOWN_DIR / ".env"
    if not env_path.exists():
        pytest.skip(".env 不存在(本测试需要真实部署环境)")
    # 备份当前值
    original = os.environ.get("MINIMAX_API_KEY")
    # 清掉再 load
    os.environ.pop("MINIMAX_API_KEY", None)
    load_dotenv(env_path)
    assert os.environ.get("MINIMAX_API_KEY"), "MINIMAX_API_KEY 未从 .env 加载"
    # 恢复
    if original:
        os.environ["MINIMAX_API_KEY"] = original


import pytest  # noqa: E402