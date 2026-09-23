"""Offline fault injection for the stress runner itself; no service/model calls."""

import asyncio
from contextlib import closing
import sqlite3

import pytest

from scripts import run_isolated_stress as stress


class DummyClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass


@pytest.mark.parametrize("raises", [False, True])
async def test_first_fault_cancels_inflight_and_never_starts_queued_flows(tmp_path, monkeypatch, raises):
    monkeypatch.setattr(stress.httpx, "AsyncClient", lambda **_kwargs: DummyClient())
    started = []
    cancelled = asyncio.Event()

    async def fake_flow(index, *_args, **_kwargs):
        started.append(index)
        if index == 1:
            return {"marker": "ARENA_STRESS_00001", "stage": "done", "success": True}
        if index == 2:
            await asyncio.sleep(0)  # Allow a second in-flight flow to start.
            if raises:
                raise RuntimeError("injected failure")
            return {"marker": "ARENA_STRESS_00002", "stage": "injected_fault", "success": False}
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(stress, "_flow", fake_flow)
    result = await asyncio.wait_for(stress._load(tmp_path, "unused", "token", "real", 8, 2), timeout=2)
    assert set(started) == {1, 2, 3}
    assert cancelled.is_set()
    assert any(flow["stage"] == ("harness_exception" if raises else "injected_fault")
               for flow in result["flows"])
    assert all(flow["stage"] != "done" for flow in result["flows"] if flow["marker"] != "ARENA_STRESS_00001")


def test_db_invariant_reader_releases_file_for_cleanup(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db_path = data / "eas.db"
    with closing(sqlite3.connect(db_path)) as conn:
        for table in ("plans", "execution_runs", "execution_attempts"):
            conn.execute(f"CREATE TABLE {table} (status TEXT)")
            conn.execute(f"INSERT INTO {table} VALUES ('Completed')")
        conn.commit()
    assert stress._get_db_invariants(tmp_path)["plans_by_status"] == {"Completed": 1}
    db_path.unlink()  # Windows refuses this if _get_db_invariants left SQLite open.
    assert not db_path.exists()
