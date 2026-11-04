"""Configurable resource types and type-specific specification fields.

The catalog is configuration data rather than a hard-coded enum.  Resource
requests keep the stable type key and a rendered specification summary, while
the structured values are stored separately so changing a label never rewrites
historical requests.
"""
import copy
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import SystemSetting

RESOURCE_CATALOG_KEY = "resource_catalog"
KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,29}$")
FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FIELD_TYPES = {"text", "textarea", "number", "select"}


DEFAULT_RESOURCE_CATALOG = [
    {
        "key": "ECS", "name": "云服务器 ECS", "description": "通用计算实例，按 CPU、内存和磁盘申请。", "active": True,
        "fields": [
            {"key": "cpu", "label": "CPU", "type": "text", "required": True, "placeholder": "例如：2C"},
            {"key": "memory", "label": "内存", "type": "text", "required": True, "placeholder": "例如：4G"},
            {"key": "storage", "label": "存储", "type": "text", "required": False, "placeholder": "例如：100G SSD"},
            {"key": "quantity", "label": "数量", "type": "number", "required": True, "placeholder": "例如：2"},
        ],
    },
    {
        "key": "MySQL", "name": "MySQL 数据库", "description": "关系型数据库实例。", "active": True,
        "fields": [
            {"key": "version", "label": "版本", "type": "text", "required": True, "placeholder": "例如：8.0"},
            {"key": "storage", "label": "存储", "type": "text", "required": True, "placeholder": "例如：200G"},
            {"key": "instance", "label": "实例规格", "type": "text", "required": False, "placeholder": "例如：主从 / 高可用"},
        ],
    },
    {
        "key": "Redis", "name": "Redis 缓存", "description": "缓存与高性能键值存储。", "active": True,
        "fields": [
            {"key": "memory", "label": "内存", "type": "text", "required": True, "placeholder": "例如：4G"},
            {"key": "mode", "label": "部署模式", "type": "select", "required": False, "options": ["单节点", "主从", "集群"], "placeholder": "请选择部署模式"},
        ],
    },
    {"key": "MongoDB", "name": "MongoDB", "description": "文档型数据库。", "active": True, "fields": [{"key": "version", "label": "版本", "type": "text", "required": False, "placeholder": "例如：7.0"}]},
    {"key": "PostgreSQL", "name": "PostgreSQL", "description": "关系型数据库实例。", "active": True, "fields": [{"key": "version", "label": "版本", "type": "text", "required": False, "placeholder": "例如：16"}]},
    {"key": "K8s", "name": "Kubernetes 集群", "description": "容器编排集群资源。", "active": True, "fields": [{"key": "nodes", "label": "节点规格", "type": "text", "required": True, "placeholder": "例如：3 × 4C8G"}]},
    {"key": "OSS", "name": "对象存储 OSS", "description": "对象存储与归档空间。", "active": True, "fields": [{"key": "capacity", "label": "容量", "type": "text", "required": True, "placeholder": "例如：1TB"}]},
    {"key": "GPU", "name": "GPU 计算", "description": "图形或 AI 加速资源。", "active": True, "fields": [{"key": "model", "label": "型号", "type": "text", "required": True, "placeholder": "例如：NVIDIA A10"}, {"key": "quantity", "label": "数量", "type": "number", "required": True, "placeholder": "例如：1"}]},
    {"key": "APIKEY", "name": "API Key", "description": "第三方 API 密钥或调用额度。", "active": True, "fields": [{"key": "vendor", "label": "厂商", "type": "text", "required": True, "placeholder": "例如：阿里云 / OpenAI"}, {"key": "quota", "label": "额度说明", "type": "text", "required": False, "placeholder": "例如：每月 100 万次"}]},
]


def default_catalog() -> list[dict[str, Any]]:
    return copy.deepcopy(DEFAULT_RESOURCE_CATALOG)


def _string(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise HTTPException(422, f"{field}必须是文本")
    value = value.strip()
    if not value or len(value) > maximum:
        raise HTTPException(422, f"{field}不能为空且不超过 {maximum} 个字符")
    return value


def normalize_catalog(raw: Any, *, allow_inactive: bool = True) -> list[dict[str, Any]]:
    """Validate and canonicalize an administrator supplied catalog."""
    if isinstance(raw, dict):
        raw = raw.get("items")
    if not isinstance(raw, list) or not raw or len(raw) > 100:
        raise HTTPException(422, "至少配置一个资源类型，最多 100 个")
    result, keys = [], set()
    names = set()
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(422, "资源类型配置格式无效")
        key = _string(item.get("key"), "资源类型标识", 30)
        if not KEY_PATTERN.fullmatch(key) or key in keys:
            raise HTTPException(422, f"资源类型标识「{key}」无效或重复，只能使用字母、数字、下划线和短横线")
        keys.add(key)
        name = _string(item.get("name"), "资源类型名称", 60)
        if name.casefold() in names:
            raise HTTPException(422, f"资源类型名称「{name}」重复")
        names.add(name.casefold())
        description = str(item.get("description") or "").strip()
        if len(description) > 200:
            raise HTTPException(422, "资源类型说明不能超过 200 个字符")
        active = item.get("active", True)
        if not isinstance(active, bool):
            raise HTTPException(422, f"资源类型「{name}」的启用状态无效")
        if not allow_inactive and not active:
            continue
        fields, field_keys = [], set()
        raw_fields = item.get("fields", [])
        if not isinstance(raw_fields, list) or len(raw_fields) > 30:
            raise HTTPException(422, f"资源类型「{name}」最多配置 30 个规格字段")
        for field in raw_fields:
            if not isinstance(field, dict):
                raise HTTPException(422, f"资源类型「{name}」的规格字段格式无效")
            field_key = _string(field.get("key"), "规格字段标识", 40)
            if not FIELD_PATTERN.fullmatch(field_key) or field_key in field_keys:
                raise HTTPException(422, f"规格字段标识「{field_key}」无效或重复")
            field_keys.add(field_key)
            label = _string(field.get("label"), "规格字段名称", 60)
            field_type = field.get("type", "text")
            if field_type not in FIELD_TYPES:
                raise HTTPException(422, f"规格字段「{label}」类型不受支持")
            required = field.get("required", False)
            if not isinstance(required, bool):
                raise HTTPException(422, f"规格字段「{label}」的必填设置无效")
            placeholder = str(field.get("placeholder") or "").strip()
            if len(placeholder) > 160:
                raise HTTPException(422, f"规格字段「{label}」提示语不能超过 160 个字符")
            options = field.get("options", [])
            if not isinstance(options, list) or len(options) > 100 or any(not isinstance(option, str) or not option.strip() or len(option.strip()) > 100 for option in options):
                raise HTTPException(422, f"规格字段「{label}」的选项无效")
            options = list(dict.fromkeys(option.strip() for option in options))
            if field_type == "select" and not options:
                raise HTTPException(422, f"下拉字段「{label}」至少需要一个选项")
            fields.append({"key": field_key, "label": label, "type": field_type, "required": required, "placeholder": placeholder, "options": options})
        result.append({"key": key, "name": name, "description": description, "active": active, "fields": fields})
    if not any(item["active"] for item in result):
        raise HTTPException(422, "至少保留一个启用的资源类型")
    return result


def catalog(db: Session, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    row = db.get(SystemSetting, RESOURCE_CATALOG_KEY)
    if row is None:
        items = default_catalog()
    else:
        try:
            items = normalize_catalog(json.loads(row.value))
        except (TypeError, ValueError, json.JSONDecodeError):
            items = default_catalog()
    return items if include_inactive else [item for item in items if item["active"]]


def seed(db: Session):
    if db.get(SystemSetting, RESOURCE_CATALOG_KEY) is None:
        db.add(SystemSetting(key=RESOURCE_CATALOG_KEY, value=json.dumps(default_catalog(), ensure_ascii=False), is_secret=False))


def save(db: Session, raw: Any, actor):
    items = normalize_catalog(raw)
    row = db.get(SystemSetting, RESOURCE_CATALOG_KEY) or SystemSetting(key=RESOURCE_CATALOG_KEY)
    row.value = json.dumps(items, ensure_ascii=False)
    row.is_secret = False
    row.updated_by = actor.id
    db.add(row)
    from . import audit
    audit.record(db, actor, "configure", "settings", target_id="resource-catalog", target_name="资源类型与规格字段", extra={"resource_catalog": (None, "已更新")})
    db.commit()
    return items


def find(db: Session, key: str, *, active_only: bool = True) -> dict[str, Any] | None:
    return next((item for item in catalog(db, include_inactive=not active_only) if item["key"] == key and (item["active"] or not active_only)), None)


def validate_values(db: Session, resource_type: str, values: dict[str, Any], legacy_specification: str = "") -> tuple[str, dict[str, Any]]:
    """Validate submitted values and create a stable human-readable summary."""
    item = find(db, resource_type)
    if item is None:
        raise HTTPException(400, "资源类型不存在或已归档，请刷新后重新选择")
    if not isinstance(values, dict):
        raise HTTPException(422, "资源规格字段格式无效")
    fields = {field["key"]: field for field in item["fields"]}
    unknown = set(values) - set(fields)
    if unknown:
        raise HTTPException(422, f"资源规格包含未配置字段：{', '.join(sorted(unknown))}")
    # An empty structured payload with a non-empty specification is the legacy
    # contract used by existing integrations. It remains readable after a
    # catalog is introduced; newly submitted UI forms always send structured data.
    if not values and str(legacy_specification or "").strip():
        return str(legacy_specification).strip(), {}
    normalized = {}
    for key, field in fields.items():
        value = values.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if field["required"]:
                raise HTTPException(422, f"请填写{field['label']}")
            continue
        if isinstance(value, (dict, list)):
            raise HTTPException(422, f"{field['label']}只能填写单个值")
        if field["type"] == "number":
            try:
                value = float(value) if "." in str(value) else int(value)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{field['label']}必须是数字")
        else:
            value = str(value).strip()
        if field["type"] == "select" and value not in field["options"]:
            raise HTTPException(422, f"{field['label']}请选择有效选项")
        if len(str(value)) > 1000:
            raise HTTPException(422, f"{field['label']}不能超过 1000 个字符")
        normalized[key] = value
    if not normalized:
        if str(legacy_specification or "").strip():
            return str(legacy_specification).strip(), {}
        raise HTTPException(422, "请填写资源规格")
    summary = " · ".join(f"{fields[key]['label']}：{value}" for key, value in normalized.items())
    return summary, normalized


def specification_snapshot(db: Session, resource_type: str, values: dict[str, Any]) -> dict[str, Any]:
    item = find(db, resource_type, active_only=False)
    configured = {field["key"]: field for field in item["fields"]} if item else {}
    fields = []
    if isinstance(values, dict):
        for key, value in values.items():
            field = configured.get(key)
            fields.append({"key": key, "label": field["label"] if field else key, "value": value})
    return {"type_name": item["name"] if item else resource_type, "fields": fields}
