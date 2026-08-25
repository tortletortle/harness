"""
Test to verify steps are saved to database
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from database import Database
from models import ExecutionPlan, PlanSegment, Step, DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment, ToolRequirement
from uuid import uuid4
from datetime import datetime

def test_steps_persistence():
    """Test that steps are saved to the steps table"""
    
    # Create test database
    db = Database("data/test_steps.db")
    
    # Create a simple design
    design = DesignDocument(
        design_id=uuid4(),
        user_request="Test task",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(
                components=["Component1"],
                interactions=["Interaction1"]
            ),
            difficulty=Difficulty(
                overall="Simple",
                breakdown={"technical": "Low"}
            )
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[
                ToolRequirement(
                    tool_id="file_creator",
                    availability="Available"
                )
            ],
            overall_tool_completeness=1.0,
            risks=[]
        )
    )
    
    # Save design
    db.save_design(design)
    
    # Create a plan with steps
    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=design.design_id,
        created_at=datetime.utcnow(),
        status="Pending",
        segments=[
            PlanSegment(
                segment_id=uuid4(),
                description="Test Segment",
                steps=[
                    Step(
                        step_id=uuid4(),
                        order=1,
                        description="Create file test.txt",
                        tool="file_creator",
                        inputs={"instruction": "create_file", "path": "test.txt"},
                        check_criteria="File created",
                        status="Pending"
                    ),
                    Step(
                        step_id=uuid4(),
                        order=2,
                        description="Create directory src",
                        tool="file_creator",
                        inputs={"instruction": "create_directory", "path": "src"},
                        check_criteria="Directory created",
                        status="Pending"
                    )
                ]
            )
        ],
        implementation_list=["test.txt"]
    )
    
    # Save plan
    success = db.save_plan(plan)
    print(f"[TEST] Save plan: {'SUCCESS' if success else 'FAILED'}")
    
    # Query steps from database
    steps = db.get_steps(str(plan.plan_id))
    print(f"[TEST] Steps in database: {len(steps)}")
    
    if len(steps) == 2:
        print("[OK] Steps saved correctly!")
        print(f"  Step 1: {steps[0]['description']}")
        print(f"  Step 2: {steps[1]['description']}")
        return True
    else:
        print(f"[FAIL] Expected 2 steps, got {len(steps)}")
        return False

if __name__ == "__main__":
    result = test_steps_persistence()
    sys.exit(0 if result else 1)
