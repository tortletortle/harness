"""缓存命中率与指纹校验测试（上下文缓存优化）。"""

import pytest

from cache import DesignCache, PlanCache


@pytest.fixture()
def design_cache(tmp_path):
    return DesignCache(cache_file=str(tmp_path / "design_cache.json"))


def _design_payload(marker: str) -> dict:
    return {"design_id": marker, "part1_design": {"components": [marker]}}


def test_cache_hit_and_miss_counted(design_cache):
    assert design_cache.get("需求A") is None
    design_cache.set("需求A", _design_payload("A"))
    assert design_cache.get("需求A") is not None
    assert design_cache.get("需求A") is not None
    stats = design_cache.stats()
    assert stats["lookups"] == 3
    assert stats["process_hits"] == 2
    assert stats["hit_rate"] == round(2 / 3, 4)


def test_cache_key_normalization_improves_hits(design_cache):
    design_cache.set("  需求A  ", _design_payload("A"))
    # 大小写与首尾空白规范化后命中同一键
    assert design_cache.get("需求A") is not None


def test_fingerprint_mismatch_evicts_stale_entry(design_cache):
    design_cache.set("需求A", _design_payload("A"), fingerprint="tools-v1")
    # 工具目录变化 → 指纹不一致 → 不允许错误命中
    assert design_cache.get("需求A", fingerprint="tools-v2") is None
    # 陈旧条目已被淘汰；重新生成后新指纹命中
    design_cache.set("需求A", _design_payload("A2"), fingerprint="tools-v2")
    assert design_cache.get("需求A", fingerprint="tools-v2") is not None


def test_fingerprint_absent_keeps_legacy_behavior(design_cache):
    design_cache.set("需求A", _design_payload("A"))
    # 不传指纹（旧调用方）→ 行为与原来一致
    assert design_cache.get("需求A") is not None


def test_ttl_expiry_is_a_miss(design_cache):
    design_cache.ttl = -1  # 立即过期
    design_cache.set("需求A", _design_payload("A"))
    assert design_cache.get("需求A") is None


def test_plan_cache_inherits_fingerprint(tmp_path):
    pc = PlanCache(cache_file=str(tmp_path / "plan_cache.json"))
    pc.set("design-1", {"plan": "p1"}, fingerprint="tools-v1")
    assert pc.get("design-1", fingerprint="tools-v1") is not None
    assert pc.get("design-1", fingerprint="tools-v2") is None
