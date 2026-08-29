import React, { useState, useEffect } from 'react';
import {
  Zap,
  Activity,
  Wrench,
  Cpu,
  Play,
  Pause,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  FileText,
  Search,
  Sparkles,
  Volume2,
  Code2,
  Layers,
  BarChart3,
  ListFilter,
  Grid,
  List,
  ChevronRight,
  ChevronDown,
  Terminal,
  Brain,
  ShieldAlert,
  Send,
  X,
  Plus,
  RefreshCw,
  FolderTree,
  FileCode,
  Layout,
  Maximize2,
  Split,
  Eye,
  PanelLeft,
  PanelLeftClose,
  Download,
  Key,
  Copy,
  Check
} from 'lucide-react';

// 主题定义：包含“灰原哀 (Ai Haibara)”特色主题
const THEMES = {
  haibara: {
    id: 'haibara',
    name: '🧪 灰原哀 (Ai Haibara)',
    bg: 'bg-[#1a0b12]',
    headerBg: 'bg-[#2a101d]/90',
    cardBg: 'bg-[#260e1a]/80',
    cardBorder: 'border-rose-900/40',
    textPrimary: 'text-rose-100',
    textSecondary: 'text-rose-300/70',
    accent: 'bg-rose-600 hover:bg-rose-500 text-white',
    accentGlow: 'shadow-[0_0_15px_rgba(225,29,72,0.3)]',
    badge: 'bg-rose-950/80 text-rose-300 border-rose-800/50',
    highlight: 'text-cyan-400',
    subtleBorder: 'border-rose-900/20'
  },
  cyber: {
    id: 'cyber',
    name: '🌌 赛博暗黑 (Cyber Dark)',
    bg: 'bg-slate-950',
    headerBg: 'bg-slate-900/90',
    cardBg: 'bg-slate-900/80',
    cardBorder: 'border-slate-800',
    textPrimary: 'text-slate-100',
    textSecondary: 'text-slate-400',
    accent: 'bg-indigo-600 hover:bg-indigo-500 text-white',
    accentGlow: 'shadow-[0_0_15px_rgba(99,102,241,0.3)]',
    badge: 'bg-indigo-950/80 text-indigo-300 border-indigo-800/50',
    highlight: 'text-indigo-400',
    subtleBorder: 'border-slate-800/50'
  },
  matrix: {
    id: 'matrix',
    name: '🟢 黑客帝国 (Matrix Emerald)',
    bg: 'bg-black',
    headerBg: 'bg-emerald-950/80',
    cardBg: 'bg-emerald-950/30',
    cardBorder: 'border-emerald-800/50',
    textPrimary: 'text-emerald-100',
    textSecondary: 'text-emerald-400/80',
    accent: 'bg-emerald-600 hover:bg-emerald-500 text-black font-bold',
    accentGlow: 'shadow-[0_0_15px_rgba(16,185,129,0.4)]',
    badge: 'bg-emerald-950 text-emerald-400 border-emerald-700',
    highlight: 'text-emerald-300',
    subtleBorder: 'border-emerald-900/40'
  },
  neon: {
    id: 'neon',
    name: '🔮 霓虹紫 (Neon Purple)',
    bg: 'bg-[#0f071a]',
    headerBg: 'bg-[#1b0d30]/90',
    cardBg: 'bg-[#180a2b]/80',
    cardBorder: 'border-purple-800/40',
    textPrimary: 'text-purple-100',
    textSecondary: 'text-purple-300/70',
    accent: 'bg-purple-600 hover:bg-purple-500 text-white',
    accentGlow: 'shadow-[0_0_15px_rgba(168,85,247,0.4)]',
    badge: 'bg-purple-950 text-purple-300 border-purple-800',
    highlight: 'text-fuchsia-400',
    subtleBorder: 'border-purple-900/30'
  }
};

// IDE 源码模式下的真实项目文件列表
const INITIAL_PROJECT_FILES = {
  'App.jsx': {
    name: 'App.jsx',
    language: 'javascript',
    role: '前端 React 交互系统',
    code: `import React, { useState } from 'react';\nimport { Zap, Activity, Wrench } from 'lucide-react';\n\nexport default function App() {\n  const [status, setStatus] = useState('Executing Step 1-2');\n  return (\n    <div className="p-6 bg-slate-950 text-white min-h-screen">\n      <header className="flex items-center justify-between pb-4 border-b border-slate-800">\n        <h1 className="text-xl font-bold flex items-center space-x-2">\n          <Zap className="text-amber-400" />\n          <span>EAS 2.0 Task Orchestrator</span>\n        </h1>\n        <span className="px-3 py-1 bg-emerald-500/10 text-emerald-400 rounded-full text-xs font-mono border border-emerald-500/20">\n          System Status: {status}\n        </span>\n      </header>\n      <main className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-6">\n        <div className="bg-slate-900/80 p-5 rounded-xl border border-slate-800 shadow-lg">\n          <h2 className="text-sm font-semibold text-slate-400 mb-2">Designer Agent</h2>\n          <p className="text-xs text-slate-300">Phase 2: Generating dynamic steps with check_criteria...</p>\n        </div>\n      </main>\n    </div>\n  );\n}`
  },
  'dispatcher.rs': {
    name: 'dispatcher.rs',
    language: 'rust',
    role: 'Rust 格式检查与路由内核',
    code: `// EAS 2.0 High-Performance Dispatcher Core in Rust\nuse serde::{Deserialize, Serialize};\nuse tokio::sync::mpsc;\n\n#[derive(Debug, Serialize, Deserialize)]\npub struct Step {\n    pub step_id: String,\n    pub tool: String,\n    pub check_criteria: String,\n}\n\npub struct Dispatcher {\n    pub log_sender: mpsc::Sender<String>,\n}\n\nimpl Dispatcher {\n    pub async fn validate_and_route(&self, step: &Step) -> Result<(), String> {\n        println!("[Rust Native Dispatcher] Verifying format for step: {}", step.step_id);\n        if step.check_criteria.is_empty() {\n            return Err("Missing check_criteria validation rule".into());\n        }\n        Ok(())\n    }\n}`
  },
  'designer_agent.py': {
    name: 'designer_agent.py',
    language: 'python',
    role: 'Python Designer 智能体服务',
    code: `# EAS 2.0 Designer Agent Service\nimport json\nfrom typing import Dict, Any\n\nclass DesignerAgent:\n    def __init__(self, model_name: str = "gemini-3-flash"):\n        self.model = model_name\n\n    def generate_phase1_doc(self, user_prompt: str) -> Dict[str, Any]:\n        """生成 Part 1 架构设计与难度评估"""\n        return {\n            "architecture": ["React UI", "Rust Dispatcher", "Python Agents"],\n            "difficulty": "Medium"\n        }\n\n    def generate_phase2_plan(self, design_doc: dict) -> dict:\n        """基于设计书生成带有 check_criteria 的 Step 队列"""\n        return {"plan_id": "plan-001", "segments": []}`
  },
  'tool_manager.py': {
    name: 'tool_manager.py',
    language: 'python',
    role: 'ToolManager 工具质量与复制管理器',
    code: `# ToolManager AI Service\nclass ToolManager:\n    def check_tool_availability(self, tool_id: str) -> dict:\n        """评估工具能力 Schema 与缺失影响"""\n        return {\n            "tool_id": tool_id,\n            "quality_level": "high",\n            "max_instances": 5\n        }`
  },
  'schema.sql': {
    name: 'schema.sql',
    language: 'sql',
    role: 'PostgreSQL 系统底层数据库模型',
    code: `-- EAS 2.0 Database Schema Definition\nCREATE TABLE IF NOT EXISTS designs (\n    design_id VARCHAR(64) PRIMARY KEY,\n    user_request TEXT NOT NULL,\n    part1_design JSONB NOT NULL,\n    part2_tool_assessment JSONB,\n    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n);\n\nCREATE TABLE IF NOT EXISTS steps (\n    step_id VARCHAR(64) PRIMARY KEY,\n    plan_id VARCHAR(64) NOT NULL,\n    order_num INT NOT NULL,\n    tool VARCHAR(64) NOT NULL,\n    check_criteria TEXT NOT NULL,\n    status VARCHAR(32) DEFAULT 'pending'\n);`
  }
};

export default function App() {
  // 全局视觉与模式状态
  const [currentTheme, setCurrentTheme] = useState('haibara');
  const [appMode, setAppMode] = useState('console'); // 'console' | 'editor'
  const [activeTab, setActiveTab] = useState('overview'); // console tabs
  const [toolViewMode, setToolViewMode] = useState('grid'); // 'grid' | 'list'

  // IDE 编辑器模式相关状态
  const [projectFiles, setProjectFiles] = useState(INITIAL_PROJECT_FILES);
  const [activeEditorFile, setActiveEditorFile] = useState('App.jsx');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [previewTab, setPreviewTab] = useState('split'); // 'split' | 'code' | 'preview'
  const [isHotReloading, setIsHotReloading] = useState(false);
  const [copiedCode, setCopiedCode] = useState(false);

  // EAS 2.0 运行逻辑状态
  const [isPaused, setIsPaused] = useState(false);
  const [showInterventionModal, setShowInterventionModal] = useState(false);
  const [showKnowledgeDrawer, setShowKnowledgeDrawer] = useState(false);

  // Gemini & Knowledge AI 状态
  const [apiKey, setApiKey] = useState('');
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [enableGrounding, setEnableGrounding] = useState(true);
  const [knowledgeChat, setKnowledgeChat] = useState([
    {
      role: 'assistant',
      text: '你好！我是 EAS 2.0 驻场 Knowledge AI 助手。我具备只读 Context 隔离特性，可实时解答架构调度、日志流或工具评级问题。',
      citations: []
    }
  ]);
  const [isAIThinking, setIsAIThinking] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);

  // Level 1-4 诊断状态
  const [issueDescription, setIssueDescription] = useState('');
  const [aiDiagnostic, setAiDiagnostic] = useState(null);
  const [isDiagnosing, setIsDiagnosing] = useState(false);

  // 当前主题 Token 引用
  const theme = THEMES[currentTheme];

  const handleCodeChange = (newCode) => {
    setProjectFiles(prev => ({
      ...prev,
      [activeEditorFile]: {
        ...prev[activeEditorFile],
        code: newCode
      }
    }));
  };

  const handleHotReload = () => {
    setIsHotReloading(true);
    setTimeout(() => {
      setIsHotReloading(false);
    }, 600);
  };

  const handleCopyCode = () => {
    navigator.clipboard.writeText(projectFiles[activeEditorFile].code);
    setCopiedCode(true);
    setTimeout(() => setCopiedCode(false), 2000);
  };

  const handleAIDiagnose = () => {
    if (!issueDescription.trim()) return;
    setIsDiagnosing(true);
    setTimeout(() => {
      setIsDiagnosing(false);
      setAiDiagnostic({
        level: 'Level 3 (局部问题)',
        recommendation: '检测到工具配置文件缺失。建议通过 Designer 动态插入补全步骤，无需作废整个 Plan。',
        action: 'Dynamic Step Insertion'
      });
    }, 1000);
  };

  const handleAskKnowledge = () => {
    if (!knowledgeQuery.trim()) return;
    const userMsg = knowledgeQuery;
    setKnowledgeChat(prev => [...prev, { role: 'user', text: userMsg }]);
    setKnowledgeQuery('');
    setIsAIThinking(true);

    setTimeout(() => {
      setIsAIThinking(false);
      setKnowledgeChat(prev => [
        ...prev,
        {
          role: 'assistant',
          text: `针对您的问题：“${userMsg}”，根据 EAS 2.0 设计文档，Rust Dispatcher 负责纯机械格式检查与路由，不引入 LLM 幻觉。当前 Step 1-2 的 check_criteria 检查完全通过。`,
          citations: ['https://eas2.0.internal/docs/dispatcher-rust-spec']
        }
      ]);
    }, 1200);
  };

  return (
    <div className={`min-h-screen ${theme.bg} ${theme.textPrimary} font-sans transition-colors duration-300 pb-12`}>
      
      {/* ================= 1. 顶部全局置顶导航栏 (Sticky Header) ================= */}
      <div className="sticky top-0 z-40 backdrop-blur-md border-b border-white/10 bg-black/40">
        <header className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          
          {/* 左侧 Logo & 系统标识 */}
          <div className="flex items-center space-x-3">
            <div className={`p-2 rounded-xl ${theme.accent} ${theme.accentGlow} transition-transform hover:scale-105`}>
              <Zap className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-extrabold text-lg tracking-wide bg-clip-text text-transparent bg-gradient-to-r from-white via-slate-200 to-slate-400">
                  EAS 2.0
                </span>
                <span className={`text-[10px] px-2 py-0.5 rounded-full border ${theme.badge} font-mono hidden sm:inline-block`}>
                  Gemini 3 Flash Powered
                </span>
              </div>
              <p className="text-[11px] text-slate-400 hidden md:block">
                用户可控 • 职责分离 • 工具复用 • 动态计划
              </p>
            </div>
          </div>

          {/* 中间：控制台 / IDE 编辑器模式切换 */}
          <div className="flex items-center bg-black/40 p-1 rounded-xl border border-white/10">
            <button
              onClick={() => setAppMode('console')}
              className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                appMode === 'console' ? theme.accent : 'text-slate-400 hover:text-white'
              }`}
            >
              <Layout className="w-3.5 h-3.5" />
              <span>控制台模式</span>
            </button>
            <button
              onClick={() => setAppMode('editor')}
              className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                appMode === 'editor' ? theme.accent : 'text-slate-400 hover:text-white'
              }`}
            >
              <Code2 className="w-3.5 h-3.5" />
              <span>编辑器模式</span>
            </button>
          </div>

          {/* 右侧工具：主题选择 + Knowledge AI + 暂停干预 */}
          <div className="flex items-center space-x-2">
            {/* 主题选择器 */}
            <select
              value={currentTheme}
              onChange={(e) => setCurrentTheme(e.target.value)}
              className="bg-black/60 text-xs border border-white/10 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-rose-500 cursor-pointer"
            >
              {Object.values(THEMES).map(t => (
                <option key={t.id} value={t.id} className="bg-slate-900 text-white">
                  {t.name}
                </option>
              ))}
            </select>

            {/* 驻场 Knowledge AI Drawer 开关 */}
            <button
              onClick={() => setShowKnowledgeDrawer(true)}
              className="flex items-center space-x-1.5 px-3 py-1.5 bg-indigo-900/40 hover:bg-indigo-900/70 border border-indigo-700/50 rounded-lg text-xs text-indigo-200 transition"
            >
              <Brain className="w-3.5 h-3.5 text-indigo-400" />
              <span className="hidden sm:inline">Knowledge AI</span>
            </button>

            {/* 暂停干预按钮 */}
            <button
              onClick={() => {
                setIsPaused(!isPaused);
                if (!isPaused) setShowInterventionModal(true);
              }}
              className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                isPaused
                  ? 'bg-amber-500 text-black animate-pulse shadow-lg shadow-amber-500/20'
                  : 'bg-rose-950/80 hover:bg-rose-900 text-rose-300 border border-rose-800'
              }`}
            >
              {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              <span>{isPaused ? '恢复执行' : '暂停干预'}</span>
            </button>
          </div>
        </header>

        {/* 控制台二级导航栏（仅在控制台模式下显示） */}
        {appMode === 'console' && (
          <nav className="border-t border-white/10 bg-black/20 backdrop-blur-md overflow-x-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
            <div className="max-w-7xl mx-auto px-4 flex items-center space-x-1 py-1.5 text-xs font-medium">
              {[
                { id: 'overview', label: '架构全景与拓扑', shortLabel: '架构拓扑', icon: Layers },
                { id: 'designer', label: '设计书审阅 (Designer)', shortLabel: '设计书审阅', icon: FileText, badge: '待审阅' },
                { id: 'execution', label: '执行计划与控制', shortLabel: '执行控制', icon: Play, badge: 'Step 1-2' },
                { id: 'tools', label: '智能工具库 (ToolManager)', shortLabel: '智能工具', icon: Wrench },
                { id: 'models', label: '模型与成本分析', shortLabel: '模型成本', icon: Cpu }
              ].map(item => {
                const Icon = item.icon;
                const isActive = activeTab === item.id;
                return (
                  <button
                    key={item.id}
                    onClick={() => setActiveTab(item.id)}
                    className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg transition whitespace-nowrap ${
                      isActive ? `${theme.accent} ${theme.accentGlow}` : 'text-slate-400 hover:text-white hover:bg-white/5'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    <span className="hidden md:inline">{item.label}</span>
                    <span className="md:hidden">{item.shortLabel}</span>
                    {item.badge && (
                      <span className="ml-1 text-[10px] px-1.5 py-0.2 bg-amber-500/20 text-amber-300 rounded border border-amber-500/30">
                        {item.badge}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </nav>
        )}
      </div>

      {/* ================= 2. 主体视图渲染区域 ================= */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 pt-6">

        {/* ---------------- 模式 A: IDE 代码模式 ---------------- */}
        {appMode === 'editor' && (
          <div className="space-y-4">
            
            {/* IDE 标题与操作状态栏 */}
            <div className={`${theme.cardBg} border ${theme.cardBorder} p-4 rounded-xl flex items-center justify-between shadow-xl backdrop-blur-md`}>
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-purple-500/10 rounded-lg border border-purple-500/20">
                  <Code2 className="w-5 h-5 text-purple-400" />
                </div>
                <div>
                  <h2 className="text-base font-bold text-white flex items-center space-x-2">
                    <span>EAS 2.0 项目源码 IDE & 实时动态加载沙盒</span>
                  </h2>
                  <p className="text-xs text-slate-400">
                    正在直接编辑当前系统的工程文件（包含 React UI 前端、Rust 调度内核、Python AI 模块与 SQL Schema）。修改将触发实时热重载动态渲染！
                  </p>
                </div>
              </div>

              {/* 视口模式选择器：分屏 / 纯代码 / 纯预览 */}
              <div className="flex items-center space-x-2">
                <div className="bg-black/40 p-1 rounded-lg border border-white/10 flex items-center space-x-1 text-xs">
                  <button
                    onClick={() => setPreviewTab('split')}
                    className={`px-2.5 py-1 rounded transition flex items-center space-x-1 ${
                      previewTab === 'split' ? theme.accent : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    <Split className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">分屏 (Code + Preview)</span>
                  </button>
                  <button
                    onClick={() => setPreviewTab('code')}
                    className={`px-2.5 py-1 rounded transition flex items-center space-x-1 ${
                      previewTab === 'code' ? theme.accent : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    <Code2 className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">纯代码视图</span>
                  </button>
                  <button
                    onClick={() => setPreviewTab('preview')}
                    className={`px-2.5 py-1 rounded transition flex items-center space-x-1 ${
                      previewTab === 'preview' ? theme.accent : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    <Eye className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">纯实时渲染</span>
                  </button>
                </div>

                <button
                  onClick={handleCopyCode}
                  className="px-3 py-1.5 bg-white/5 hover:bg-white/10 border border-white/10 text-slate-300 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition"
                >
                  {copiedCode ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copiedCode ? '已复制' : '复制代码'}</span>
                </button>

                <button
                  onClick={handleHotReload}
                  className={`px-3 py-1.5 ${theme.accent} ${theme.accentGlow} rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition`}
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${isHotReloading ? 'animate-spin' : ''}`} />
                  <span>热重载并更新项目</span>
                </button>
              </div>
            </div>

            {/* IDE 主工作区：左侧资源管理器侧边栏 + 中间代码编辑器 + 右侧实时预览 */}
            <div className="flex gap-4 min-h-[620px] items-stretch">
              
              {/* 最左侧：可折叠 IDE 文件资源管理器侧边栏 */}
              <div
                className={`transition-all duration-300 ${theme.cardBg} border ${theme.cardBorder} rounded-xl overflow-hidden flex flex-col backdrop-blur-md ${
                  isSidebarOpen ? 'w-64' : 'w-12'
                }`}
              >
                {/* 侧边栏头部 */}
                <div className="p-3 border-b border-white/10 flex items-center justify-between bg-black/30">
                  {isSidebarOpen ? (
                    <div className="flex items-center space-x-2">
                      <FolderTree className="w-4 h-4 text-rose-400" />
                      <span className="text-xs font-bold text-slate-200">项目文件资源管理器</span>
                    </div>
                  ) : null}
                  <button
                    onClick={() => setIsSidebarOpen(!isSidebarOpen)}
                    className="p-1 hover:bg-white/10 rounded text-slate-400 hover:text-white transition mx-auto"
                    title={isSidebarOpen ? '折叠侧边栏' : '展开侧边栏'}
                  >
                    {isSidebarOpen ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeft className="w-4 h-4" />}
                  </button>
                </div>

                {/* 文件树列表 */}
                <div className="p-2 space-y-1 flex-1 overflow-y-auto">
                  {Object.entries(projectFiles).map(([fileName, fileObj]) => {
                    const isSelected = activeEditorFile === fileName;
                    return (
                      <button
                        key={fileName}
                        onClick={() => setActiveEditorFile(fileName)}
                        className={`w-full flex items-center space-x-2 px-2.5 py-2 rounded-lg text-xs transition text-left ${
                          isSelected
                            ? `${theme.accent} font-semibold shadow-md`
                            : 'text-slate-300 hover:bg-white/5'
                        }`}
                        title={fileObj.role}
                      >
                        <FileCode className={`w-4 h-4 flex-shrink-0 ${isSelected ? 'text-white' : 'text-rose-400'}`} />
                        {isSidebarOpen && (
                          <div className="min-w-0 flex-1">
                            <div className="truncate font-mono">{fileObj.name}</div>
                            <div className="text-[10px] text-slate-400 truncate opacity-80">{fileObj.role}</div>
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* 主工作区列 (代码编辑器 & 预览沙盒) */}
              <div className="flex-1 min-w-0">
                <div className={`grid gap-4 ${previewTab === 'split' ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1'}`}>
                  
                  {/* 左侧工作区列: 代码编辑器 */}
                  {(previewTab === 'split' || previewTab === 'code') && (
                    <div className={`${theme.cardBg} border ${theme.cardBorder} rounded-xl overflow-hidden shadow-2xl flex flex-col backdrop-blur-md`}>
                      <div className="bg-black/40 px-4 py-2 border-b border-white/10 flex items-center justify-between text-xs">
                        <div className="flex items-center space-x-2">
                          {!isSidebarOpen && (
                            <button
                              onClick={() => setIsSidebarOpen(true)}
                              className="mr-1 p-1 bg-white/10 hover:bg-white/20 text-rose-300 rounded transition"
                              title="展开文件资源管理器"
                            >
                              <PanelLeft className="w-3.5 h-3.5" />
                            </button>
                          )}
                          <span className="text-slate-200 font-mono font-semibold">{projectFiles[activeEditorFile].name}</span>
                          <span className="text-[10px] text-slate-400 bg-white/10 px-2 py-0.5 rounded font-mono">
                            {projectFiles[activeEditorFile].role}
                          </span>
                        </div>

                        <div className="flex items-center space-x-2 text-[11px] font-mono text-slate-400">
                          <span>UTF-8</span>
                          <span>{projectFiles[activeEditorFile].language.toUpperCase()}</span>
                        </div>
                      </div>

                      <div className="p-4 flex-1 font-mono text-xs overflow-auto bg-black/60 relative">
                        <textarea
                          value={projectFiles[activeEditorFile].code}
                          onChange={(e) => handleCodeChange(e.target.value)}
                          className="w-full h-full min-h-[500px] bg-transparent text-slate-200 font-mono focus:outline-none resize-none leading-relaxed"
                          spellCheck="false"
                        />
                      </div>
                    </div>
                  )}

                  {/* 右侧工作区列: 实时动态渲染预览沙盒 */}
                  {(previewTab === 'split' || previewTab === 'preview') && (
                    <div className={`${theme.cardBg} border ${theme.cardBorder} rounded-xl overflow-hidden shadow-2xl flex flex-col backdrop-blur-md relative`}>
                      <div className="bg-black/40 px-4 py-2 border-b border-white/10 flex items-center justify-between text-xs">
                        <div className="flex items-center space-x-2">
                          <Eye className="w-3.5 h-3.5 text-cyan-400" />
                          <span className="text-slate-200 font-bold">0ms Live Preview 沙盒</span>
                        </div>
                        <div className="flex items-center space-x-2">
                          <span className="flex h-2 w-2 relative">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                          </span>
                          <span className="text-[11px] text-emerald-400 font-mono">Hot Reload Active</span>
                        </div>
                      </div>

                      {/* 沙盒内容视口 */}
                      <div className="p-6 flex-1 bg-slate-950 flex flex-col justify-between relative overflow-hidden">
                        {isHotReloading && (
                          <div className="absolute inset-0 bg-black/80 backdrop-blur-sm z-20 flex flex-col items-center justify-center space-y-3">
                            <RefreshCw className="w-8 h-8 text-rose-400 animate-spin" />
                            <span className="text-xs text-rose-200 font-mono">正在热编译与更新沙盒模块...</span>
                          </div>
                        )}

                        <div className="space-y-4">
                          <div className="p-4 bg-slate-900/90 rounded-xl border border-slate-800 space-y-3">
                            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                              <span className="text-xs font-bold text-rose-300 font-mono">
                                [Live Sandbox Output] {projectFiles[activeEditorFile].name}
                              </span>
                              <span className="text-[10px] bg-cyan-950 text-cyan-300 border border-cyan-800 px-2 py-0.5 rounded">
                                Compiled
                              </span>
                            </div>
                            <p className="text-xs text-slate-300 leading-relaxed font-mono">
                              已成功编译当前工程文件。修改代码将通过 Babel/Rust Native Live Pipeline 实时挂载并驱动整套 EAS 2.0 调度逻辑。
                            </p>
                          </div>

                          <div className="grid grid-cols-2 gap-3">
                            <div className="p-3 bg-slate-900/50 rounded-lg border border-slate-800">
                              <div className="text-[10px] text-slate-400">Dispatcher Kernel</div>
                              <div className="text-xs font-bold text-emerald-400 mt-1">Rust Native Ready</div>
                            </div>
                            <div className="p-3 bg-slate-900/50 rounded-lg border border-slate-800">
                              <div className="text-[10px] text-slate-400">Context Isolation</div>
                              <div className="text-xs font-bold text-purple-400 mt-1">Active (Zero-Explosion)</div>
                            </div>
                          </div>
                        </div>

                        <div className="mt-6 p-3 bg-black/50 rounded-lg border border-white/5 font-mono text-[11px] text-slate-400 flex items-center justify-between">
                          <span>Build Time: 0.12ms</span>
                          <span>Module Hot Swap: OK</span>
                        </div>
                      </div>
                    </div>
                  )}

                </div>
              </div>

            </div>

          </div>
        )}

        {/* ---------------- 模式 B: 控制台 UI 模式 ---------------- */}
        {appMode === 'console' && (
          <div className="space-y-6">

            {/* TAB 1: 架构全景与拓扑 */}
            {activeTab === 'overview' && (
              <div className="space-y-6">
                
                {/* 4 大核心指标卡片 */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  {[
                    { title: 'Designer 设计方案', val: 'Part 1/2 完成', sub: '难度评估: 中等', icon: Layers, color: 'text-amber-400' },
                    { title: 'ToolManager 评估', val: '80% 完整度', sub: '1 项缺失 (Postgres)', icon: Wrench, color: 'text-cyan-400' },
                    { title: 'Dispatcher 调度器', val: '路由正常', sub: 'Rust Native 高并发', icon: Zap, color: 'text-emerald-400' },
                    { title: 'Executor 边执边检', val: '按规自检', sub: 'check_criteria 监控', icon: CheckCircle2, color: 'text-rose-400' }
                  ].map((card, i) => {
                    const Icon = card.icon;
                    return (
                      <div key={i} className={`${theme.cardBg} border ${theme.cardBorder} p-4 rounded-xl shadow-lg backdrop-blur-md`}>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-slate-400 font-medium">{card.title}</span>
                          <Icon className={`w-4 h-4 ${card.color}`} />
                        </div>
                        <div className="text-lg font-bold mt-2 text-white">{card.val}</div>
                        <div className="text-[11px] text-slate-400 mt-1">{card.sub}</div>
                      </div>
                    );
                  })}
                </div>

                {/* 5 大 Agent 交互拓扑图 */}
                <div className={`${theme.cardBg} border ${theme.cardBorder} p-5 rounded-2xl shadow-2xl backdrop-blur-md space-y-4`}>
                  <div className="flex items-center justify-between border-b border-white/10 pb-3">
                    <h3 className="text-sm font-bold text-white flex items-center space-x-2">
                      <Layers className="w-4 h-4 text-rose-400" />
                      <span>EAS 2.0 职责分离与 Context 隔离协作拓扑</span>
                    </h3>
                    <span className="text-xs text-slate-400">用户可控 + 动态步骤插入</span>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-5 gap-3 pt-2">
                    {[
                      { role: 'User', name: '用户干预层', desc: '设计审阅 & 暂停介入', bg: 'bg-indigo-950/60 border-indigo-700/50 text-indigo-200' },
                      { role: 'Designer', name: '设计师 Agent', desc: 'Phase 1/2 方案与重构', bg: 'bg-amber-950/60 border-amber-700/50 text-amber-200' },
                      { role: 'Dispatcher', name: 'Rust 调度器', desc: '纯逻辑格式校验路由', bg: 'bg-emerald-950/60 border-emerald-700/50 text-emerald-200' },
                      { role: 'ToolManager', name: '工具库 Agent', desc: '能力评估与实例复制', bg: 'bg-cyan-950/60 border-cyan-700/50 text-cyan-200' },
                      { role: 'Executor', name: '边执边检执行器', desc: 'Check Criteria 自检', bg: 'bg-rose-950/60 border-rose-700/50 text-rose-200' }
                    ].map((agent, i) => (
                      <div key={i} className={`p-3.5 rounded-xl border ${agent.bg} space-y-1.5 hover:scale-105 transition-transform`}>
                        <div className="text-[10px] font-mono opacity-80 uppercase tracking-wider">{agent.role}</div>
                        <div className="text-xs font-bold">{agent.name}</div>
                        <div className="text-[11px] opacity-70 leading-snug">{agent.desc}</div>
                      </div>
                    ))}
                  </div>
                </div>

              </div>
            )}

            {/* TAB 2: 设计书审阅 (Designer Phase 1) */}
            {activeTab === 'designer' && (
              <div className="space-y-6">
                <div className={`${theme.cardBg} border ${theme.cardBorder} p-6 rounded-2xl shadow-xl backdrop-blur-md space-y-6`}>
                  <div className="flex items-center justify-between border-b border-white/10 pb-4">
                    <div>
                      <h3 className="text-base font-bold text-white">Designer Phase 1 设计书审阅</h3>
                      <p className="text-xs text-slate-400">架构方案设计 + ToolManager 反馈之工具评估</p>
                    </div>
                    <div className="flex items-center space-x-2">
                      <button className="px-3 py-1.5 bg-white/5 hover:bg-white/10 text-slate-300 rounded-lg text-xs font-semibold border border-white/10">
                        打回要求重写
                      </button>
                      <button
                        onClick={() => setActiveTab('execution')}
                        className={`px-4 py-1.5 ${theme.accent} ${theme.accentGlow} rounded-lg text-xs font-semibold`}
                      >
                        批准设计书并生成 Phase 2 执行计划
                      </button>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Part 1: 设计方案 */}
                    <div className="p-4 bg-black/40 rounded-xl border border-white/10 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-amber-400">Part 1: 架构设计方案</span>
                        <span className="text-[10px] bg-amber-500/20 text-amber-300 px-2 py-0.5 rounded">难度: 中等</span>
                      </div>
                      <div className="text-xs text-slate-300 leading-relaxed space-y-2 font-mono">
                        <p>• 架构组件：React 前端 + FastAPI 后端 + PostgreSQL 数据库</p>
                        <p>• 交互模式：前端 REST API 调用后端，后端进行 DB 持久化。</p>
                      </div>
                    </div>

                    {/* Part 2: 工具评估 */}
                    <div className="p-4 bg-black/40 rounded-xl border border-white/10 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-cyan-400">Part 2: 工具评估 (ToolManager 反馈)</span>
                        <span className="text-[10px] bg-cyan-500/20 text-cyan-300 px-2 py-0.5 rounded">完整度: 80%</span>
                      </div>
                      <div className="text-xs text-slate-300 leading-relaxed space-y-2 font-mono">
                        <p>• 可用工具：react_template_generator (High), fastapi_generator (Medium)</p>
                        <p className="text-rose-400">• 缺失工具：postgres_setup (影响: 需动态插入补全步骤)</p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* TAB 3: 执行计划与控制 (Phase 2 Execution) */}
            {activeTab === 'execution' && (
              <div className="space-y-6">
                <div className={`${theme.cardBg} border ${theme.cardBorder} p-6 rounded-2xl shadow-xl backdrop-blur-md space-y-6`}>
                  <div className="flex items-center justify-between border-b border-white/10 pb-4">
                    <div>
                      <h3 className="text-base font-bold text-white">Phase 2 动态执行计划</h3>
                      <p className="text-xs text-slate-400">Executor 逐步执行 + 边执边检 (check_criteria)</p>
                    </div>
                    <span className="text-xs font-mono px-3 py-1 bg-emerald-500/20 text-emerald-300 rounded-full border border-emerald-500/30">
                      Executing: Step 1-2
                    </span>
                  </div>

                  {/* Step 步骤队列 */}
                  <div className="space-y-3">
                    {[
                      { id: 'step-1-1', name: '创建项目目录结构', tool: 'create_directory', criteria: '目录存在且可写', status: 'completed' },
                      { id: 'step-1-2', name: '安装前端 React 依赖', tool: 'npm_install', criteria: 'node_modules 存在', status: 'executing' },
                      { id: 'step-2-1', name: '生成前端 Dashboard 模板', tool: 'react_template', criteria: '无语法编译错误', status: 'pending' }
                    ].map((step, i) => (
                      <div
                        key={i}
                        className={`p-4 rounded-xl border flex items-center justify-between transition ${
                          step.status === 'executing'
                            ? 'bg-rose-950/40 border-rose-500/50 shadow-lg shadow-rose-950/30'
                            : step.status === 'completed'
                            ? 'bg-emerald-950/20 border-emerald-800/40 opacity-80'
                            : 'bg-black/40 border-white/10'
                        }`}
                      >
                        <div className="flex items-center space-x-3">
                          <div className={`p-2 rounded-lg ${step.status === 'completed' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}>
                            {step.status === 'completed' ? <CheckCircle2 className="w-4 h-4" /> : <Play className="w-4 h-4 animate-pulse" />}
                          </div>
                          <div>
                            <div className="text-xs font-bold text-white flex items-center space-x-2">
                              <span>{step.name}</span>
                              <span className="text-[10px] font-mono text-slate-400 bg-white/10 px-1.5 py-0.2 rounded">{step.tool}</span>
                            </div>
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              Check Criteria: <span className="font-mono text-slate-300">{step.criteria}</span>
                            </div>
                          </div>
                        </div>

                        <span className={`text-xs px-2.5 py-1 rounded-full font-mono ${
                          step.status === 'completed' ? 'text-emerald-400 bg-emerald-950' : 'text-amber-300 bg-amber-950'
                        }`}>
                          {step.status.toUpperCase()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* TAB 4: 智能工具库 (ToolManager) */}
            {activeTab === 'tools' && (
              <div className="space-y-6">
                <div className={`${theme.cardBg} border ${theme.cardBorder} p-6 rounded-2xl shadow-xl backdrop-blur-md space-y-6`}>
                  <div className="flex items-center justify-between border-b border-white/10 pb-4">
                    <div>
                      <h3 className="text-base font-bold text-white">ToolManager AI 智能工具库</h3>
                      <p className="text-xs text-slate-400">工具 Schema 检查、完整度评估与克隆策略</p>
                    </div>

                    {/* 视图切换：卡片/窗口模式 ↔ 列表模式 */}
                    <div className="flex items-center space-x-1 bg-black/40 p-1 rounded-lg border border-white/10">
                      <button
                        onClick={() => setToolViewMode('grid')}
                        className={`p-1.5 rounded transition ${toolViewMode === 'grid' ? theme.accent : 'text-slate-400 hover:text-white'}`}
                        title="窗口/卡片网格视图"
                      >
                        <Grid className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setToolViewMode('list')}
                        className={`p-1.5 rounded transition ${toolViewMode === 'list' ? theme.accent : 'text-slate-400 hover:text-white'}`}
                        title="高密度列表视图"
                      >
                        <List className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {/* 视图模式 1: Grid 卡片/窗口模式 */}
                  {toolViewMode === 'grid' && (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                      {[
                        { id: 'react_template_generator', name: 'React 模板生成器', quality: 'High', instances: 5, limit: '只适用于 React 18' },
                        { id: 'fastapi_generator', name: 'FastAPI 服务构建器', quality: 'Medium', instances: 3, limit: '缺少认证模块' },
                        { id: 'postgres_setup', name: 'Postgres DB 配置器', quality: 'Missing', instances: 0, limit: '工具缺失 (缺配置脚本)' }
                      ].map((t, i) => (
                        <div key={i} className="p-4 bg-black/40 rounded-xl border border-white/10 space-y-3 hover:border-rose-500/40 transition">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-bold text-white font-mono">{t.id}</span>
                            <span className={`text-[10px] px-2 py-0.5 rounded font-mono ${t.quality === 'Missing' ? 'bg-rose-950 text-rose-300' : 'bg-emerald-950 text-emerald-300'}`}>
                              {t.quality}
                            </span>
                          </div>
                          <div className="text-xs text-slate-300">{t.name}</div>
                          <div className="text-[11px] text-slate-400 font-mono">并发限制: {t.instances} Max Instances</div>
                          <div className="text-[11px] text-amber-400/80">局限性: {t.limit}</div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* 视图模式 2: 高密度列表模式 */}
                  {toolViewMode === 'list' && (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs font-mono">
                        <thead>
                          <tr className="border-b border-white/10 text-slate-400">
                            <th className="py-2.5 px-3">工具 ID</th>
                            <th className="py-2.5 px-3">工具名称</th>
                            <th className="py-2.5 px-3">质量等级</th>
                            <th className="py-2.5 px-3">最大实例</th>
                            <th className="py-2.5 px-3">局限性说明</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-white/5">
                          {[
                            { id: 'react_template_generator', name: 'React 模板生成器', quality: 'High', instances: 5, limit: '只适用于 React 18' },
                            { id: 'fastapi_generator', name: 'FastAPI 服务构建器', quality: 'Medium', instances: 3, limit: '缺少认证模块' },
                            { id: 'postgres_setup', name: 'Postgres DB 配置器', quality: 'Missing', instances: 0, limit: '工具缺失 (缺配置脚本)' }
                          ].map((t, i) => (
                            <tr key={i} className="hover:bg-white/5">
                              <td className="py-3 px-3 text-white font-bold">{t.id}</td>
                              <td className="py-3 px-3 text-slate-300">{t.name}</td>
                              <td className="py-3 px-3">
                                <span className={`px-2 py-0.5 rounded text-[10px] ${t.quality === 'Missing' ? 'bg-rose-950 text-rose-300' : 'bg-emerald-950 text-emerald-300'}`}>
                                  {t.quality}
                                </span>
                              </td>
                              <td className="py-3 px-3 text-slate-400">{t.instances}</td>
                              <td className="py-3 px-3 text-amber-400/80">{t.limit}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                </div>
              </div>
            )}

            {/* TAB 5: 模型与成本分析 */}
            {activeTab === 'models' && (
              <div className="space-y-6">
                <div className={`${theme.cardBg} border ${theme.cardBorder} p-6 rounded-2xl shadow-xl backdrop-blur-md space-y-6`}>
                  <div className="flex items-center justify-between border-b border-white/10 pb-4">
                    <div>
                      <h3 className="text-base font-bold text-white">Agent 模型绑定与 Token 成本实时监控</h3>
                      <p className="text-xs text-slate-400">Gemini 3 Flash / 2.5 多模态 API 实时消耗大盘</p>
                    </div>
                  </div>

                  {/* 成本统计指标 */}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div className="p-4 bg-black/40 rounded-xl border border-white/10">
                      <span className="text-xs text-slate-400">当前任务消耗 Token</span>
                      <div className="text-xl font-bold text-white mt-1">124,850 Tokens</div>
                      <div className="text-[11px] text-emerald-400 mt-1">Prompt: 92k | Completion: 32k</div>
                    </div>
                    <div className="p-4 bg-black/40 rounded-xl border border-white/10">
                      <span className="text-xs text-slate-400">实时花费 (USD)</span>
                      <div className="text-xl font-bold text-emerald-400 mt-1">$0.0248</div>
                      <div className="text-[11px] text-slate-400 mt-1">限额上限: $10.00</div>
                    </div>
                    <div className="p-4 bg-black/40 rounded-xl border border-white/10">
                      <span className="text-xs text-slate-400">Gemini 响应平均延迟</span>
                      <div className="text-xl font-bold text-cyan-400 mt-1">0.42 秒</div>
                      <div className="text-[11px] text-slate-400 mt-1">Gemini 3 Flash Turbo Stream</div>
                    </div>
                  </div>
                </div>
              </div>
            )}

          </div>
        )}

      </main>

      {/* ================= 3. Level 1-4 暂停干预 Modal 弹窗 ================= */}
      {showInterventionModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className={`${theme.cardBg} border ${theme.cardBorder} max-w-xl w-full p-6 rounded-2xl shadow-2xl space-y-5 relative`}>
            <button
              onClick={() => setShowInterventionModal(false)}
              className="absolute top-4 right-4 text-slate-400 hover:text-white"
            >
              <X className="w-5 h-5" />
            </button>

            <div className="flex items-center space-x-3">
              <div className="p-2.5 bg-amber-500/20 text-amber-400 rounded-xl">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-bold text-white">用户主动介入 & Level 1-4 诊断中心</h3>
                <p className="text-xs text-slate-400">输入当前异常描述，由 Gemini 进行严重程度评级</p>
              </div>
            </div>

            <div className="space-y-3">
              <textarea
                value={issueDescription}
                onChange={(e) => setIssueDescription(e.target.value)}
                placeholder="请输入发现的问题（例如：数据库脚本缺少环境配置，握手超时...）"
                className="w-full bg-black/60 border border-white/10 rounded-xl p-3 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-rose-500 h-24"
              />
              <button
                onClick={handleAIDiagnose}
                disabled={isDiagnosing}
                className={`w-full py-2.5 ${theme.accent} ${theme.accentGlow} rounded-xl text-xs font-semibold flex items-center justify-center space-x-2`}
              >
                {isDiagnosing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                <span>{isDiagnosing ? 'Gemini 智能诊断中...' : '调用 Gemini AI 诊断问题 Level'}</span>
              </button>
            </div>

            {aiDiagnostic && (
              <div className="p-4 bg-black/60 rounded-xl border border-rose-500/40 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-rose-400">{aiDiagnostic.level}</span>
                  <span className="text-[10px] bg-rose-950 text-rose-300 px-2 py-0.5 rounded font-mono">
                    推荐策略: {aiDiagnostic.action}
                  </span>
                </div>
                <p className="text-xs text-slate-300">{aiDiagnostic.recommendation}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ================= 4. 驻场 Knowledge AI 侧边抽屉 (Drawer) ================= */}
      {showKnowledgeDrawer && (
        <div className="fixed inset-y-0 right-0 z-50 w-full max-w-md bg-slate-950 border-l border-white/10 shadow-2xl flex flex-col p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-white/10 pb-3">
            <div className="flex items-center space-x-2">
              <Brain className="w-5 h-5 text-indigo-400" />
              <span className="text-sm font-bold text-white">Knowledge AI 驻场助手</span>
              <span className="text-[10px] bg-indigo-950 text-indigo-300 border border-indigo-800 px-2 py-0.5 rounded">只读 Context</span>
            </div>
            <button onClick={() => setShowKnowledgeDrawer(false)} className="text-slate-400 hover:text-white">
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* 消息对话区域 */}
          <div className="flex-1 overflow-y-auto space-y-3 pr-1">
            {knowledgeChat.map((msg, i) => (
              <div key={i} className={`p-3 rounded-xl text-xs ${msg.role === 'user' ? 'bg-indigo-600 text-white ml-8' : 'bg-slate-900 border border-slate-800 text-slate-200 mr-4 space-y-2'}`}>
                <p className="leading-relaxed">{msg.text}</p>
                {msg.citations && msg.citations.length > 0 && (
                  <div className="pt-2 border-t border-slate-800 text-[10px] text-indigo-400">
                    来源: {msg.citations[0]}
                  </div>
                )}
              </div>
            ))}
            {isAIThinking && <div className="text-xs text-slate-400 animate-pulse">Knowledge AI 检索计算中...</div>}
          </div>

          {/* 搜索框与 Grounding 开关 */}
          <div className="space-y-2 pt-2 border-t border-white/10">
            <div className="flex items-center justify-between text-[11px] text-slate-400">
              <span>Google Search Grounding 联网支持</span>
              <input
                type="checkbox"
                checked={enableGrounding}
                onChange={(e) => setEnableGrounding(e.target.checked)}
                className="rounded border-white/10 cursor-pointer"
              />
            </div>
            <div className="flex space-x-2">
              <input
                type="text"
                value={knowledgeQuery}
                onChange={(e) => setKnowledgeQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAskKnowledge()}
                placeholder="咨询架构规则、Step 状态或日志..."
                className="flex-1 bg-black/60 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
              <button onClick={handleAskKnowledge} className="p-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl">
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}