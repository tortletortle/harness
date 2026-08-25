"""
Final Comprehensive Test - Validates Complete Fix
Tests the entire flow without depending on backend server
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from database import Database
from designer import DefaultDesigner
from llm_client import LLMClient, OpenAICompatibleProvider
from dispatcher import Dispatcher
from tools import init_registry_with_ai
from models import DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment, ToolRequirement
from uuid import uuid4
from datetime import datetime
import yaml

async def test_complete_workflow():
    """Test the complete workflow end-to-end"""
    print("=" * 70)
    print("FINAL COMPREHENSIVE TEST - EAS 2.0 Empty Plan Fix")
    print("=" * 70)
    
    # Initialize all components
    print("\n[1] Initializing components...")
    db = Database("../data/eas.db")
    
    # Load config
    with open("../config/models.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # Initialize LLM client
    llm_client = LLMClient()
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
    
    print(f"    - Registered {len(llm_client.providers)} LLM providers")
    
    # Initialize tools and dispatcher
    tool_config = config.get("models", {}).get("designer", {})
    tool_registry = init_registry_with_ai(llm_client, tool_config)
    dispatcher = Dispatcher(tool_registry)
    
    # Initialize designer
    designer_config = config.get("models", {}).get("designer", {})
    designer = DefaultDesigner(llm_client, designer_config, dispatcher)
    
    print("    [OK] All components initialized")
    
    # Create test design
    print("\n[2] Creating test design...")
    task = f"Create a simple Python hello world script (test: {datetime.now().isoformat()})"
    design = DesignDocument(
        design_id=uuid4(),
        user_request=task,
        timestamp=datetime.utcnow(),
        part1_design=DesignPart1(
            architecture=Architecture(
                components=["main.py"],
                interactions=["User runs script -> prints hello world"]
            ),
            difficulty=Difficulty(
                overall="Simple",
                breakdown={"technical": "Low"}
            )
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[
                ToolRequirement(tool_id="file_creator", availability="Available"),
                ToolRequirement(tool_id="text_editor", availability="Available")
            ],
            overall_tool_completeness=1.0,
            risks=[]
        )
    )
    
    db.save_design(design)
    print(f"    [OK] Design created: {design.design_id}")
    
    # Generate plan
    print("\n[3] Generating execution plan...")
    plan = await designer.generate_plan(design)
    
    total_steps = sum(len(seg.steps) for seg in plan.segments)
    print(f"    [OK] Plan generated: {plan.plan_id}")
    print(f"    - Segments: {len(plan.segments)}")
    print(f"    - Total steps: {total_steps}")
    
    if total_steps == 0:
        print("    [FAIL] Plan has 0 steps!")
        return False
    
    # Save plan to database
    print("\n[4] Saving plan to database...")
    success = db.save_plan(plan)
    
    if not success:
        print("    [FAIL] Failed to save plan")
        return False
    
    print("    [OK] Plan saved")
    
    # Verify steps in database
    print("\n[5] Verifying database persistence...")
    steps_in_db = db.get_steps(str(plan.plan_id))
    
    print(f"    - Steps in database table: {len(steps_in_db)}")
    print(f"    - Expected steps: {total_steps}")
    
    if len(steps_in_db) != total_steps:
        print(f"    [FAIL] Mismatch! Expected {total_steps}, got {len(steps_in_db)}")
        return False
    
    print("    [OK] All steps persisted correctly")
    
    # Verify step details
    print("\n[6] Verifying step details...")
    for i, step_row in enumerate(steps_in_db[:5]):
        print(f"    - Step {i+1}: {step_row['description']}")
        print(f"      Tool: {step_row['tool']}, Status: {step_row['status']}")
    
    # Test retrieval
    print("\n[7] Testing plan retrieval...")
    retrieved_plan = db.get_plan(str(plan.plan_id))
    
    if not retrieved_plan:
        print("    [FAIL] Could not retrieve plan")
        return False
    
    retrieved_steps = sum(len(seg.steps) for seg in retrieved_plan.segments)
    print(f"    - Retrieved segments: {len(retrieved_plan.segments)}")
    print(f"    - Retrieved steps: {retrieved_steps}")
    
    if retrieved_steps != total_steps:
        print(f"    [FAIL] Retrieved {retrieved_steps} steps, expected {total_steps}")
        return False
    
    print("    [OK] Plan retrieval successful")
    
    # Final summary
    print("\n" + "=" * 70)
    print("TEST RESULTS SUMMARY")
    print("=" * 70)
    print(f"✅ Plan Generation: {total_steps} steps created")
    print(f"✅ Database Persistence: {len(steps_in_db)} steps saved")
    print(f"✅ Database Retrieval: {retrieved_steps} steps retrieved")
    print(f"✅ Data Integrity: All checks passed")
    print("\n[SUCCESS] All tests passed! Empty plan issue is FIXED.")
    print("=" * 70)
    
    return True

if __name__ == "__main__":
    result = asyncio.run(test_complete_workflow())
    sys.exit(0 if result else 1)
