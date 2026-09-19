# 模型配置界面与保存契约

更新：2026-09-19（模型工作台 v2，主从布局）

## 使用方式

1. 打开“模型配置”。左栏是提供商列表（状态点 = 密钥已注入 / 未检测到密钥，副信息 = 被多少角色使用、已缓存多少模型），右栏是选中提供商的详情，下方是“角色路由”表。顶部搜索同时过滤提供商列表与角色表（角色名、模型 ID、提供商、端点）。
2. 新增提供商：点“+ 添加提供商”，从内置预设（OpenAI、DeepSeek、Moonshot、智谱、阿里云百炼、硅基流动、阶跃星辰、火山方舟、MiniMax、OpenRouter、Groq、Ollama、LM Studio、vLLM 等 20 个 OpenAI 兼容端点）一键带入标识与 Base URL，或选“自定义”手填；标识规则不变（字母开头，最多 64 位，不允许大小写重复）。
3. 在详情面板填 API Key 并点“保存密钥”（仍只走 `POST /api/config/key`，DPAPI 加密、不回显；环境变量 `EAS_LLM_API_KEY_<提供商大写标识>` 优先），修改 Base URL 后点“保存并检测 / 获取模型列表”：页面先保存配置草稿，再保存待提交密钥，最后才对**已保存**端点发起连通性检测，成功后模型目录缓存在后台。无草稿时按钮显示“检测连通性 / 获取模型列表”。
4. 目录里的每个模型都可以直接“分配到角色 ▾”（写入草稿，不立即保存）或“测试模型”（需费用确认）。角色路由表中的模型 ID 输入框也会列出该提供商目录里的候选；模型 ID 依旧可以自由填写，候选只表示“曾被上游列出”，不表示已验证可用。
5. 点击底部“保存配置”。保存只写本地覆盖文件，不调用模型、不自动重启，也不修改当前任务使用的运行时配置。页面提示待重启时，确认所有任务结束后通过项目实际使用的启动方式受控重启后台；旧的 `/api/restart` 只请求退出，页面不调用它。

## 交互与保护

- 卡片按真实 `models` 角色生成，包含专项角色；不再固定六个角色，也不再误读空的 `agents` 配置。
- 页面区分“未保存修改”“已保存、待重启”“密钥已注入”，不把密钥状态冒充连接成功。
- 离开页面、关闭/刷新浏览器及丢弃草稿有未保存保护；重新加载失败保留草稿。
- 保存时锁定编辑，失败或版本冲突保留草稿。旧后台不支持 schema 2 时界面只读，避免表单提交后被旧接口静默忽略。
- URL 必须为 HTTP(S)，禁止内嵌用户名/密码、查询参数、片段与空白。支持本地模型的 HTTP 端点；保存时不进行网络请求，连通性须在“上游检测”中单独点击。
- 新增标识使用字母开头，可含数字、下划线、短横线，最多 64 位；不允许大小写重复。新提供商被主模型或备用模型引用时不能撤回。
- 已保存提供商不提供删除按钮：模板与本地配置采用递归合并，直接省略不能删除模板项，且可能破坏备用模型依赖。
- 既有备用模型可以修改，但不在页面新增/删除备用策略。温度、token 上限、并发参数和其他高级字段保留；本轮仅编辑 provider/model 与 Base URL。
- `EAS_LLM_BASE_URL_<提供商大写标识>` 覆盖文件端点时，页面明确提示环境变量优先。
- 响应式布局适配桌面与窄屏；标签切换支持方向键/Home/End，丢弃确认支持 Escape 与焦点循环。

## 后端 API

`GET /api/config` 返回 `success` 与非敏感 `config`：

- `schema_version: 2`
- `probe_version: 1`：支持独立上游检测；前端在旧后台缺少该字段时禁用检测按钮。
- `base_urls`：模板与本地覆盖合并后的文件端点
- `models`：角色的 primary/fallback；仅投影 provider、model、temperature、max_tokens
- `agents`：只读兼容别名，等同 `models`
- `api_keys_configured`：环境变量或加密存储中密钥的存在状态，永不返回密钥值
- `api_keys_persisted`：仅由加密存储提供（未被环境变量覆盖）的密钥标记
- `environment_overrides`：被环境变量覆盖端点的提供商标识
- `revision`：模板与本地文件内容的版本摘要
- `requires_restart`：所支持的模型/端点配置是否与运行配置不同（考虑端点环境变量覆盖）

`POST /api/config` 示例（仅演示，不要把版本占位符用于真实请求）：

```json
{
  "revision": "GET 返回的 revision",
  "base_urls": {"example": "https://api.example.com/v1"},
  "models": {
    "designer": {
      "primary": {"provider": "example", "model": "your-model-id"}
    }
  }
}
```

所有保存均须携带 revision；不匹配或缺失返回 409。该示例中的 base_urls 必须包含实际配置中的全部既有提供商。models 支持按角色更新；未提交的角色、备用字段及高级参数保留。

非空 `api_keys` 请求在配置保存接口被明确拒绝：密钥只走 `POST /api/config/key`（单提供商、鉴权同其它端点、值不回显），
不会再出现“页面收下密钥但后台忽略”的假成功。配置读写与检测端点均使用原鉴权依赖。

### 密钥存储（`POST /api/config/key`）

- 请求体 `{provider, value}`；`value` 为空字符串表示删除该提供商的本地密钥。无法读取的加密文件只会让状态显示为未保存，不会让配置页报错。
- 加密使用 Windows DPAPI（`CryptProtectData`，禁止 UI 提示），密钥与当前 Windows 账户/机器绑定；换账户或换机器无法解密。
- 明文绝不写入 `models.yaml`、`models.local.yaml` 或从 API/日志返回；运行时优先级为环境变量 > 加密存储。
- 非 Windows 平台保存会明确失败（500），服务继续只依赖环境变量，不做明文降级。

保存只更新 `models.local.yaml`，使用同目录临时文件、flush/fsync 和原子替换；故障时清理临时文件。已有其他本地字段保留，不新增请求中的密钥，不改跟踪模板、不修改 `state.config`，也不触发服务重启。配置读取错误不回显 YAML 内容。

并发保护使用进程内锁和文件 revision，不能视为跨多个服务进程/外部编辑器的完整文件事务锁。继续遵循单实例维护原则。

## 验证与演示边界

- 新增配置后端测试使用临时 YAML 与独立 FastAPI 应用；不运行真实服务 lifespan，不读写真实配置。
- 新增 React/客户端测试 mock API/fetch，验证模型提交、冲突、草稿、密钥只读、校验与旧版本处理。
- 全量 Python 回归必须继续在不含真实数据库、token 和本地覆盖配置的源码副本运行。
- 交互预览为相同组件配合虚构模型、内存 API 的隔离演示，保存不影响真实项目；它不是已经执行过真实配置保存的证据。
- 本轮验证结果与保护检查记录见 `out/model-config-20260913/`。

## 明暗主题与上游检测补充（2026-09-13）

### 页面

- ConfigView 跟随实际 App 的 `data-theme`，使用局部语义色；亮色使用白色卡片、深色正文与柔和紫色强调，暗色保持深色表面、浅色正文。
- 卡片网格依据主内容容器宽度，而不是整窗宽度决定列数，避免侧栏与 Copilot 打开时挤压字段。此次验证包含实际 App、Sidebar、AiPanel、LogPanel 和全局 index.css，而非孤立深色组件。
- “上游检测”明确分为“检测连通性 / 获取模型”和“测试模型”。需要已保存配置；草稿未保存时不测试旧端点。切换配置或离开页面取消前端等待，忽略迟到的旧结果。

### 请求与边界

认证接口 `POST /api/config/probe` 接收 `provider`、当前 `revision`、`mode`（`connectivity` / `model`）。模型测试另需 `model` 和明确的 `acknowledge_cost: true`。

- 连通性：只向已保存提供商的 `<base>/models` 发 GET，不发推理。最多显示 200 个去重模型 ID；点选 ID 只填入测试框，不保存、不自动推理。
- 模型测试：费用确认后向 `<base>/chat/completions` 发送一次固定文本 `Reply with OK.`，`max_tokens: 16`、`stream: false`；不附带业务数据、工具定义或任意提示词。
- 接口可达、列表可见、文本推理通过是不同结论。404 的 `/models` 不代表已知模型不可推理；空文本不能证明模型不可用，推理模型可能需要更大输出预算。
- 此最小文本测试不证明视觉、语音、结构化输出、工具调用或完整任务参数兼容性；也不是实时监控或持续健康承诺。
- 根据现有服务加载器，凭据仅取 `EAS_LLM_API_KEY_<PROVIDER>`；不读取 YAML 中历史密钥或旧 runtime key。支持端点环境覆盖；已有本地 HTTP 模型端点允许诊断，不接受浏览器额外提交的 URL、key 或 prompt。
- 总超时 20 秒，连接 5 秒、读等待 12 秒，响应读取上限 256 KiB；同一进程同时只允许一次检测。不重试、不跟随重定向、不继承系统代理；依赖代理的网络须另行部署支持。
- 区分鉴权、权限、404、限流/配额、协议拒绝、重定向、上游错误、连接/DNS/TLS、读取超时、无效 JSON 和空文本。已收到 HTTP 头后的超时仍保留可达状态。
- 不回传上游推理正文、错误体、请求 ID 或密钥；目录中包含当前密钥的异常 ID 也被过滤。
- 超时、关页或取消等待不保证停止上游执行/计费；不自动补发。费用没有固定金额承诺，16 tokens 是请求输出上限，不是账单总量上限。

### 最终验证与运行状态

- 前端 **85 passed / 15 files**，TypeScript 与生产构建通过；新增 7 个面板测试及 2 个 transport 测试。
- 脱敏源码副本 Python **1060 passed / 9 skipped / 2 deselected**；新增 29 个 MockTransport 后端用例。未使用真实数据库运行测试，未发真实上游请求。
- Chromium 实际应用外壳中，亮/暗主题 × 模型/提供商页、窄内容区、确认/取消、模拟成功/失败均验证；无页面 JS 错误，内容区无横向溢出。
- 5 个受保护原文件 SHA256 与首轮维护前一致。首次只读检查旧进程尚无 probe_version；获得空闲受控重启许可后，复核发现运行进程已更换且返回 **probe_version: 1 / 12 roles**，因此没有重复发起重启。
- 上述运行状态仅为本次只读复核时的状态；不代表真实上游已检测通过。刷新页面后由用户自行选择是否检测，模型推理始终需要费用确认。
- 证据：`out/model-probes-20260913/`、`out/model-probes-verification-full-20260913-191646/`；实际外壳浏览器截图位于 Arena 隔离预览工作区，使用模拟数据，不能当作生产模型调用证据。


## 已保存中文提供商兼容修复（2026-09-13）

- 修复已保存的“国模”检测被 ASCII 名称校验拒绝的问题：检测允许 Unicode 字母开头、随后为 Unicode 字母/数字/下划线/短横线的 1–64 位标识；仍必须精确匹配已保存提供商，不接受任意目的地址。空白、控制字符、路径符号及双向控制符继续拒绝。
- 同步修复保存接口：已有安全中文标识可以原样保留，不再阻断其他模型配置保存；不重命名、不删除提供商或改写模型依赖。新建提供商仍遵守页面已有的 ASCII 命名规则。
- 当前后台 OpenAPI 已显示 Unicode 校验规则。获准后仅对“国模”进行一次真实连通性复测：约 1.47 秒，已到达上游，返回 HTTP 401；本地 422 已消除，当前后台未检测到其环境密钥。没有发送模型推理请求或真实配置保存。
- 本轮新增 17 个后端用例和 1 个前端用例，覆盖 Unicode 环境变量取钥、已保存端点解析、未保存标识拒绝、非法字符、旧 ID 与模型依赖保留。定向后端 100 项通过；前端 86 项通过，TypeScript/生产构建通过。测试使用脱敏副本、MockTransport 或 mock API。
- 实际密钥状态：BBAPI、HEJUAPI、NEWAPI、国模未检测到后台环境注入；STEPFUN 已检测到。不要将真实密钥发到聊天或写进 models.yaml/models.local.yaml；应通过后台使用的环境/密钥管理方式注入对应 `EAS_LLM_API_KEY_<提供商大写标识>`。
- hejuapi 保存地址为 `https://www.hejuapi.com/v1`，无端点环境覆盖。此前 `/v1/models` 返回 HTTP 404；公开页面不足以确认正确替代地址，因此保持原值，需供应商控制台/接入文档确认。404 不证明推理不可用。
- 真实复测与文件保护证据：`out/upstream-connectivity-20260913-200100.json`。五项受保护文件在复测前后保持不变；没有主动重启、执行或归档历史任务。

- 后续全量回归完成：**1077 passed / 9 skipped / 2 deselected**，证据 `out/model-probes-verification-full-20260913-200034/`。
- 额外只读检查 Windows 用户/系统环境及维护进程，五个提供商均未发现对应密钥变量；这不等同于后台进程环境。后台仍报告 STEPFUN 已注入，其他四家未注入，因此应以后台状态为准。检查仅记录布尔值，不保存或回显密钥，证据 `out/model-probes-20260913/key-presence-only.json`。

## 模型工作台 v2：主从布局与模型目录（2026-09-19）

### 设计来源

参考 Cherry Studio“模型服务”与 LobeChat“Provider Settings”的共同做法：左侧提供商列表 + 右侧单提供商详情；Key / 地址 / 获取模型列表 / 连通性检测集中在一张卡片；获取到的模型可直接启用或分配；内置常见服务商的地址预设。旧版把“模型分配”“服务提供商”“上游检测”分在三个区域，新增一个提供商并投入使用需要来回切换约 8 步，且检测得到的模型列表无法用于角色分配；本轮以此为主要优化目标。

### 页面结构（`frontend/src/components/ConfigView.tsx` + `config/`）

- `ConfigView.tsx`：状态编排（草稿 / 基线 / 待保存密钥 / 目录 / 选中提供商）、统一保存栏、重新加载确认、离开页面保护。
- `config/ProviderPanel.tsx`：单提供商详情，含密钥输入（显示/隐藏、保存、撤回新增）、Base URL、“保存并检测”、状态徽标、模型目录列表（分配到角色、测试模型、清除目录）。
- `config/RoleRoutingTable.tsx`：角色 × 主/备用模型的紧凑表格；提供商为下拉，模型 ID 为可搜索组合框（`config/ModelPicker.tsx`），候选 = 该提供商目录 ∪ 配置中已用 ID，允许自由输入。
- `config/AddProviderDialog.tsx` + `config/providerPresets.ts`：新增提供商对话框与地址预设（只含公开的 Base URL 与获取密钥链接，不含任何凭据）。
- `config/ConfirmDialog.tsx`、`config/shared.ts`：确认对话框、校验/规范化工具（URL 规则与标识规则与旧版一致）。
- 样式 `ConfigView.css` 全部重写，仍跟随 `data-theme`，用容器查询在 960px / 560px 折叠为单列。

### 后端：模型目录缓存

- `python/api/model_catalog.py`：连通性检测成功后，把去重后的模型 ID（最多 200 个）、抓取时间与当时端点写入 `data/model_catalog.json`；写入使用临时文件 + 原子替换，只存模型 ID，不存密钥或上游错误体。
- `GET /api/config/catalog`：返回 `{success, catalog: {provider: {models, fetched_at, base_url, stale}}}`；`stale` 表示当时抓取的端点与现在生效的端点不同（含环境变量覆盖），页面提示“列表可能过期”。读取不会联系上游。
- `DELETE /api/config/catalog/{provider}`：清除单个提供商的缓存；标识校验与检测接口一致。
- `POST /api/config/probe` 连通性成功响应新增 `catalog` 字段（本次写入的目录条目）；`GET /api/config` 新增 `catalog_version: 1`，缺少该字段的旧后台前端不显示目录功能。
- 未放宽的边界：检测仍只针对已保存提供商，不接受浏览器提交的 URL / 密钥 / 提示词；模型推理测试仍需 `acknowledge_cost: true`；密钥仍只经 `POST /api/config/key`；配置保存仍需 revision，冲突 409；不自动重启。

### “保存并检测”的顺序与失败处理

1. 草稿有改动 → `POST /api/config`（携带 revision，不含密钥）；失败则停止，草稿保留，密钥不提交。
2. 当前提供商有待保存密钥 → `POST /api/config/key`；失败则停止，已保存的配置不回滚，密钥输入框内容保留。
3. 以保存后的 revision 调用 `POST /api/config/probe`（`mode: connectivity`）；配置刷新或切换提供商时取消等待，迟到结果不写入界面。

### 测试

- `ConfigView.test.tsx`（18 项）：保留原 14 项行为契约（版本携带、密钥不回显、无效 URL/空模型禁保存、重复标识、只读旧后台、409 保留草稿、重新加载确认、离开保护、中文提供商不重命名等），新增 4 项：预设一键填入且新提供商保存前不能单独存密钥；已缓存目录进入角色路由候选并显示在提供商列表；修改端点后目录标记可能过期、“清除目录”调用后台；从模型列表“分配到角色”只改草稿。
- `config/ProviderPanel.test.tsx`（11 项）：移植原 ModelProbePanel 7 项（不自动检测、费用确认、401 不算通过、旧后台禁请求、选模型不自动推理、网络失败可重试、刷新取消等待），新增 4 项：“保存并检测”顺序（配置 → 密钥 → 检测已保存端点）；保存失败或草稿无效时不发出检测；切换提供商丢弃上一个的结果与输入；“保存并检测”返回的新版本不会取消刚发起的检测。
- 后端 `tests/python/test_model_catalog.py`（5 项）：损坏/缺失缓存按空处理；写入时过滤、裁剪并标记 stale；环境变量端点覆盖参与 stale 判定；检测路由写入目录且 `GET /api/config/catalog` 可读回；缓存写入失败不影响检测结果。

### 验证结果

- 前端 `npx vitest run`：**95 passed / 15 files**（原 85 + 本轮净增 10）；`npm run build`（`tsc && vite build`）通过。
- `npx tsc -p tsconfig.app.json --noEmit` 对本轮改动的文件无报错；该命令另报出 5 处**既有**错误（`AuditView.test.tsx`、`ExecutionView(.test).tsx`、`PlanView.tsx`、`Sidebar.tsx`，均未在本轮改动），根 `tsconfig.json` 为 `files: []` 的引用配置，因此 `npm run build` 中的 `tsc` 不会检查它们。建议后续单独处理。
- 后端定向用例 **78 passed**；全量 `tests/python` **1133 passed / 10 skipped**，`test_exec_e2e.py` 的 2 项在全量运行中报 setup error，但单独运行 **2 passed**，属既有的顺序/环境相关问题，与配置或目录改动无关。
- 本轮没有向真实上游发送任何请求，没有保存真实配置或密钥；`data/model_catalog.json` 只会在用户主动点击检测并成功后产生。
