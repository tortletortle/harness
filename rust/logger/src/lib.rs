/// EAS 2.0 Logger
///
/// 高性能日志系统，支持插件化扩展
use async_trait::async_trait;
use eas_common::*;
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::RwLock;
use uuid::Uuid;

pub struct InMemoryLogger {
    logs: Arc<RwLock<Vec<LogEntry>>>,
}

impl InMemoryLogger {
    pub fn new() -> Self {
        Self {
            logs: Arc::new(RwLock::new(Vec::new())),
        }
    }
}

impl Default for InMemoryLogger {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Logger for InMemoryLogger {
    async fn log(&self, level: LogLevel, message: &str, context: Option<Value>) -> EasResult<()> {
        let entry = LogEntry {
            id: Uuid::new_v4(),
            timestamp: chrono::Utc::now(),
            level: level.clone(),
            message: message.to_string(),
            context,
        };

        // 使用tracing记录
        match &entry.level {
            LogLevel::Debug => tracing::debug!("{}", message),
            LogLevel::Info => tracing::info!("{}", message),
            LogLevel::Warn => tracing::warn!("{}", message),
            LogLevel::Error => tracing::error!("{}", message),
        }

        let mut logs = self.logs.write().await;
        logs.push(entry);

        Ok(())
    }

    async fn query(&self, filter: LogFilter) -> EasResult<Vec<LogEntry>> {
        let logs = self.logs.read().await;

        let filtered: Vec<LogEntry> = logs
            .iter()
            .filter(|entry| {
                if let Some(start) = filter.start_time {
                    if entry.timestamp < start {
                        return false;
                    }
                }

                if let Some(end) = filter.end_time {
                    if entry.timestamp > end {
                        return false;
                    }
                }

                // 可以添加更多过滤条件

                true
            })
            .cloned()
            .collect();

        Ok(filtered)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_log() {
        let logger = InMemoryLogger::new();

        logger
            .log(LogLevel::Info, "Test message", None)
            .await
            .unwrap();

        let filter = LogFilter {
            start_time: None,
            end_time: None,
            level: None,
            service: None,
        };

        let logs = logger.query(filter).await.unwrap();
        assert_eq!(logs.len(), 1);
        assert_eq!(logs[0].message, "Test message");
    }
}
