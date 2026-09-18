/// 插件化接口定义
///
/// 所有核心组件都实现这些Trait，支持插件扩展
use async_trait::async_trait;
use serde_json::Value;
use std::sync::Arc;
use uuid::Uuid;

use crate::{DesignDocument, EasError, EasResult, ExecutionPlan, Step, ToolSchema};

/// AI服务接口 - Designer, ToolManager, Knowledge AI都实现此接口
#[async_trait]
pub trait AIService: Send + Sync {
    /// 服务名称
    fn name(&self) -> &str;

    /// 服务版本
    fn version(&self) -> &str;

    /// 处理请求
    async fn process(&self, request: Value) -> EasResult<Value>;

    /// 健康检查
    async fn health_check(&self) -> EasResult<bool>;
}

/// 调度器接口 - 支持自定义调度策略
#[async_trait]
pub trait Dispatcher: Send + Sync {
    /// 验证设计书格式
    async fn validate_design(&self, design: &DesignDocument) -> EasResult<()>;

    /// 验证执行计划格式
    async fn validate_plan(&self, plan: &ExecutionPlan) -> EasResult<()>;

    /// 路由请求到对应的AI服务
    async fn route(&self, service_name: &str, request: Value) -> EasResult<Value>;
}

/// 执行器接口 - 支持不同类型的执行器
#[async_trait]
pub trait Executor: Send + Sync {
    /// 执行单个步骤
    async fn execute_step(&self, step: &Step) -> EasResult<Value>;

    /// 检查步骤执行结果
    async fn check_result(&self, step: &Step, output: &Value) -> EasResult<bool>;

    /// 处理执行失败
    async fn handle_failure(&self, step: &Step, error: &str) -> EasResult<()>;
}

/// 工具管理器接口 - 支持工具插件
#[async_trait]
pub trait ToolManager: Send + Sync {
    /// 注册工具
    async fn register_tool(&self, tool: ToolSchema) -> EasResult<()>;

    /// 获取工具
    async fn get_tool(&self, tool_id: &str) -> EasResult<Option<ToolSchema>>;

    /// 列出所有工具
    async fn list_tools(&self) -> EasResult<Vec<ToolSchema>>;

    /// 评估工具可用性
    async fn assess_tool(&self, tool_id: &str) -> EasResult<Value>;

    /// 创建工具实例
    async fn create_instance(&self, tool_id: &str) -> EasResult<String>;

    /// 删除工具实例
    async fn delete_instance(&self, instance_id: &str) -> EasResult<()>;
}

/// 日志服务接口 - 支持不同的日志后端
#[async_trait]
pub trait Logger: Send + Sync {
    /// 记录日志
    async fn log(&self, level: LogLevel, message: &str, context: Option<Value>) -> EasResult<()>;

    /// 查询日志
    async fn query(&self, filter: LogFilter) -> EasResult<Vec<LogEntry>>;
}

#[derive(Debug, Clone)]
pub enum LogLevel {
    Debug,
    Info,
    Warn,
    Error,
}

#[derive(Debug, Clone)]
pub struct LogFilter {
    pub start_time: Option<chrono::DateTime<chrono::Utc>>,
    pub end_time: Option<chrono::DateTime<chrono::Utc>>,
    pub level: Option<LogLevel>,
    pub service: Option<String>,
}

#[derive(Debug, Clone)]
pub struct LogEntry {
    pub id: Uuid,
    pub timestamp: chrono::DateTime<chrono::Utc>,
    pub level: LogLevel,
    pub message: String,
    pub context: Option<Value>,
}

/// 数据库接口 - 支持不同的数据库后端
#[async_trait]
pub trait Database: Send + Sync {
    /// 保存设计书
    async fn save_design(&self, design: &DesignDocument) -> EasResult<()>;

    /// 获取设计书
    async fn get_design(&self, design_id: Uuid) -> EasResult<Option<DesignDocument>>;

    /// 保存执行计划
    async fn save_plan(&self, plan: &ExecutionPlan) -> EasResult<()>;

    /// 获取执行计划
    async fn get_plan(&self, plan_id: Uuid) -> EasResult<Option<ExecutionPlan>>;

    /// 更新步骤状态
    async fn update_step(&self, step: &Step) -> EasResult<()>;
}

/// 插件接口 - 第三方扩展实现此接口
#[async_trait]
pub trait Plugin: Send + Sync {
    /// 插件名称
    fn name(&self) -> &str;

    /// 插件版本
    fn version(&self) -> &str;

    /// 插件描述
    fn description(&self) -> &str;

    /// 初始化插件
    async fn initialize(&mut self) -> EasResult<()>;

    /// 插件钩子：设计书生成前
    async fn before_design(&self, request: &str) -> EasResult<Option<Value>>;

    /// 插件钩子：设计书生成后
    async fn after_design(&self, design: &DesignDocument) -> EasResult<Option<Value>>;

    /// 插件钩子：步骤执行前
    async fn before_step(&self, step: &Step) -> EasResult<Option<Value>>;

    /// 插件钩子：步骤执行后
    async fn after_step(&self, step: &Step, output: &Value) -> EasResult<Option<Value>>;

    /// 关闭插件
    async fn shutdown(&mut self) -> EasResult<()>;
}

/// 插件管理器 - 管理所有插件
pub trait PluginManager: Send + Sync {
    /// 加载插件
    fn load_plugin(&mut self, plugin: Arc<dyn Plugin>) -> Result<(), EasError>;

    /// 卸载插件
    fn unload_plugin(&mut self, name: &str) -> Result<(), EasError>;

    /// 列出所有插件
    fn list_plugins(&self) -> Vec<String>;

    /// 触发钩子（同步版本，内部使用tokio::spawn处理异步）
    fn trigger_hook(&self, hook: PluginHook);
}

#[derive(Debug, Clone)]
pub enum PluginHook {
    BeforeDesign(String),
    AfterDesign(DesignDocument),
    BeforeStep(Step),
    AfterStep(Step, Value),
}
