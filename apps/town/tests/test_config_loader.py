"""config_loader 单元测试 — 阶段 3 T1 (REQ-7cfc9696 AC 1-4)

覆盖 7 个场景:
  1. happy path:合法 personas.yaml + locations.yaml → 无 errors
  2. 缺必填字段:persona 缺 start_location → 1 error
  3. id 重复:2 个 persona 同 id → 1 error
  4. start_location 不存在:引用 locations.yaml 之外的地点 → 1 error
  5. YAML 语法错:bad: [unclosed → 1 error
  6. locations 邻接引用不存在:adjacency 含未定义的 location 名 → 1 error
  7. 空目录:config_dir 不存在 → 1 error

约定:
- 用 pytest tmp_path 写 YAML,不依赖真实文件
- errors 元素形如 {"file", "line" (可空), "message", "severity"}
- 配置 schema:personas.yaml 顶层 agents 列表,locations.yaml 顶层 locations 列表
"""
from pathlib import Path

from town.config_loader import load_config


# ---------- 测试 fixture:YAML 模板 ----------

VALID_PERSONAS = """\
agents:
  - id: "lisi"
    name: "李四"
    persona: "32 岁程序员,内向但善良"
    start_location: "李四家"
    color: "#FF6B6B"
  - id: "wangwu"
    name: "王五"
    persona: "30 岁产品经理,外向话多"
    start_location: "王五家"
"""

VALID_LOCATIONS = """\
locations:
  - name: "李四家"
    x: 100
    y: 100
    color: "#FFD700"
    adjacency:
      - "客厅"
  - name: "王五家"
    x: 300
    y: 100
    color: "#87CEEB"
    adjacency:
      - "客厅"
  - name: "客厅"
    x: 200
    y: 200
    color: "#98FB98"
    adjacency:
      - "李四家"
      - "王五家"
"""


def _write(tmp_path: Path, personas_yaml: str, locations_yaml: str) -> Path:
    """在 tmp_path/config/ 下写两个 YAML,返回 config_dir。"""
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "personas.yaml").write_text(personas_yaml, encoding="utf-8")
    (cfg / "locations.yaml").write_text(locations_yaml, encoding="utf-8")
    return cfg


# ---------- 1. happy path ----------

def test_load_config_happy_path(tmp_path):
    """合法 personas.yaml + locations.yaml → errors 空,字段对得上"""
    cfg = _write(tmp_path, VALID_PERSONAS, VALID_LOCATIONS)

    personas, locations, errors = load_config(cfg)

    assert errors == [], f"unexpected errors: {errors}"
    assert len(personas) == 2
    assert personas[0]["id"] == "lisi"
    assert personas[0]["start_location"] == "李四家"
    assert personas[0]["color"] == "#FF6B6B"
    assert len(locations) == 3
    assert locations[0]["name"] == "李四家"
    assert locations[0]["x"] == 100
    assert locations[0]["adjacency"] == ["客厅"]


# ---------- 2. 缺必填字段 ----------

def test_load_config_missing_start_location(tmp_path):
    """persona 缺 start_location → 1 error"""
    bad_personas = """\
agents:
  - id: "lisi"
    name: "李四"
    persona: "32 岁程序员"
"""
    cfg = _write(tmp_path, bad_personas, VALID_LOCATIONS)

    personas, locations, errors = load_config(cfg)

    assert len(errors) == 1
    assert errors[0]["file"] == "personas.yaml"
    assert "start_location" in errors[0]["message"] or "lisi" in errors[0]["message"]
    assert errors[0]["severity"] == "error"
    # 缺必填字段时,personas 列表应仍返回以便调用方排错
    assert isinstance(personas, list)


# ---------- 3. id 重复 ----------

def test_load_config_duplicate_persona_id(tmp_path):
    """2 个 persona 同 id → 1 error"""
    bad_personas = """\
agents:
  - id: "lisi"
    name: "李四"
    persona: "32 岁程序员"
    start_location: "李四家"
  - id: "lisi"
    name: "李四(副本)"
    persona: "重复 id"
    start_location: "客厅"
"""
    cfg = _write(tmp_path, bad_personas, VALID_LOCATIONS)

    personas, locations, errors = load_config(cfg)

    assert len(errors) == 1
    assert errors[0]["file"] == "personas.yaml"
    assert "lisi" in errors[0]["message"]
    assert "重复" in errors[0]["message"] or "重复" in errors[0]["message"] or "duplicate" in errors[0]["message"].lower()


# ---------- 4. start_location 不存在 ----------

def test_load_config_start_location_not_in_locations(tmp_path):
    """persona 的 start_location 不在 locations.yaml 中 → 1 error"""
    bad_personas = """\
agents:
  - id: "lisi"
    name: "李四"
    persona: "程序员"
    start_location: "外太空"   # locations.yaml 没有
"""
    cfg = _write(tmp_path, bad_personas, VALID_LOCATIONS)

    personas, locations, errors = load_config(cfg)

    assert len(errors) == 1
    assert errors[0]["file"] == "personas.yaml"
    assert "外太空" in errors[0]["message"] or "start_location" in errors[0]["message"]
    assert errors[0]["severity"] == "error"


# ---------- 5. YAML 语法错 ----------

def test_load_config_yaml_syntax_error(tmp_path):
    """故意写 bad: [unclosed → 1 error(yaml.YAMLError 被捕获)"""
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "personas.yaml").write_text("bad: [unclosed\n", encoding="utf-8")
    (cfg / "locations.yaml").write_text(VALID_LOCATIONS, encoding="utf-8")

    personas, locations, errors = load_config(cfg)

    assert len(errors) == 1
    assert errors[0]["file"] == "personas.yaml"
    assert "personas.yaml" in errors[0]["message"] or "解析" in errors[0]["message"] or "YAML" in errors[0]["message"]
    assert errors[0]["severity"] == "error"


# ---------- 6. locations 邻接引用不存在 ----------

def test_load_config_adjacency_references_unknown_location(tmp_path):
    """locations.yaml 自己的 adjacency 包含未定义的 location 名 → 1 error

    配 personas 也只用合法 location,确保只有 adjacency 引用错这一条 error。
    """
    bad_locations = """\
locations:
  - name: "李四家"
    x: 100
    y: 100
    color: "#FFD700"
    adjacency:
      - "百慕大三角洲"   # 自己在 locations 里没声明
  - name: "王五家"
    x: 300
    y: 100
    color: "#87CEEB"
    adjacency:
      - "李四家"
"""
    cfg = _write(tmp_path, VALID_PERSONAS, bad_locations)

    personas, locations, errors = load_config(cfg)

    assert len(errors) == 1
    assert errors[0]["file"] == "locations.yaml"
    assert "百慕大三角洲" in errors[0]["message"]
    assert errors[0]["severity"] == "error"


# ---------- 7. 空目录 ----------

def test_load_config_dir_not_exist(tmp_path):
    """config_dir 不存在 → 至少 1 error(不是抛异常)"""
    missing = tmp_path / "no_such_dir"

    # 必须不抛异常,而是聚合到 errors
    personas, locations, errors = load_config(missing)

    assert len(errors) >= 1
    # 至少一条 error 提到 personas.yaml 或 locations.yaml
    msg = " ".join(e["message"] for e in errors)
    assert "personas.yaml" in msg or "locations.yaml" in msg or "不存在" in msg or "目录" in msg
    assert all(e["severity"] == "error" for e in errors)
    assert personas == []
    assert locations == []