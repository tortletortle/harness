"""
Python 语法门禁（SWE-agent ACI 式 lint 拦截）

原则（SWE-agent 论文 ACI 设计第一原则）：编辑命令执行前先做语法检查，
不让语法错误的 .py 内容落盘——避免把"看起来写完了"的坏文件留给后续步骤。

规则（docs/ERROR_HANDLING.md 工具层约定，工具永不抛异常）：
- 仅对 .py 文件生效，其他扩展名直接放行；
- 新内容语法合法 → 放行；
- 新内容语法错误：
  - 文件不存在 / 原内容语法合法 → 拒绝（不许把好文件改坏）；
  - 原内容本来就语法错误 → 放行（允许修复过程经过中间态），调用方
    应在结果里附带 syntax_warning 提示；
- 调用方可用 force_write=True 显式跳过门禁（写模板/测试夹具等场景）。
"""

from collections import namedtuple
from typing import Optional

GateResult = namedtuple("GateResult", ["allowed", "detail"])


def lint_python(path: str, new_content: str, old_content: Optional[str]) -> GateResult:
    """对 .py 写入做语法门禁检查。

    Args:
        path: 目标文件路径（用于报错定位与扩展名判断）。
        new_content: 即将写入的完整内容。
        old_content: 写入前的完整内容；文件不存在时传 None。

    Returns:
        GateResult(allowed, detail)：allowed=False 时 detail 为拒绝理由；
        allowed=True 且 detail 非 None 表示放行但建议附带警告。
    """
    try:
        if not str(path).lower().endswith(".py"):
            return GateResult(True, None)
        try:
            compile(new_content, str(path), "exec")
            return GateResult(True, None)
        except SyntaxError as e:
            reason = f"语法检查未通过（第 {e.lineno} 行）：{e.msg}"
        except (ValueError, TypeError) as e:
            reason = f"语法检查未通过：{e}"

        # 新内容语法错误：看旧内容是否本来就是坏的
        if old_content is not None:
            try:
                compile(old_content, str(path), "exec")
                old_broken = False
            except Exception:
                old_broken = True
            if old_broken:
                # 原文件已经坏了，允许继续（修复中间态），但提示调用方
                return GateResult(True, f"注意：写入后的文件仍有语法错误——{reason}")
        return GateResult(False, reason)
    except Exception as e:  # 门禁自身故障绝不阻断写入，但要有痕迹
        return GateResult(True, f"语法门禁跳过（内部异常：{e}）")


__all__ = ["GateResult", "lint_python"]
