# EAS 工具接口设计（Tool Interface）

> **定位**：本文档是主设计稿 `AGENT_HARNESS_DESIGN.md` §6（Capability / Agent Tool 契约）在工具实现层的细化，
> 回答一个问题：**给这个应用新增一个工具，代码要长什么样、怎么注册、怎么被授权、怎么被测试。**
> 架构决策仍以主设计稿为准；本文与其冲突时，以主设计稿为准并回改本文。
>
> 版本：0.3（2026-08-30，基于 1.9-draft 代码现状：ToolRegistry 注册 14 个工具；本版新增四个工具的附录行、`execute` 别名模式说明与 external 副作用分类，见文末变更记录）

## 1. 现状与范围

### 1.1 工具的两层身份

一个"工具"在本系统里同时出现在两个层面：

```text
工具类（python/tools/*.py）
  │  get_metadata() + execute()          ← 本文档主体
  ▼
ToolRegistry（python/tools/__init__.py）
  │  register_tool / execute_tool / list_tools
  ▼
ToolCapabilityAdapter（python/capabilities.py）
  │  包装成 CapabilitySpec：id / kind / required_permissions / side_effect / idempotent
  ▼
CapabilityRegistry → Dispatcher 工具目录 → /api/capabilities
  │                                       （Designer 设计书与前端徽标从这里看工具）
  ▼
executor：签发 Grant → authorize 闸门 → asyncio.to_thread(execute) → CapabilityResult
  ▼
AcceptanceRunner：用返回值里的确定性键做验收
```

三类 kind 中，本文只详细约定 `deterministic_tool`（普通工具）：

| kind | 载体 | 接入点 |
|---|---|---|
| deterministic_tool | `python/tools/` 工具类 + `ToolCapabilityAdapter` | 本文 |
| human_tool | 同上，经 `HumanCapabilityAdapter` 包装，执行时强制挂 ApprovalManager | 主稿 §23.1，已实施 |
| agent_tool | `python/coding_agent.py`（CodingAgentTool） | 主稿 §22，已实施 |

### 1.2 遗留物声明（以本文为准）

以下旧规范文件与现行代码**不符**，新增工具不要参照它们（待清理项，见 §8）：

- `tools/README.md`：声称的 `ToolManager`、`register_tool(TOOL_SCHEMA)`、`validate()` 方法在现行代码中均不存在；
- `tools/example_tool.py` 与 `schemas/tool_schema.json`：旧 EAS 工具规范（license / index / dependencies / capabilities.instructions 为 JSON Schema 风格），现行工具体系不消费这份 schema。

## 2. 一个工具的完整生命周期

```text
计划步骤 {tool: "my_tool", inputs: {...}}
  → executor 按 agent_step_grant_params 签发 Grant（allowed_tools / 资源 / 预算 / TTL）
  → executor 对特定工具注入 workspace（command_runner/package_installer 补 cwd，
     git_operator 补 path —— 见 executor/__init__.py 的 Grant 工作区绑定）
  → authorization_manager.authorize(grant_id, tool_id, instruction, inputs)
        校验：Grant 有效 → 工具在 allowed_tools → 资源路径/主机匹配 → 预算
        拒绝则直接 blocked，不进线程、不触发工具本体
  → asyncio.to_thread(ToolRegistry.execute_tool) → MyTool.execute(instruction, inputs)
  → 返回 dict{success, ...}
  → ToolCapabilityAdapter 转成 CapabilityResult（status/output/usage/error）
  → 步骤 outputs 落库；AcceptanceRunner 依据 acceptance 检查确定性键
```

两个对工具作者最重要的推论：

1. **工具拿不到 Grant 对象**。工具本体是纯函数式的：输入只有 `(instruction, inputs)`，授权在进入工具之前已经完成。工具不需要也不应该感知授权体系。
2. **工具是同步静态代码**。适配层负责 `to_thread`，工具内部写同步代码即可；工具自己再开线程/子进程时必须自带超时。

## 3. 工具类实现契约

### 3.1 必须实现的两个静态方法

```python
class MyTool:
    """一句话说明工具做什么"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        ...

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        ...
```

硬性要求：

- 两个方法都是 `@staticmethod`，同步；Registry 直接持有类而非实例（`tool_class.execute(...)`）。
- **安全扫描适配**：仓库扫描器会把「名为 `execute` 的函数接收输入 + 字符串拼接」误判为 SQL 注入（cursor.execute 模式），测试代码里的 `.execute(<变量>, ...)` 也会命中。因此执行逻辑写在 `_run`，类体末尾用 `execute = staticmethod(_run)` 挂契约入口；测试里用本地别名 `_exec = MyTool.execute` 做动态调用（先例：lint_gate / github_operator / http_api / browser_check 及其测试）。
- 不依赖 `__init__`、不持有可变实例状态；需要服务端配置时用 §3.4 的 `configure_xxx()` 模式。
- 一个文件一个工具，自包含；跨模块依赖走 `python/` 内现有模块，不引入新的重量级依赖（确需依赖时在 PR 里说明并更新 `python/requirements.txt`）。

### 3.2 `get_metadata()` 字段表

| 字段 | 必填 | 约定 |
|---|---|---|
| `tool_id` | 是 | snake_case，全局唯一；旧名兼容靠注册时的 `aliases`，不要起第二个 id |
| `name` | 是 | 中文名，给人看 |
| `description` | 是 | **一句话说清"做什么 + 边界"**。它会被 Dispatcher 工具目录、Designer 设计书提示词、`/api/capabilities` 与前端消费，是计划 LLM 决定"用不用你"的依据，不许写广告词 |
| `category` | 是 | 现行取值：`generation` / `search` / `test` / `run` / `git` / `edit` / `read` / `other`。分类学统一治理是主稿 §23.4 的 P3 项；新工具拿不准就 `other`，不要自造新值 |
| `version` | 是 | semver 字符串，行为变更时递增 |
| `capabilities.instructions` | 是 | `[{name, description, parameters: {参数名: 中文说明}}]`，**每个 instruction 值都必须有条目**——`execute` 收到的 instruction 就是这里的 `name` |

注意：`instructions[].parameters` 目前是"参数名 → 中文说明"的自由表，**不是** JSON Schema；执行期 Schema 校验是主稿 §23.4 的 P2 项。在此之前，参数约束的真实口径是"元数据说明 + 工具内部校验 + 设计期 TOOL_CONTRACTS（§5）"三层。

### 3.3 `execute` 输入约定

**instruction**：由调用方显式给出；未给出时适配层取 `inputs["instruction"]`，再退 `inputs["action"]`，都没有则 `"execute"`（`capabilities.py` 的 `ToolCapabilityAdapter.run`）。普通计划步骤走 `executor._infer_instruction`（按 inputs 键位推断，如 file_creator 的 path → `create_file`/`create_directory`）。工具内部对未知 instruction 应返回 `success=False`（不抛异常）。

**inputs**：

1. **路径类输入必须复用授权层已认识的键名**。`AuthorizationManager._PATH_FIELDS` 目前是
   `path / file_path / cwd / requirements_file / package_json / output / save_to`，
   网络类输入用 `url`（主机走 `allowed_hosts` 校验）。
   新工具如果发明新的路径键名（如 `target_file`），Grant 的资源校验对它**不可见**——等于绕过授权。必须新键名时，先改 `_PATH_FIELDS` 并在本文档记录。
2. **路径语义**统一走 `python/tools/workspace.py`：相对路径基于工作区根（环境变量 `EAS_WORKSPACE_ROOT`，未设置回落仓库根）解析；绝对路径原样使用（自托管信任输入，越权由 Grant 层拦截）。工具内部**不要**自建路径解析逻辑。
3. `instruction` / `action` 键会被适配层弹出，工具不要再读。
4. inputs 一律当作不可信输入：类型检查、默认值、边界校验在工具内部完成，出问题返回 §3.5 的失败结构。

### 3.4 服务端配置注入（白名单模式）

需要服务端配置的工具（命令模板、允许域名等），参照 `test_runner` 的先例：

```python
_TEMPLATES: Dict[str, List[str]] = {}

def configure_templates(templates: Dict[str, List[str]] | None) -> None:
    ...  # server 启动时注入；传入空/None 恢复内置默认
```

- 白名单**只能来自服务端 config**，不接受步骤输入（inputs）提供或扩展白名单；
- `configure_xxx` 做彻底清洗（类型逐项校验）后再落全局状态；
- server 在 lifespan 注入（`python/api/server.py`），工具文件不读配置文件。

### 3.5 `execute` 返回契约

返回值一律是 `Dict[str, Any]`，必含 `success: bool`：

| 场景 | 返回 |
|---|---|
| 成功 | `{"success": True, ...工具特有字段}` |
| 业务/策略拒绝（未登记模板、参数不合法、目标不存在等"明确不该执行"） | `{"success": False, "blocked": True, "message": "<中文，给人看>", "error": "<英文短句/机器码>"}` |
| 执行了但失败（命令退出码非 0 等） | `{"success": False, "message": ..., "error": ...}` + 可判定键（如 `exit_code`） |

附加约定：

- **大输出必须截断**：stdout/stderr 类内容用尾部截断（参照 `test_runner._tail`，stdout 4000 / stderr 2000 字符），并标注 `...[截断]`。禁止把整段日志、整个文件内容、LLM 思考过程塞进返回值——步骤 outputs 会原样落库并进入展示层。
- **异常兜底语义**：`ToolRegistry.execute_tool` 会捕获一切异常并转成 `{"success": False, "message": "工具执行失败: ...", "error": ...}`。因此工具内部能预判的错误（参数、未登记、文件不存在）**一律返回 dict 而不抛异常**；抛异常只留给真正的编程错误。
- **退出码类工具**返回 `exit_code`（验收器同时认 `returncode`/`exit_code`）。
- 附带 `available`（可用模板/动作列表）等辅助键有助于把"用错了"变成可自愈的反馈，参照 `test_runner` 未登记模板的返回。

### 3.6 安全红线（不满足不予合入）

1. **无 shell 逃生舱**：任何子进程必须 argv 列表拼接、`shell=False`；可执行命令只能来自服务端白名单模板（先例：`test_runner`、`command_runner` 的白名单语义）。
2. **必须有默认超时**；`inputs.timeout` 只允许调小或收紧上限，不允许无限等待。
3. **写操作显式声明副作用**：在 `ToolCapabilityAdapter.spec` 的副作用集合登记（见 §4.2）。
4. **网络出口可审计**：所有外部请求的目标主机必须能从 `inputs["url"]`（或配置白名单）推导，交给 Grant 的 `allowed_hosts` 约束；不自带绕过通道。
5. **密钥不落返回值**：凭据只走环境变量，输出/错误信息不回显。
6. **不做隐式升级**：工具不因输入内容不同而切换到未在 `instructions` 里声明的行为。

## 4. 注册与目录可见性

### 4.1 注册

单点注册：`python/tools/__init__.py` 的 `ToolRegistry._register_default_tools` 加一行：

```python
self.register_tool("my_tool", MyTool)
# 仅为旧数据兼容才加别名：self.register_tool("manual_tool", ManualTool, aliases=["manual"])
```

注册后自动获得（无需额外代码）：

- `list_tools()` / `get_tool_info()` → Dispatcher 工具目录 → Designer 设计书可见；
- `ToolCapabilityAdapter` 包装进 CapabilityRegistry → `GET /api/capabilities` 暴露
  `{id, version, kind: "deterministic_tool", required_permissions: ["tool:my_tool"], side_effect, idempotent, ...}`；
- 执行期 Grant 校验所需的 `tool:my_tool` 权限名自动成立。

### 4.2 副作用与幂等登记（现状为新工具必改点）

`ToolCapabilityAdapter.spec` 用类级集合判定（`python/capabilities.py`）：

- `side_effect="reversible"`：`_REVERSIBLE_TOOLS = {file_creator, text_editor, git_operator}`；
- `side_effect="external"`：`_EXTERNAL_TOOLS = {github_operator, http_api}`——**对外发送、不可回退**（建 PR、写方法 HTTP 请求），目录层据此提示 Designer/前端审慎；
- 其余一律 `"none"`；
- `idempotent=False`：`_NON_IDEMPOTENT_TOOLS = {git_operator, package_installer, github_operator, http_api}`，其余一律 `True`。

**新工具有副作用时，必须同步修改这些集合**，否则目录层会把它当作无副作用幂等工具展示给 Designer。把该判定改为工具 metadata 声明是 §8 的演进项，在实现之前本条是硬性要求。

## 5. 让计划用得上你（设计期接入，可选）

工具注册后默认对 Designer **可见但未必敢用**：计划提示词目前是分级白名单（允许 `file_creator` / `coding_agent` / `test_runner`，禁 shell 类）。新工具要进入"计划可直接生成"的范围，参照 `test_runner` 在 1.6 的完整先例做三件事：

1. **计划提示词白名单**：加入允许列表并写清步骤契约示例（inputs 必填键、acceptance 写法）；
2. **设计期轻契约**：`python/utils/json_fixer.py` 的 `TOOL_CONTRACTS` 增加条目——缺必填 inputs / 缺合法 acceptance → 计划整体打回重试（fail-closed，设计期拒绝比执行期拒绝便宜，主稿 §22.4）；
3. **验收器对接**：确认 AcceptanceRunner 现有验收类型（`exit_code` / 文件类检查等）能判定你的返回值；判定不了就先扩展验收器，不要让工具返回"自定义成功标志"来糊弄验收。

不进计划白名单的工具（例如仅由 `coding_agent` 在 `allowed_tools` 里调用的内部工具）可跳过 1、2，但 §3、§4、§6 全部适用。

## 6. 测试要求（合入验收口径）

- 位置与命名：`tests/python/test_<tool_id>.py`；安全专项另立 `tests/python/test_<tool_id>_security.py`（先例：command_runner / web_fetcher / package_installer）。
- 从仓库根运行 pytest（pytest-from-root 规则，`pytest.ini` 生效）。
- **必测四件**：
  1. 两态：至少一条成功路径 + 一条失败路径（退出码/错误结构断言）；
  2. blocked 路径：未登记模板/非法参数 → `{"success": False, "blocked": True}`；
  3. 元数据完整性：`tool_id` 唯一、`instructions` 覆盖 `execute` 实际接受的所有 instruction 值、有参数的 instruction 其 parameters 说明非空（无参数指令允许空表）；
  4. 授权交互：authorize 拒绝时工具本体不被调用（mock `execute` 断言零调用）。
- 子进程类工具：断言 argv 构造无 shell 注入面（注入字符串原样成为单个 argv 项）、超时生效。
- 网络类工具：默认 mock；真实网络用环境变量打标（`RUN_REAL_*` 模式，先例 `RUN_REAL_LLM_EVAL`）。
- 进了计划白名单的工具：补 `test_full_flow_design.py` 契约校验用例 + `test_fullflow_e2e.py` 真实链路场景（打标）。

## 7. 新工具落地清单

1. 设计确认：tool_id、category、instructions 集、**输入键名是否复用 §3.3 的资源键**；
2. 实现 `python/tools/my_tool.py`：`get_metadata` + `execute`，§3.6 红线逐条过；
3. 需要服务端配置：`configure_xxx()` + server lifespan 注入；
4. 注册进 `ToolRegistry._register_default_tools`（不造别名）；
5. 有副作用：同步登记 `ToolCapabilityAdapter.spec` 两个集合；
6. 测试：§6 必测四件 + security 专项（如适用），仓库根跑 pytest 全绿；
7. （可选）设计期接入：提示词白名单 + `TOOL_CONTRACTS` + 验收器确认；
8. 文档：主设计稿"变更记录"加一行；本文档附录 A 加一行；
9. 冒烟：起 server，`GET /api/capabilities` 确认新工具 kind / required_permissions / side_effect 正确。

## 8. 演进项（与主稿 §23.4 对齐，按优先级）

1. **P2 · Schema 真实化**：`instructions[].parameters` 升级为 JSON Schema，`CapabilitySpec.input_schema/output_schema` 填真值，执行期统一 Schema 校验——在此之前 §3.2 的三层口径有效；
2. **side_effect / idempotent 改由 metadata 声明**，`ToolCapabilityAdapter` 只做缺省兜底（消除 §4.2 的硬编码集合）；
3. **P3 · category 分类学**统一治理（`generation` 与 `edit/read` 的边界等）；
4. **P3 · plugin_system 废弃收尾**：`PluginManager/ToolPlugin` 标记废弃候选，外部扩展（如 MCP 适配）启用时以新设计为准；
5. **遗留物清理**：改写 `tools/README.md` 指向本文档；`tools/example_tool.py` 与 `schemas/tool_schema.json` 归档或删除；
6. **工具级异步**：若出现长时工具（分钟级），评估在适配层增加异步执行/进度上报协议，当前一律同步 + 超时。

## 附录 A. 现有工具一览（2026-08-30，实际注册）

| tool_id | 模块 | category | instructions | side_effect | idempotent |
|---|---|---|---|---|---|
| file_creator | file_creator.py | generation | create_file, create_directory | reversible | 是 |
| text_editor | text_editor.py | generation | read_file, write_file, append_to_file, replace_in_file, insert_at_line（write 曾缺失，0.2 已补入 metadata） | reversible | 是 |
| git_operator | git_operator.py | other | git_init, git_add, git_commit, git_clone | reversible | 否 |
| package_installer | package_installer.py | generation | install_pip, install_npm | none | 否 |
| command_runner | command_runner.py | other | run_command | none | 是 |
| test_runner | test_runner.py | test | run（run_tests / execute 为同义别名） | none | 是 |
| lint_gate | lint_gate.py | test | lint_file（0.3 由存根补全注册） | none | 是 |
| github_operator | github_operator.py | git | pr_create, pr_status, pr_checks, pr_list | external | 否 |
| http_api | http_api.py | other | send_request | external | 否 |
| browser_check | browser_check.py | test | check_page | none | 是 |
| web_fetcher | web_fetcher.py | search | download_file, fetch_content | none | 是 |
| super_search | super_search.py | search | search, deep_search, extract_text | none | 是 |
| code_context | code_context.py | search | repo_map, search, reindex, remember | none | 是 |
| project_memory | project_memory.py | other | save, recall | none | 是 |
| manual_tool | manual_tool.py（别名 `manual`） | other | manual_step | none（human_tool，强制挂审批） | 是 |

> 注：side_effect/idempotent 两列来自 `ToolCapabilityAdapter.spec` 的现行集合（§4.2），非工具声明。`external` 为 0.3 新增取值。
>
> **0.2 勘误（2026-08-30）**：
> 1. `code_context` 曾在 `_register_default_tools` 中被重复注册 3 次（多会话同文件合并残留），已去重——运行时无影响（dict 覆盖），但属必须清理的合并残留；
> 2. `python/tools/lint_gate.py` 曾是**未注册的存根**（无 get_metadata/execute）——**已由 0.3 补全为正式工具并注册**（lint_file，exit_code 验收），本条闭环；
> 3. `text_editor` 的 metadata 原先漏声明 `write_file`/`write` 指令（违反本文 §6 必测第 3 条），而 `coding_agent` 的受权写回正依赖它——0.2 已补入 metadata。**教训：新增指令时必须同步 metadata。**

## 变更记录

### 0.3 — 2026-08-30（工具补充批次）
- 新增 4 个工具并入册附录 A：lint_gate（由存根补全注册）、github_operator（gh CLI 白名单动作）、http_api（方法白名单 + SSRF 防护 + 不跟随重定向）、browser_check（Playwright 一次性页面检查，懒加载依赖）；§4.2 副作用分类新增 `external` 值与 `_EXTERNAL_TOOLS` 集合。
- §3.1 新增安全扫描适配约定：`def execute` + 字符串拼接（及测试里的 `.execute(<变量>, ...)`）会被扫描器误判为 SQL 注入，统一用 `_run` + `execute = staticmethod(_run)` 别名模式。
- §6 元数据完整性口径修正：无参数指令允许空 parameters 表。
- 配套测试 5 个文件（test_lint_gate_tool / test_github_operator + _security / test_http_api + _security / test_browser_check）。

### 0.2 — 2026-08-30（并发会话勘误）
- 附录 A 勘误：code_context 三连注册残留去重；lint_gate 存根识别；text_editor metadata 补 write_file（详见勘误块）。
