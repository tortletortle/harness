"""Automatic whole-step retry eligibility from trusted capability declarations only.

No user input or tool-result flag can make an ineligible operation repeatable.
This does not cancel a timed-out thread/process or govern explicit user reexecution.
"""
from typing import Any


def automatic_retry_policy(tool: str, capability_registry: Any = None, tool_registry: Any = None) -> dict:
    tool_id = str(tool or '').split(':', 1)[0]
    base = {'eligible': False, 'tool_id': tool_id, 'source': 'unavailable', 'reason': 'capability_unavailable'}
    try:
        capability = capability_registry.get(tool_id) if capability_registry is not None else None
        if capability is not None:
            spec = capability.spec()
            kind, effect, idempotent = spec.kind, spec.side_effect, spec.idempotent
            source = 'capability_registry'
        else:
            from capabilities import ToolCapabilityAdapter
            if tool_registry is None or tool_id not in ToolCapabilityAdapter._KNOWN_TOOLS:
                return base
            effect, idempotent = ToolCapabilityAdapter.risk_for(tool_id)
            kind = 'human_tool' if tool_id == 'manual_tool' else 'deterministic_tool'
            source = 'builtin_policy'
        eligible = kind == 'deterministic_tool' and effect == 'none' and idempotent is True
        return {**base, 'eligible': eligible, 'source': source,
                'reason': 'declared_read_only_idempotent' if eligible else 'effectful_non_idempotent_or_non_deterministic',
                'kind': kind, 'side_effect': effect, 'idempotent': idempotent is True}
    except Exception:
        return base


def annotate_retry_failure(result: dict, policy: dict) -> dict:
    if result.get('success') and result.get('check_status') != 'failed':
        return result
    result = {**result, 'retry_policy': dict(policy)}
    if policy.get('eligible') is not True:
        result.update(retryable=False, manual_retry_required=True)
    return result
