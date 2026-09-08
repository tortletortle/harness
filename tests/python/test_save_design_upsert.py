import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from database import Database
from models import Architecture, DesignDocument, DesignPart1, Difficulty, ToolAssessment


def make_design(design_id, suffix, timestamp):
    return DesignDocument(
        design_id=design_id,
        user_request=f"需求 {suffix}",
        timestamp=timestamp,
        part1_design=DesignPart1(
            architecture=Architecture(
                components=[f"component-{suffix}"],
                interactions=[f"interaction-{suffix}"],
            ),
            difficulty=Difficulty(overall=suffix, breakdown={"score": suffix}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[],
            overall_tool_completeness=0.5 if suffix == "v1" else 1.0,
            risks=[f"risk-{suffix}"],
        ),
        user_approval=False,
        user_comments=f"comment-{suffix}",
    )


def test_save_design_upsert_persists_revised_body(tmp_path):
    db = Database(str(tmp_path / "eas.db"))
    design_id = uuid.uuid4()
    first_timestamp = datetime(2026, 1, 1)
    second_timestamp = first_timestamp + timedelta(days=1)

    assert db.save_design(make_design(design_id, "v1", first_timestamp))
    assert db.save_design(make_design(design_id, "v2", second_timestamp))

    saved = db.get_design(str(design_id))
    assert saved is not None
    assert saved.user_request == "需求 v2"
    assert saved.timestamp == second_timestamp
    assert saved.part1_design.architecture.components == ["component-v2"]
    assert saved.part1_design.difficulty.overall == "v2"
    assert saved.part2_tool_assessment.risks == ["risk-v2"]
    assert saved.part2_tool_assessment.overall_tool_completeness == 1.0
    assert saved.user_comments == "comment-v2"
