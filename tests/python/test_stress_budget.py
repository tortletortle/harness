"""The real-model cap is persistent across servers and atomic under contention."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.stress_budget import BudgetUnavailable, claim_budget, create_budget, read_budget


def test_missing_or_corrupt_ledger_fails_closed(tmp_path):
    path = tmp_path / "budget.sqlite"
    with pytest.raises(BudgetUnavailable, match="missing"):
        claim_budget(path)
    path.write_text("not sqlite")
    with pytest.raises(BudgetUnavailable, match="unavailable"):
        claim_budget(path)


def test_once_only_setup_and_persistent_claims(tmp_path):
    path = tmp_path / "budget.sqlite"
    create_budget(path, precharged=98)
    with pytest.raises(FileExistsError):
        create_budget(path, precharged=0)
    assert claim_budget(path) == 99
    assert claim_budget(path) == 100
    with pytest.raises(BudgetUnavailable, match="cap reached"):
        claim_budget(path)
    assert read_budget(path) == {"cap": 100, "used": 100, "precharged": 98}


def test_concurrent_claims_never_exceed_total_cap(tmp_path):
    path = tmp_path / "budget.sqlite"
    create_budget(path, precharged=96)

    def attempt(_):
        try:
            return claim_budget(path)
        except BudgetUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(32)))
    assert sorted(value for value in results if value is not None) == [97, 98, 99, 100]
    assert results.count(None) == 28
    assert read_budget(path)["used"] == 100
