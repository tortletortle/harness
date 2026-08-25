#!/usr/bin/env python3
"""EAS 2.0 关键功能覆盖测试。

测试使用临时目录/临时 SQLite，不调用真实 LLM API，也不安装外部依赖。
"""

import asyncio
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from database import Database
from executor import DefaultExecutor
from models import (
    Architecture,
    DesignDocument,
    DesignPart1,
    Difficulty,
    ExecutionPlan,
    PlanSegment,
    PlanStatus,
    Step,
    ToolAssessment,
)
from plugin_system import PluginManager
from tools import ToolRegistry


class TestTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.registry = ToolRegistry()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_all_tools_are_registered(self):
        expected = {
            "file_creator", "command_runner", "package_installer", "manual",
            "git_operator", "text_editor", "web_fetcher",
        }
        self.assertTrue(expected.issubset(self.registry.tools.keys()))
        for metadata in self.registry.list_tools():
            self.assertIn("tool_id", metadata)
            self.assertIn("capabilities", metadata)

    def test_file_creator_and_text_editor(self):
        file_path = self.root / "nested" / "sample.txt"
        result = self.registry.execute_tool(
            "file_creator", "create_file",
            {"path": str(file_path), "content": "one\n"},
        )
        self.assertTrue(result["success"], result)

        result = self.registry.execute_tool(
            "text_editor", "append_to_file",
            {"path": str(file_path), "content": "two\n"},
        )
        self.assertTrue(result["success"], result)

        result = self.registry.execute_tool(
            "text_editor", "replace_in_file",
            {"path": str(file_path), "old_text": "one", "new_text": "ONE"},
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "ONE\ntwo\n")

        result = self.registry.execute_tool(
            "text_editor", "insert_at_line",
            {"path": str(file_path), "line_number": 2, "content": "middle"},
        )
        self.assertTrue(result["success"], result)
        self.assertIn("middle", file_path.read_text(encoding="utf-8"))

        missing = self.registry.execute_tool(
            "text_editor", "read_file", {"path": str(self.root / "missing.txt")}
        )
        self.assertFalse(missing["success"])

    def test_command_runner_and_git(self):
        result = self.registry.execute_tool(
            "command_runner", "run_command",
            {"command": "python -c \"print('coverage-ok')\"", "timeout": 10},
        )
        self.assertTrue(result["success"], result)
        self.assertIn("coverage-ok", result["stdout"])

        repo = self.root / "repo"
        repo.mkdir()
        result = self.registry.execute_tool(
            "git_operator", "git_init", {"path": str(repo)}
        )
        self.assertTrue(result["success"], result)
        self.assertTrue((repo / ".git").exists())

        failed = self.registry.execute_tool(
            "command_runner", "run_command",
            {"command": "python -c \"raise SystemExit(3)\"", "timeout": 10},
        )
        self.assertFalse(failed["success"])
        self.assertEqual(failed["returncode"], 3)

    def test_package_validation_and_unknown_tool(self):
        result = self.registry.execute_tool(
            "package_installer", "install_pip", {}
        )
        self.assertFalse(result["success"])

        result = self.registry.execute_tool(
            "does_not_exist", "run", {}
        )
        self.assertFalse(result["success"])

    def test_web_fetcher_with_local_file(self):
        source = self.root / "source.txt"
        target = self.root / "downloaded.txt"
        source.write_text("local-web-content", encoding="utf-8")
        result = self.registry.execute_tool(
            "web_fetcher", "fetch_content", {"url": source.as_uri()}
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["content"], "local-web-content")

        result = self.registry.execute_tool(
            "web_fetcher", "download_file",
            {"url": source.as_uri(), "path": str(target)},
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(target.read_text(encoding="utf-8"), "local-web-content")


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "eas.db"))
        self.design = DesignDocument(
            user_request="database coverage",
            part1_design=DesignPart1(
                architecture=Architecture(components=["api"], interactions=["db"]),
                difficulty=Difficulty(overall="easy", breakdown={"score": 1}),
            ),
            part2_tool_assessment=ToolAssessment(
                required_tools=[], overall_tool_completeness=1.0, risks=[]
            ),
        )
        self.step = Step(
            order=1,
            description="create file",
            tool="file_creator",
            inputs={"instruction": "create_file", "path": "x.txt"},
            check_criteria="手动检查",
        )
        self.plan = ExecutionPlan(
            design_id=self.design.design_id,
            status=PlanStatus.PENDING,
            segments=[PlanSegment(description="phase 1", steps=[self.step])],
            implementation_list=["x.txt"],
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_design_round_trip_and_update(self):
        self.assertTrue(self.db.save_design(self.design))
        loaded = self.db.get_design(str(self.design.design_id))
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.user_request, self.design.user_request)
        self.assertEqual(loaded.part1_design.architecture.components, ["api"])

        self.design.user_approval = True
        self.design.user_comments = "approved"
        self.assertTrue(self.db.save_design(self.design))
        loaded = self.db.get_design(str(self.design.design_id))
        self.assertTrue(loaded.user_approval)
        self.assertEqual(loaded.user_comments, "approved")

    def test_plan_steps_logs_and_round_trip(self):
        self.assertTrue(self.db.save_design(self.design))
        self.assertTrue(self.db.save_plan(self.plan))
        loaded = self.db.get_plan(str(self.plan.plan_id))
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.segments), 1)
        self.assertEqual(len(loaded.segments[0].steps), 1)
        self.assertEqual(loaded.segments[0].steps[0].description, "create file")

        self.db.log("INFO", "coverage event", {"plan_id": str(self.plan.plan_id)}, "test")
        with closing(self.db._get_connection()) as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM logs").fetchone()
        self.assertEqual(row["count"], 1)

    def test_list_queries(self):
        self.assertTrue(self.db.save_design(self.design))
        self.assertTrue(self.db.save_plan(self.plan))
        self.assertEqual(len(self.db.list_designs()), 1)
        self.assertEqual(len(self.db.list_plans()), 1)
        self.assertEqual(len(self.db.list_plans(str(self.design.design_id))), 1)


class TestPluginsAndExecution(unittest.TestCase):
    def test_plugin_load_reload_and_unload(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin_path = Path(directory) / "coverage_plugin.py"
            plugin_path.write_text(
                "from plugin_system import ToolPlugin\n"
                "class CoveragePlugin(ToolPlugin):\n"
                "    @property\n"
                "    def name(self): return 'coverage_plugin'\n"
                "    @property\n"
                "    def version(self): return '1.0.0'\n"
                "    def execute(self, instruction, inputs):\n"
                "        return {'success': True, 'value': inputs.get('value')}\n",
                encoding="utf-8",
            )
            manager = PluginManager(directory)
            plugin = manager.load_plugin(str(plugin_path))
            self.assertIsNotNone(plugin)
            self.assertEqual(len(manager.list_plugins()), 1)
            result = manager.execute_tool_plugin("coverage_plugin", "run", {"value": 7})
            self.assertTrue(result["success"])
            self.assertTrue(manager.reload_plugin("coverage_plugin"))
            self.assertTrue(manager.unload_plugin("coverage_plugin"))
            self.assertEqual(manager.list_plugins(), [])

    def test_executor_end_to_end_without_llm(self):
        class NoopLLM:
            pass

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "hello.txt")
                executor = DefaultExecutor(NoopLLM(), {"primary": {}})
                step = Step(
                    order=1,
                    description="create test artifact",
                    tool="file_creator",
                    inputs={
                        "instruction": "create_file",
                        "path": path,
                        "content": "coverage",
                    },
                    check_criteria="手动检查",
                )
                result = await executor.execute_step(step)
                self.assertTrue(result["success"], result)
                self.assertTrue(result["check_passed"])
                self.assertEqual(Path(path).read_text(encoding="utf-8"), "coverage")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
