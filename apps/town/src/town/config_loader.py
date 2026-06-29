"""config_loader:town YAML 配置加载器

任务 T1 (REQ-7cfc9696 AC 1-4):
- 纯函数 load_config(config_dir) -> (personas, locations, errors)
- 错误聚合,不 raise;调用方决定回退还是硬失败
- errors 元素形如:
    {"file": "personas.yaml", "line": int | None, "message": str, "severity": "error"|"warning"}

校验规则(对应设计 §4.3):
  personas.yaml:
    - 顶层 agents 字段必须存在且为非空 list
    - 每条 agent 必填 id / name / persona / start_location (color 可选)
    - id 全局唯一
    - start_location 必须 ∈ locations[].name
  locations.yaml:
    - 顶层 locations 字段必须存在且为非空 list
    - 每条 location 必填 name / x / y / color / adjacency (adjacency 可为空 [])
    - adjacency 元素必须 ∈ locations[].name

架构定位:L1 原子组件,可复用资产(config-driven-runtime)。
"""
from pathlib import Path
from typing import Any

import yaml


# 必填字段常量
_PERSONA_REQUIRED = ("id", "name", "persona", "start_location")
_LOCATION_REQUIRED = ("name", "x", "y", "color", "adjacency")


def _err(file: str, message: str, line: int | None = None) -> dict[str, Any]:
    """构造一条错误条目(调用方模板)"""
    return {"file": file, "line": line, "message": message, "severity": "error"}


def _load_yaml(path: Path, file_label: str, errors: list[dict]) -> Any:
    """读 + 解析 YAML,失败时把错误塞进 errors 并返回 None。

    不存在的文件也走这里(目录缺失场景由 caller 决定聚合位置)。
    """
    if not path.exists():
        errors.append(_err(file_label, f"{file_label} 不存在: {path}"))
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        errors.append(_err(file_label, f"{file_label} 读取失败: {e}"))
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        # PyYAML 1.x: e.problem_mark.line (0-based)
        line = None
        mark = getattr(e, "problem_mark", None)
        if mark is not None:
            line = mark.line + 1
        errors.append(_err(file_label, f"{file_label} YAML 解析失败: {e}", line=line))
        return None


def _validate_personas(
    raw: Any, file_label: str, errors: list[dict]
) -> list[dict]:
    """把 raw dict 转成 personas list,记录缺字段 / id 重复等错误。"""
    if raw is None:
        return []
    if not isinstance(raw, dict) or "agents" not in raw:
        errors.append(_err(file_label, f"{file_label} 缺少顶层 agents 字段"))
        return []
    agents = raw["agents"]
    if not isinstance(agents, list) or not agents:
        errors.append(_err(file_label, f"{file_label} agents 必须为非空列表"))
        return []

    personas: list[dict] = []
    seen_ids: set[str] = set()
    dup_ids: set[str] = set()

    for idx, p in enumerate(agents, start=1):
        if not isinstance(p, dict):
            errors.append(_err(file_label, f"第 {idx} 条 agent 不是对象"))
            continue
        missing = [k for k in _PERSONA_REQUIRED if k not in p]
        if missing:
            errors.append(
                _err(
                    file_label,
                    f"第 {idx} 条 agent 缺字段: {', '.join(missing)}",
                )
            )
            # 仍加入 personas 让调用方拿到部分数据用于排错
        pid = p.get("id")
        if pid is not None:
            if pid in seen_ids:
                dup_ids.add(pid)
            else:
                seen_ids.add(pid)
        personas.append(p)

    if dup_ids:
        errors.append(
            _err(
                file_label,
                f"persona id 重复: {sorted(dup_ids)}",
            )
        )
    return personas


def _validate_locations(
    raw: Any, file_label: str, errors: list[dict]
) -> tuple[list[dict], set[str]]:
    """把 raw dict 转成 locations list + name 集合;记录缺字段错误。"""
    if raw is None:
        return [], set()
    if not isinstance(raw, dict) or "locations" not in raw:
        errors.append(_err(file_label, f"{file_label} 缺少顶层 locations 字段"))
        return [], set()
    locs_raw = raw["locations"]
    if not isinstance(locs_raw, list) or not locs_raw:
        errors.append(_err(file_label, f"{file_label} locations 必须为非空列表"))
        return [], set()

    locations: list[dict] = []
    seen_names: set[str] = set()
    for idx, loc in enumerate(locs_raw, start=1):
        if not isinstance(loc, dict):
            errors.append(_err(file_label, f"第 {idx} 条 location 不是对象"))
            continue
        missing = [k for k in _LOCATION_REQUIRED if k not in loc]
        if missing:
            errors.append(
                _err(
                    file_label,
                    f"第 {idx} 条 location 缺字段: {', '.join(missing)}",
                )
            )
        # adjacency 必须是 list
        if "adjacency" in loc and not isinstance(loc["adjacency"], list):
            errors.append(
                _err(
                    file_label,
                    f"第 {idx} 条 location adjacency 必须是列表",
                )
            )
        name = loc.get("name")
        if isinstance(name, str):
            seen_names.add(name)
        locations.append(loc)
    return locations, seen_names


def _cross_validate(
    personas: list[dict],
    locations: list[dict],
    loc_names: set[str],
    errors: list[dict],
) -> None:
    """跨文件校验:
    - persona.start_location 必须 ∈ loc_names
    - location.adjacency 元素必须 ∈ loc_names
    """
    # 1. persona.start_location
    for p in personas:
        sl = p.get("start_location")
        pid = p.get("id", "<unknown>")
        if isinstance(sl, str) and sl not in loc_names:
            errors.append(
                _err(
                    "personas.yaml",
                    f"persona {pid} 的 start_location '{sl}' 不在 locations.yaml 中",
                )
            )

    # 2. adjacency 引用
    for loc in locations:
        adj = loc.get("adjacency")
        lname = loc.get("name", "<unknown>")
        if not isinstance(adj, list):
            continue
        for target in adj:
            if isinstance(target, str) and target not in loc_names:
                errors.append(
                    _err(
                        "locations.yaml",
                        f"location '{lname}' 的 adjacency 引用了不存在的地点 '{target}'",
                    )
                )


def load_config(config_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """加载 town YAML 配置,聚合错误,返回 (personas, locations, errors)。

    设计 §4.3:
        errors 空 → 完全 OK
        errors 非空 → 调用方决定回退 base 或硬失败

    Args:
        config_dir:包含 personas.yaml 和 locations.yaml 的目录路径

    Returns:
        (personas, locations, errors)
        - personas: list[dict],每条对应 YAML 中一个 agent(缺字段也保留以便排错)
        - locations: list[dict],每条对应 YAML 中一个 location
        - errors: list[dict],每条形如
            {"file": str, "line": int|None, "message": str, "severity": "error"}
    """
    errors: list[dict] = []
    personas_path = Path(config_dir) / "personas.yaml"
    locations_path = Path(config_dir) / "locations.yaml"

    # 目录本身缺失 → 两条 error(每个文件一条),调用方仍可走 base 兜底
    if not Path(config_dir).exists():
        errors.append(
            _err(
                "personas.yaml",
                f"配置目录不存在: {config_dir}",
            )
        )
        errors.append(
            _err(
                "locations.yaml",
                f"配置目录不存在: {config_dir}",
            )
        )
        return [], [], errors

    p_raw = _load_yaml(personas_path, "personas.yaml", errors)
    l_raw = _load_yaml(locations_path, "locations.yaml", errors)

    personas = _validate_personas(p_raw, "personas.yaml", errors)
    locations, loc_names = _validate_locations(l_raw, "locations.yaml", errors)

    _cross_validate(personas, locations, loc_names, errors)

    return personas, locations, errors