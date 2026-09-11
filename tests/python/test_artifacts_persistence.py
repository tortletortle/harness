from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from database import Database


def test_artifact_metadata_roundtrip(tmp_path):
    db = Database(str(tmp_path / "artifacts.db"))
    assert db.create_artifact(
        "artifact-1", "test-report", "artifact://artifact-1",
        plan_id=None, metadata={"passed": True}, size_bytes=12,
    )
    artifact = db.get_artifact("artifact-1")
    assert artifact["uri"] == "artifact://artifact-1"
    assert artifact["metadata"] == {"passed": True}
    assert artifact["size_bytes"] == 12
    assert db.list_artifacts()[0]["artifact_id"] == "artifact-1"


def test_legacy_steps_database_migrates_dependencies(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE plans (plan_id TEXT PRIMARY KEY, design_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, segments TEXT NOT NULL, implementation_list TEXT NOT NULL, dynamic_insertions TEXT NOT NULL)")
    conn.execute("CREATE TABLE steps (step_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, segment_id TEXT NOT NULL, step_order INTEGER NOT NULL, description TEXT NOT NULL, tool TEXT NOT NULL, inputs TEXT NOT NULL, check_criteria TEXT NOT NULL, outputs TEXT, status TEXT NOT NULL, executed_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0)")
    conn.commit()
    conn.close()
    Database(str(path))
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(steps)")}
    assert "depends_on" in columns
    assert "artifacts" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
