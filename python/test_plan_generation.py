"""
Test plan generation and persistence with the fixed database code
"""
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from database import Database
from designer import DefaultDesigner
from llm_client import LLMClient
from models import DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment, ToolRequirement
from uuid import uuid4
from datetime import datetime

async def test_plan_generation_and_persistence():
    """Test that Designer generates plan AND database saves steps correctly"""
    
    print("[TEST] Plan Generation and Persistence Test")
    print("=" * 60)
    
    # Initialize components
    db = Database("../data/eas.db")
    
    # Load config
    import yaml
    with open("../config/models.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # Initialize LLM client
    from llm_client import LLMClient, OpenAICompatibleProvider
    llm_client = LLMClient()
    
    # Register providers
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            provider = OpenAICompatibleProvider(
                name=provider_name,
                base_url=base_urls[provider_name],
                api_key=api_key
            )
            llm_client.register_provider(provider)
            print(f"[OK] Registered provider: {provider_name}")
    
    # Initialize dispatcher
    from dispatcher import Dispatcher
    from tools import init_registry_with_ai
    tool_config = config.get("models", {}).get("designer", {})
    tool_registry = init_registry_with_ai(llm_client, tool_config)
    dispatcher = Dispatcher(tool_registry)
    
    # Initialize designer
    designer_config = config.get("models", {}).get("designer", {})
    designer = DefaultDesigner(llm_client, designer_config, dispatcher)
    
    # Create a simple design
    design = DesignDocument(
        design_id=uuid4(),
        user_request="创建一个简单的 hello world 程序",
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(
                components=["main.py"],
                interactions=["用户运行程序 -> 打印 hello world"]
            ),
            difficulty=Difficulty(
                overall="Simple",
                breakdown={"technical": "Low", "time": "5 minutes"}
            )
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[
                ToolRequirement(
                    tool_id="file_creator",
                    availability="Available"
                ),
                ToolRequirement(
                    tool_id="text_editor",
                    availability="Available"
                )
            ],
            overall_tool_completeness=1.0,
            risks=[]
        )
    )
    
    print(f"[1] Created design: {design.design_id}")
    db.save_design(design)
    
    # Generate plan
    print(f"[2] Generating execution plan...")
    plan = await designer.generate_plan(design)
    
    print(f"[3] Plan generated: {plan.plan_id}")
    print(f"    - Segments: {len(plan.segments)}")
    
    total_steps = sum(len(seg.steps) for seg in plan.segments)
    print(f"    - Total steps: {total_steps}")
    
    if total_steps == 0:
        print("[FAIL] Plan has 0 steps! Plan generation failed.")
        return False
    
    # Display steps
    for i, segment in enumerate(plan.segments):
        print(f"    - Segment {i+1}: {segment.description} ({len(segment.steps)} steps)")
        for j, step in enumerate(segment.steps):
            print(f"        Step {j+1}: {step.description} (tool: {step.tool})")
    
    # Save plan to database
    print(f"[4] Saving plan to database...")
    success = db.save_plan(plan)
    
    if not success:
        print("[FAIL] Failed to save plan to database")
        return False
    
    print("[OK] Plan saved successfully")
    
    # Verify steps are in database
    print(f"[5] Verifying steps in database...")
    steps_in_db = db.get_steps(str(plan.plan_id))
    
    print(f"[6] Steps in database: {len(steps_in_db)}")
    
    if len(steps_in_db) != total_steps:
        print(f"[FAIL] Expected {total_steps} steps, found {len(steps_in_db)} in database")
        return False
    
    print(f"[OK] All {total_steps} steps saved correctly!")
    
    # Verify step details
    print(f"[7] Verifying step details...")
    for step_row in steps_in_db[:3]:  # Show first 3 steps
        print(f"    - {step_row['description']} (tool: {step_row['tool']}, status: {step_row['status']})")
    
    print("=" * 60)
    print("[SUCCESS] Test passed! Plan generation and persistence working correctly.")
    return True

if __name__ == "__main__":
    result = asyncio.run(test_plan_generation_and_persistence())
    sys.exit(0 if result else 1)
