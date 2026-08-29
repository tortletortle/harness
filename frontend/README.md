# EAS 2.0 Web UI

现代化的 Web 界面，用于 EAS 2.0 AI 任务编排系统。

## 快速开始

### 方式 1：一键启动（推荐）

双击运行：
```
启动完整系统.bat
```

这会自动启动后端 API 服务器和前端开发服务器。

- 前端地址：http://localhost:3000
- 后端地址：http://localhost:8000

### 方式 2：分别启动

**启动后端：**
```bash
# 方式 A：使用批处理
启动后端.bat

# 方式 B：手动启动
python scripts/start_api.py
```

**启动前端：**
```bash
# 方式 A：使用批处理
启动前端.bat

# 方式 B：手动启动
cd frontend
npm run dev
```

## 功能特性

### 核心功能

1. **输入需求生成设计书**
   - 输入项目需求描述
   - AI 自动生成详细设计书
   - 评估项目难度和技术栈

2. **查看和审批设计书**
   - 查看架构设计详情
   - 查看工具评估结果
   - 批准或拒绝设计书
   - 自动生成执行计划

3. **查看执行步骤**
   - 按阶段查看所有步骤
   - 查看步骤详细信息
   - 查看输入参数和预期输出

4. **执行计划**
   - 一键启动执行
   - 实时日志流
   - 执行进度跟踪

5. **工具管理**
   - 查看所有可用工具
   - 查看工具能力和需求
   - 工具详细信息

6. **历史记录**
   - 查看历史设计书
   - 查看历史执行计划
   - 快速切换项目

7. **API 配置**
   - 配置 API 密钥
   - 配置服务地址
   - 管理多个 API 提供商

8. **实时日志**
   - WebSocket 实时日志流
   - 彩色日志分级
   - 日志筛选和清空

## 技术栈

### 前端
- **框架**: React 18 + TypeScript
- **构建工具**: Vite
- **样式**: TailwindCSS
- **状态管理**: Zustand
- **图标**: Lucide React

### 后端
- **框架**: FastAPI
- **WebSocket**: FastAPI WebSocket
- **数据库**: SQLite
- **AI**: 支持多个 LLM 提供商

## 项目结构

```
frontend/
├── src/
│   ├── api/           # API 客户端
│   ├── components/    # React 组件
│   ├── stores/        # Zustand 状态管理
│   ├── types/         # TypeScript 类型
│   ├── App.tsx        # 主应用
│   └── main.tsx       # 入口文件
├── public/
├── index.html
└── package.json

python/api/
├── __init__.py
└── server.py          # FastAPI 服务器

scripts/
└── start_api.py       # 启动脚本
```

## API 端点

### 设计书相关
- `POST /api/design` - 生成设计书
- `GET /api/designs` - 获取设计书列表
- `GET /api/design/{id}` - 获取单个设计书
- `POST /api/design/{id}/approve` - 审批设计书

### 执行计划相关
- `GET /api/plans` - 获取执行计划列表
- `GET /api/plan/{id}` - 获取单个执行计划
- `POST /api/plan/{id}/execute` - 执行计划
- `POST /api/plan/{id}/pause` - 暂停（写入恢复断点）
- `POST /api/plan/{id}/resume` - 从断点恢复
- `POST /api/plan/{id}/skip-step/{step_id}` - 跳过步骤
- `POST /api/plan/{id}/rollback` - 回滚到指定步骤（新分支）

### 其他
- `GET /api/audit-logs` - 审计日志（工具调用记录）
- `GET /api/tools` - 获取工具列表
- `GET /api/config` / `POST /api/config` - 配置读写
- `POST /api/chat` - AI 助手对话
- `POST /api/restart` - 重启后端
- `GET /api/status` - 系统状态
- `WS /ws/logs?token=` - WebSocket 实时日志

> 除 `GET /` 外所有端点需要 token（`Authorization: Bearer <token>` 或
> `X-API-Token`；WebSocket 用 `?token=`）。token 在 `config/api_token`。

## 开发说明

### 安装依赖

```bash
cd frontend
npm install
```

### 开发模式

```bash
npm run dev
```

### 构建生产版本

```bash
npm run build
```

### 预览生产版本

```bash
npm run preview
```

## 界面预览

### 主界面特点
- **Zed 风格设计**：简洁、专业、高效
- **暗色主题**：减少眼睛疲劳
- **响应式布局**：适配不同屏幕尺寸
- **实时反馈**：WebSocket 实时日志
- **流畅交互**：快速响应，无卡顿

### 组件说明

1. **Sidebar** - 侧边栏导航
2. **InputView** - 需求输入页
3. **DesignView** - 设计书审阅页
4. **PlanView** - 执行计划页
5. **ExecutionView** - 执行控制页（暂停/恢复/跳步/回滚）
6. **HistoryView** - 历史记录页
7. **ToolsView** - 工具管理页
8. **ConfigView** - 配置页
9. **AuditView** - 审计日志页
10. **LogPanel** - 实时日志面板
11. **ToastHub** - 全局错误/警告提示条

## 注意事项

1. **首次启动需要安装依赖**
   ```bash
   cd frontend
   npm install
   ```

2. **确保后端服务正在运行**
   - 前端需要连接到 `http://localhost:8000`

3. **配置 API 密钥**
   - 在配置页面设置 API 密钥
   - 修改后需要重启后端服务

4. **端口占用**
   - 前端默认使用 3000 端口
   - 后端默认使用 8000 端口
   - 如有冲突可在配置中修改

## 故障排除

### 前端无法连接后端
- 检查后端是否启动：访问 http://localhost:8000
- 检查防火墙设置
- 查看浏览器控制台错误信息

### WebSocket 连接失败
- 确认后端服务正常运行
- 检查网络连接
- 查看后端日志

### 样式显示异常
- 清除浏览器缓存
- 重新构建：`npm run build`
- 检查 TailwindCSS 配置

## 后续计划

- [x] 用户认证（后端 token 鉴权已实现，见 docs/API.md）
- [ ] 计划状态 WebSocket 推送（替代 2 秒轮询）
- [ ] 支持多项目并行
- [ ] 添加更多可视化图表
- [ ] 支持导出报告
- [ ] 添加暗/亮主题切换
- [ ] 移动端适配优化
