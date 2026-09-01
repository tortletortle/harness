"""验收失败反馈文本构建（S25 反馈回路的上游）。

自 api/server.py 迁出（server.py 拆分第一刀，2026-09-01）。职责单一：把验收
失败规则转成给 coding_agent 的反馈文本。§25 是管道，动作 B 第一级（test_passes，
§27）给它接入行为级上游；本模块只做纯文本构建，不做状态写入、不做重试决策。
"""

from typing import Optional

_FEEDBACK_TEST_OUTPUT_LIMIT = 1200


def build_acceptance_feedback(acceptance_result) -> Optional[str]:
    """把验收失败规则转成给 coding_agent 的反馈文本；无可反馈缺口返回 None。

    acceptance_result 形如 {"passed": bool, "checks": [{"type", "path", "text", "passed", ...}]}。
    提取 passed=False 的 content_contains（命名/内容缺口）与 test_passes（测试失败，
    含输出尾部，动作 B 第一级），供 §25 反馈重试使用；其余类型无语义可反馈，不进反馈。
    """
    try:
        if not isinstance(acceptance_result, dict):
            return None
        content_missing = []
        test_failures = []
        for check in acceptance_result.get("checks") or []:
            if not isinstance(check, dict) or check.get("passed") is not False:
                continue
            rtype = str(check.get("type", ""))
            if rtype == "content_contains":
                evidence = check.get("evidence") or {}
                text = str(check.get("text") or evidence.get("text") or "").strip()
                path = str(check.get("path") or evidence.get("path") or "").strip()
                if text:
                    content_missing.append(f"'{text}'（应出现在 {path or '目标文件'} 中）")
            elif rtype == "test_passes":
                evidence = check.get("evidence") or {}
                suite = str(evidence.get("suite") or "").strip()
                test_path = str(evidence.get("test_path") or "").strip()
                exit_code = evidence.get("exit_code")
                output = str(evidence.get("stderr_tail") or evidence.get("stdout_tail") or "").strip()
                head = f"suite {suite or '?'}"
                if test_path:
                    head += f"（{test_path}）"
                if exit_code is not None:
                    head += f"，exit {exit_code}"
                if len(output) > _FEEDBACK_TEST_OUTPUT_LIMIT:
                    output = "...[截断]\n" + output[-_FEEDBACK_TEST_OUTPUT_LIMIT:]
                test_failures.append((head, output))
        if not content_missing and not test_failures:
            return None
        parts = []
        if test_failures:
            lines = ["上轮修改未通过确定性验收：测试未全部通过——"]
            for head, output in test_failures:
                lines.append(f"- {head}")
                if output:
                    lines.append("失败输出尾部：")
                    lines.append(output)
            lines.append("请先修复上述失败测试，再交付本轮修改。")
            parts.append("\n".join(lines))
        if content_missing:
            parts.append(
                "上轮修改未通过确定性验收：文件中缺少以下内容，"
                "请在本轮修改中确保完整包含这些标识符（逐字出现）——" + "；".join(content_missing)
            )
        return "\n\n".join(parts)
    except Exception:
        return None
