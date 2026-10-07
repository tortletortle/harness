"""技能包加载器（生态层）：skills/ 目录 → ToolRegistry 工具 + 知识注入。

技能包格式（docs/SKILLS.md）：

    skills/<skill-id>/
    ├── SKILL.md            # YAML frontmatter（name/description/entry/...）+ 使用说明正文
    └── scripts/...         # entry 指向的脚本（stdin 收 JSON，stdout 出 JSON）

- 有 entry 的技能注册为普通工具（tool_id = skill_<目录名>）：
  计划步骤可执行、Designer 工具评估可见、对话助手可调用（统一走 Grant 授权）。
- 无 entry 的技能是知识包：只进入技能清单与对话助手系统提示词，提供做法指导。
- 执行协议：脚本从 stdin 收 {"instruction", "inputs"}，向 stdout 输出 JSON（含 success）。
- 单技能加载失败只记日志跳过，绝不阻断其他技能与服务启动。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml

from logging_config import get_logger
from capabilities import ToolCapabilityAdapter

logger = get_logger(__name__)

_SANITIZE = re.compile(r"[^a-zA-Z0-9_\-]")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_VALID_SIDE_EFFECTS = {"none", "reversible", "external"}

# 技能脚本支持的三种运行时；python 用当前解释器，保证依赖一致。
_RUNTIME_COMMANDS = {
    "python": lambda script: [sys.executable, str(script)],
    "node": lambda script: ["node", str(script)],
    "cmd": lambda script: [str(script)],
}


def _parse_skill_md(path: Path) -> tuple[Dict[str, Any], str]:
    """解析 SKILL.md：YAML frontmatter + Markdown 正文。"""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    if not m:
        raise ValueError("缺少 YAML frontmatter（文件需以 --- 开头）")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter 必须是 YAML 映射")
    return meta, text[m.end():].strip()


@dataclass
class SkillPackage:
    """一个已解析的技能包。"""

    skill_id: str  # 目录名
    tool_id: str  # skill_<sanitized>
    name: str
    description: str
    version: str
    side_effect: str
    entry: Optional[str]
    runtime: str
    timeout: float
    input_schema: Dict[str, Any] = field(default_factory=dict)
    body: str = ""
    dir: Path = field(default_factory=Path)

    @property
    def has_entry(self) -> bool:
        return bool(self.entry)

    # ---- 执行（同步；ToolRegistry.execute_tool 契约）----

    def execute(self, instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if not self.has_entry:
            return {"success": False, "error": f"知识型技能 {self.tool_id} 没有可执行入口（entry）"}
        script = (self.dir / str(self.entry)).resolve()
        if self.dir.resolve() != script.parent and self.dir.resolve() not in script.parents:
            return {"success": False, "error": f"entry 越出技能目录: {self.entry}"}
        if not script.exists():
            return {"success": False, "error": f"entry 脚本不存在: {self.entry}"}
        cmd = _RUNTIME_COMMANDS[self.runtime](script)
        payload = json.dumps(
            {"instruction": str(instruction or "execute"), "inputs": dict(inputs or {})},
            ensure_ascii=False,
        )
        try:
            proc = subprocess.run(
                cmd,
                input=payload.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout,
                cwd=str(self.dir),
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"技能脚本超时（>{self.timeout:g}s）"}
        except FileNotFoundError as exc:
            return {"success": False, "error": f"运行时不可用: {exc}"}
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace")[-500:]
            return {"success": False, "error": f"技能脚本退出码 {proc.returncode}: {tail}"}
        stdout = proc.stdout.decode("utf-8", "replace")
        try:
            out = json.loads(stdout)
        except Exception:
            # 非 JSON 输出按原文返回（截断），不假装失败
            return {"success": True, "output": stdout[:8000]}
        if not isinstance(out, dict):
            return {"success": True, "output": out}
        success = bool(out.get("success", True))
        merged = {k: v for k, v in out.items() if k != "success"}
        merged["success"] = success
        if not success and not merged.get("error"):
            merged["error"] = "技能脚本报告失败（未提供 error）"
        return merged


def _make_skill_tool_class(skill: SkillPackage) -> Type:
    """生成符合 ToolRegistry 类级接口（get_metadata/execute）的技能工具类。"""

    class SkillTool:
        _skill = skill

        @classmethod
        def get_metadata(cls) -> Dict[str, Any]:
            return {
                "tool_id": cls._skill.tool_id,
                "name": cls._skill.name,
                "description": f"[技能包] {cls._skill.description}",
                "category": "skill",
                "version": cls._skill.version,
                "has_entry": cls._skill.has_entry,
                "capabilities": {"instructions": []},
            }

        @classmethod
        def execute(cls, instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
            return cls._skill.execute(instruction, inputs)

    SkillTool.__name__ = f"SkillTool_{skill.tool_id}"
    SkillTool.__qualname__ = SkillTool.__name__
    return SkillTool


class SkillCapabilityAdapter(ToolCapabilityAdapter):
    """技能工具的 Capability 适配：description / side_effect 来自 SKILL.md frontmatter。"""

    def __init__(self, tool_registry: Any, tool_id: str, skill: SkillPackage) -> None:
        super().__init__(tool_registry, tool_id)
        self.skill = skill

    def spec(self):
        base = super().spec()
        return replace(
            base,
            description=f"[技能包] {self.skill.description}",
            side_effect=self.skill.side_effect,
            # A side-effect label alone is not a declared idempotency contract.
            # Script entry skills are not automatically retried, including local report writers.
            idempotent=False,
        )


class SkillManager:
    """技能目录扫描 + 工具注册 + 清单描述。"""

    def __init__(self, skills_dir: Path, defaults: Optional[Dict[str, Any]] = None) -> None:
        self.skills_dir = Path(skills_dir)
        self.defaults = dict(defaults or {})
        self.skills: List[SkillPackage] = []
        self.skipped: List[Dict[str, str]] = []

    @classmethod
    def from_config(cls, config: Dict[str, Any], base_dir: Optional[Path] = None) -> "SkillManager":
        cfg = (config or {}).get("skills") or {}
        if cfg.get("enabled") is False:
            return cls(Path("__skills_disabled__"), cfg)
        d = Path(str(cfg.get("dir") or "skills"))
        if not d.is_absolute():
            # 默认锚定仓库根（paths.REPO_ROOT），不依赖进程 cwd，避免 gen_* 写到别处。
            from paths import REPO_ROOT
            d = (base_dir or REPO_ROOT) / d
        return cls(d, cfg)

    # ---- 加载 ----

    def load(self) -> None:
        self.skills, self.skipped = [], []
        if not self.skills_dir.is_dir():
            logger.info("技能目录不存在，跳过: {}", self.skills_dir)
            return
        for d in sorted(p for p in self.skills_dir.iterdir() if p.is_dir()):
            try:
                self.skills.append(self._load_one(d))
            except Exception as exc:
                self.skipped.append({"skill": d.name, "error": str(exc)})
                logger.warning("技能包加载失败 {}: {}", d.name, exc)
        logger.info(
            "技能包加载完成: {} 个入口技能 / {} 个知识包（{} 个跳过）",
            len(self.entry_skills()),
            len(self.knowledge_skills()),
            len(self.skipped),
        )

    def _load_one(self, d: Path) -> SkillPackage:
        md = d / "SKILL.md"
        if not md.exists():
            raise ValueError("缺少 SKILL.md")
        meta, body = _parse_skill_md(md)
        description = str(meta.get("description") or "").strip()
        if not description:
            raise ValueError("frontmatter 缺少 description")
        sanitized = _SANITIZE.sub("_", d.name).strip("_-").lower() or "skill"
        side_effect = str(meta.get("side_effect") or "none").strip().lower()
        if side_effect not in _VALID_SIDE_EFFECTS:
            raise ValueError(f"side_effect 非法: {side_effect}（允许 {_VALID_SIDE_EFFECTS}）")
        runtime = str(meta.get("runtime") or "python").strip().lower()
        if runtime not in _RUNTIME_COMMANDS:
            raise ValueError(f"runtime 非法: {runtime}（允许 {set(_RUNTIME_COMMANDS)}）")
        try:
            timeout = float(meta.get("timeout") or self.defaults.get("timeout") or 120)
        except (TypeError, ValueError):
            timeout = 120.0
        entry = meta.get("entry")
        if entry is not None:
            entry = str(entry).strip()
            script = (d / entry).resolve()
            if d.resolve() != script.parent and d.resolve() not in script.parents:
                raise ValueError(f"entry 越出技能目录: {entry}")
        input_schema = meta.get("input_schema")
        return SkillPackage(
            skill_id=d.name,
            tool_id=f"skill_{sanitized}",
            name=str(meta.get("name") or d.name),
            description=description,
            version=str(meta.get("version") or "1.0.0"),
            side_effect=side_effect,
            entry=entry,
            runtime=runtime,
            timeout=timeout,
            input_schema=dict(input_schema) if isinstance(input_schema, dict) else {},
            body=body,
            dir=d,
        )

    # ---- 访问与注册 ----

    def entry_skills(self) -> List[SkillPackage]:
        return [s for s in self.skills if s.has_entry]

    def knowledge_skills(self) -> List[SkillPackage]:
        return [s for s in self.skills if not s.has_entry]

    def by_tool_id(self, tool_id: str) -> Optional[SkillPackage]:
        return next((s for s in self.skills if s.tool_id == tool_id), None)

    def register_into(self, tool_registry: Any) -> List[str]:
        """把 entry 技能注册进 ToolRegistry；返回成功注册的 tool_id 列表。"""
        registered: List[str] = []
        for s in self.entry_skills():
            if s.tool_id in getattr(tool_registry, "tools", {}):
                logger.warning("技能工具 ID 与内置工具冲突，跳过: {}", s.tool_id)
                continue
            tool_registry.register_tool(s.tool_id, _make_skill_tool_class(s))
            registered.append(s.tool_id)
        return registered

    # ---- 描述与注入 ----

    def summary_lines(self) -> List[str]:
        lines: List[str] = []
        for s in self.skills:
            if s.has_entry:
                lines.append(f"- {s.tool_id}: {s.description[:120]}")
            else:
                lines.append(f"- [知识包] {s.name}: {s.description[:120]}")
        return lines

    def describe_all(self) -> Dict[str, Any]:
        return {
            "skills": [
                {
                    "tool_id": s.tool_id,
                    "name": s.name,
                    "description": s.description,
                    "version": s.version,
                    "has_entry": s.has_entry,
                    "runtime": s.runtime,
                    "side_effect": s.side_effect,
                    "timeout": s.timeout,
                    "dir": str(s.dir),
                }
                for s in self.skills
            ],
            "skipped": self.skipped,
        }


__all__ = ["SkillManager", "SkillPackage", "SkillCapabilityAdapter", "SkillTool"]
