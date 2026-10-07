"""设计阶段按缺口补工具：映射到已有工具，否则写成技能包并即时注册。

执行计划只允许已注册 tool_id。合成失败不阻断设计书，对应项保持 unavailable。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from logging_config import get_logger
from models import DesignDocument, ToolRequirement
from skills_loader import SkillCapabilityAdapter, SkillManager, _SANITIZE

logger = get_logger(__name__)

_ALIASES = {
    "file-creator": "file_creator",
    "filecreator": "file_creator",
    "text-editor": "text_editor",
    "code-generator": "coding_agent",
    "code_generator": "coding_agent",
    "codegen": "coding_agent",
    "coding-agent": "coding_agent",
    "search": "super_search",
    "web-search": "super_search",
    "web_search": "super_search",
    "browser": "browser_check",
    "linter": "lint_gate",
    "lint": "lint_gate",
    "test": "test_runner",
    "tests": "test_runner",
}

# 计划侧默认不靠「禁用大半内置工具」做安全；授权/副作用/审批走 Grant（见 AGENT_HARNESS_DESIGN）。
# 仅保留明确不应作为计划步骤 executor 的 id（当前为空，留扩展点）。
PLAN_DENYLIST: Set[str] = set()
PLAN_CORE = {
    "file_creator", "coding_agent", "test_runner", "lint_gate", "browser_check",
}


def allowed_plan_tools(known_ids: Iterable[str], extra_ids: Iterable[str] = ()) -> Set[str]:
    """计划步骤允许的 tool_id：当前注册表 ∪ 计划核心 ∪ skill 扩展，减去 PLAN_DENYLIST。

    与主设计稿一致：search/git/run 等确定性工具应能进图；安全由 Runtime Grant 约束。
    """
    known = {str(item) for item in known_ids if str(item).strip()}
    extra = {str(item) for item in extra_ids if str(item).strip()}
    return (known | set(PLAN_CORE) | extra) - set(PLAN_DENYLIST)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def sanitize_stem(name: str) -> str:
    raw = str(name or "").strip()
    for prefix in ("skill_gen_", "skill_", "gen_"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
    stem = _SANITIZE.sub("_", raw).strip("_-").lower() or "custom"
    return stem[:40]


def canonical_tool_id(name: str, known: Set[str]) -> Optional[str]:
    """把设计书里的名字对上已注册工具。对不上返回 None。"""
    raw = str(name or "").strip()
    if not raw:
        return None
    if raw in known:
        return raw
    lower = raw.lower().replace(" ", "_")
    if lower in known:
        return lower
    dashed = lower.replace("_", "-")
    mapped = _ALIASES.get(lower) or _ALIASES.get(dashed)
    if mapped and mapped in known:
        return mapped
    stem = sanitize_stem(raw)
    for candidate in (stem, f"skill_{stem}", f"skill_gen_{stem}"):
        if candidate in known:
            return candidate
    return None


def recompute_completeness(design: DesignDocument) -> None:
    tools = design.part2_tool_assessment.required_tools
    if not tools:
        design.part2_tool_assessment.overall_tool_completeness = 1.0
        return
    ok = sum(1 for item in tools if (item.availability or "").lower() == "available")
    design.part2_tool_assessment.overall_tool_completeness = round(ok / len(tools), 4)


def _parse_json_object(text: str) -> Dict[str, Any]:
    blob = (text or "").strip()
    if blob.startswith("```"):
        fenced = _FENCE.fullmatch(blob)
        if fenced:
            blob = fenced.group(1).strip()
    start, end = blob.find("{"), blob.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("合成响应不是 JSON 对象")
    data = json.loads(blob[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("合成响应不是对象")
    return data


def _write_skill_files(skill_dir, name: str, description: str, script: str, side_effect: str) -> None:
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "run.py").write_text(script.rstrip() + "\n", encoding="utf-8")
    front = (
        "---\n"
        f"name: {json.dumps(name, ensure_ascii=False)}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "version: 1.0.0\n"
        "entry: scripts/run.py\n"
        "runtime: python\n"
        f"side_effect: {side_effect}\n"
        "timeout: 120\n"
        "---\n\n"
        "由设计阶段按缺口自动生成。stdin 收 JSON，stdout 出 JSON。\n"
    )
    (skill_dir / "SKILL.md").write_text(front, encoding="utf-8")


def register_skill_package(
    manager: SkillManager,
    tool_registry: Any,
    capability_registry: Any,
    package,
    *,
    database: Any = None,
    design_id: Any = None,
    source: str = "synthesized",
) -> str:
    from skills_loader import _make_skill_tool_class
    from paths import REPO_ROOT

    tool_id = package.tool_id
    if tool_id not in getattr(tool_registry, "tools", {}):
        tool_registry.register_tool(tool_id, _make_skill_tool_class(package))
    if manager.by_tool_id(tool_id) is None:
        manager.skills.append(package)
    if capability_registry is not None and capability_registry.get(tool_id) is None:
        capability_registry.register(
            SkillCapabilityAdapter(tool_registry, tool_id, package)
        )
    if database is not None and hasattr(database, "upsert_tool_catalog"):
        skill_dir = getattr(package, "dir", None)
        rel = None
        if skill_dir is not None:
            path = Path(skill_dir)
            try:
                rel = str(path.resolve().relative_to(REPO_ROOT.resolve()))
            except Exception:
                rel = str(path)
        try:
            database.upsert_tool_catalog(
                tool_id,
                kind="skill",
                name=getattr(package, "name", None),
                description=getattr(package, "description", None),
                skill_dir=rel,
                status="ready",
                source=source,
                design_id=str(design_id) if design_id else None,
            )
        except Exception as exc:
            logger.warning("工具目录落库失败 {}: {}", tool_id, exc)
    return tool_id


async def synthesize_skill(
    *,
    requested_id: str,
    purpose: str,
    user_request: str,
    llm_client: Any,
    model_config: Dict[str, Any],
    skill_manager: SkillManager,
) -> Optional[str]:
    """用设计模型写一个技能包。成功返回注册用的 tool_id。"""
    stem = sanitize_stem(requested_id)
    dir_name = f"gen_{stem}"
    skill_dir = skill_manager.skills_dir / dir_name
    existing = skill_manager.by_tool_id(f"skill_{dir_name}")
    if existing is not None:
        return existing.tool_id
    if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
        package = skill_manager._load_one(skill_dir)
        skill_manager.skills.append(package)
        return package.tool_id

    primary = (model_config or {}).get("primary") or {}
    provider = primary.get("provider")
    model = primary.get("model")
    if not provider or not model or llm_client is None:
        return None

    from llm_client import Message

    prompt = f"""为 EAS 技能包写一个最小可执行工具。只返回 JSON。

用户需求：{user_request}
缺口工具 id：{requested_id}
用途：{purpose or requested_id}

约束：
- Python 3，只用标准库
- 禁止 subprocess / os.system / socket / 网络请求
- 从 stdin 读 JSON：{{"instruction": str, "inputs": object}}
- 向 stdout 打印 JSON：必须含 success（布尔）；失败时含 error
- 文件写在 inputs.path 或当前工作目录下的相对路径
- side_effect 只能是 none 或 reversible

JSON 形状：
{{"name":"短名","description":"一句话","side_effect":"reversible","script":"完整 run.py 源码"}}
"""
    response = await llm_client.chat(
        provider_name=provider,
        messages=[
            Message(role="system", content="你只输出一个 JSON 对象，不要 markdown。"),
            Message(role="user", content=prompt),
        ],
        model=model,
        temperature=0.1,
        max_tokens=int(primary.get("max_tokens") or 2500),
    )
    data = _parse_json_object(getattr(response, "content", "") or "")
    script = str(data.get("script") or "").strip()
    if not script or "success" not in script:
        raise ValueError("合成脚本无效")
    banned = ("subprocess", "os.system", "socket.", "requests.", "httpx.", "urllib.request")
    if any(token in script for token in banned):
        raise ValueError("合成脚本含禁止调用")
    side = str(data.get("side_effect") or "reversible").strip().lower()
    if side not in {"none", "reversible"}:
        side = "reversible"
    name = str(data.get("name") or requested_id).strip() or requested_id
    description = str(data.get("description") or purpose or name).strip()
    _write_skill_files(skill_dir, name, description, script, side)
    package = skill_manager._load_one(skill_dir)
    skill_manager.skills.append(package)
    logger.info("已生成技能包 {} → {}", requested_id, package.tool_id)
    return package.tool_id


async def fulfill_design_tools(
    design: DesignDocument,
    *,
    known_ids: Iterable[str],
    llm_client: Any = None,
    model_config: Optional[Dict[str, Any]] = None,
    skill_manager: Any = None,
    tool_registry: Any = None,
    capability_registry: Any = None,
    database: Any = None,
) -> DesignDocument:
    """对照注册表改写 required_tools；缺口在可写技能目录时现场补上。

    available = 计划允许集（注册表 ∪ PLAN_CORE ∪ skill_*）内能对上的名字；
    未注册且非核心的不得假绿。合成只补「目录里真没有」的能力。
    """
    registered: Set[str] = {str(item) for item in known_ids if str(item).strip()}
    skill_extras: List[str] = []
    if skill_manager is not None:
        for package in getattr(skill_manager, "skills", None) or []:
            tid = getattr(package, "tool_id", None)
            if tid:
                skill_extras.append(str(tid))
    match_universe: Set[str] = allowed_plan_tools(registered, skill_extras)
    # 目录标 missing 的技能不能当 available（盘丢了或加载失败）
    if database is not None and hasattr(database, "list_tool_catalog"):
        try:
            for row in database.list_tool_catalog(status="missing"):
                tid = str(row.get("tool_id") or "").strip()
                if tid:
                    match_universe.discard(tid)
        except Exception as exc:
            logger.warning("读取 tool_catalog missing 失败: {}", exc)
    can_write = (
        skill_manager is not None
        and getattr(skill_manager, "skills_dir", None) is not None
        and getattr(skill_manager.skills_dir, "name", "") != "__skills_disabled__"
    )
    updated: List[ToolRequirement] = []
    dirty = False
    for req in list(design.part2_tool_assessment.required_tools):
        matched = canonical_tool_id(req.tool_id, match_universe)
        if matched:
            if req.tool_id != matched or (req.availability or "").lower() != "available":
                dirty = True
            req.tool_id = matched
            req.availability = "available"
            updated.append(req)
            continue
        new_id = None
        if can_write:
            try:
                new_id = await synthesize_skill(
                    requested_id=req.tool_id,
                    purpose=req.impact or req.limitations or req.tool_id,
                    user_request=design.user_request,
                    llm_client=llm_client,
                    model_config=model_config or {},
                    skill_manager=skill_manager,
                )
                if new_id and tool_registry is not None:
                    package = skill_manager.by_tool_id(new_id)
                    if package is not None:
                        register_skill_package(
                            skill_manager,
                            tool_registry,
                            capability_registry,
                            package,
                            database=database,
                            design_id=getattr(design, "design_id", None),
                            source="synthesized",
                        )
                        match_universe.add(new_id)
            except Exception as exc:
                logger.warning("合成工具 {} 失败: {}", req.tool_id, exc)
                new_id = None
        if new_id:
            req.tool_id = new_id
            req.availability = "available"
            req.quality = req.quality or "medium"
            extra = "（设计阶段生成的技能包）"
            req.limitations = ((req.limitations or "") + extra).strip()
            dirty = True
        elif can_write or (req.availability or "").lower() == "available":
            req.availability = "unavailable"
            dirty = True
        updated.append(req)
    design.part2_tool_assessment.required_tools = updated
    if dirty:
        recompute_completeness(design)
    return design
