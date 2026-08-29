/// EAS 2.0 Database Layer
///
/// 支持SQLite和PostgreSQL，插件化设计
use async_trait::async_trait;
use eas_common::*;
use sqlx::{Pool, Row, Sqlite, SqlitePool};
use uuid::Uuid;

pub struct SqliteDatabase {
    pool: Pool<Sqlite>,
}

impl SqliteDatabase {
    pub async fn new(database_url: &str) -> EasResult<Self> {
        let pool = SqlitePool::connect(database_url)
            .await
            .map_err(|e| EasError::Database(format!("Failed to connect: {}", e)))?;

        Ok(Self { pool })
    }

    pub async fn initialize(&self) -> EasResult<()> {
        // 创建表结构
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS designs (
                design_id TEXT PRIMARY KEY,
                user_request TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                part1_design TEXT NOT NULL,
                part2_tool_assessment TEXT NOT NULL,
                user_approval INTEGER,
                user_comments TEXT,
                created_at TEXT NOT NULL
            )
            "#,
        )
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to create designs table: {}", e)))?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS plans (
                plan_id TEXT PRIMARY KEY,
                design_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                segments TEXT NOT NULL,
                implementation_list TEXT NOT NULL,
                dynamic_insertions TEXT NOT NULL,
                FOREIGN KEY (design_id) REFERENCES designs(design_id)
            )
            "#,
        )
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to create plans table: {}", e)))?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS steps (
                step_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                step_order INTEGER NOT NULL,
                description TEXT NOT NULL,
                tool TEXT NOT NULL,
                inputs TEXT NOT NULL,
                check_criteria TEXT NOT NULL,
                outputs TEXT,
                status TEXT NOT NULL,
                executed_at TEXT,
                FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
            )
            "#,
        )
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to create steps table: {}", e)))?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS tools (
                tool_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                file_location TEXT NOT NULL,
                tool_index TEXT NOT NULL,
                license TEXT NOT NULL,
                dependencies TEXT NOT NULL,
                test_results TEXT,
                created_at TEXT NOT NULL
            )
            "#,
        )
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to create tools table: {}", e)))?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS logs (
                log_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                context TEXT,
                service TEXT
            )
            "#,
        )
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to create logs table: {}", e)))?;

        Ok(())
    }
}

#[async_trait]
impl Database for SqliteDatabase {
    async fn save_design(&self, design: &DesignDocument) -> EasResult<()> {
        let design_json =
            serde_json::to_string(&design.part1_design).map_err(|e| EasError::Serialization(e))?;
        let assessment_json = serde_json::to_string(&design.part2_tool_assessment)
            .map_err(|e| EasError::Serialization(e))?;

        sqlx::query(
            r#"
            INSERT INTO designs (design_id, user_request, timestamp, part1_design, part2_tool_assessment, user_approval, user_comments, created_at)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
            ON CONFLICT(design_id) DO UPDATE SET
                user_approval = ?6,
                user_comments = ?7
            "#,
        )
        .bind(design.design_id.to_string())
        .bind(&design.user_request)
        .bind(design.timestamp.to_rfc3339())
        .bind(design_json)
        .bind(assessment_json)
        .bind(design.user_approval)
        .bind(&design.user_comments)
        .bind(chrono::Utc::now().to_rfc3339())
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to save design: {}", e)))?;

        Ok(())
    }

    async fn get_design(&self, design_id: Uuid) -> EasResult<Option<DesignDocument>> {
        let row = sqlx::query(
            "SELECT design_id, user_request, timestamp, part1_design, part2_tool_assessment, user_approval, user_comments FROM designs WHERE design_id = ?1"
        )
        .bind(design_id.to_string())
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to get design: {}", e)))?;

        if let Some(row) = row {
            // 解析JSON字段
            let part1_design: DesignPart1 = serde_json::from_str(
                row.try_get("part1_design")
                    .map_err(|e| EasError::Database(e.to_string()))?,
            )
            .map_err(|e| EasError::Serialization(e))?;

            let part2_tool_assessment: ToolAssessment = serde_json::from_str(
                row.try_get("part2_tool_assessment")
                    .map_err(|e| EasError::Database(e.to_string()))?,
            )
            .map_err(|e| EasError::Serialization(e))?;

            let timestamp_str: String = row
                .try_get("timestamp")
                .map_err(|e| EasError::Database(e.to_string()))?;
            let timestamp = chrono::DateTime::parse_from_rfc3339(&timestamp_str)
                .map_err(|e| EasError::Database(e.to_string()))?
                .with_timezone(&chrono::Utc);

            Ok(Some(DesignDocument {
                design_id,
                user_request: row
                    .try_get("user_request")
                    .map_err(|e| EasError::Database(e.to_string()))?,
                timestamp,
                part1_design,
                part2_tool_assessment,
                user_approval: row.try_get("user_approval").ok(),
                user_comments: row.try_get("user_comments").ok(),
            }))
        } else {
            Ok(None)
        }
    }

    async fn save_plan(&self, plan: &ExecutionPlan) -> EasResult<()> {
        let segments_json =
            serde_json::to_string(&plan.segments).map_err(|e| EasError::Serialization(e))?;
        let impl_list_json = serde_json::to_string(&plan.implementation_list)
            .map_err(|e| EasError::Serialization(e))?;
        let insertions_json = serde_json::to_string(&plan.dynamic_insertions)
            .map_err(|e| EasError::Serialization(e))?;
        let status_str = format!("{:?}", plan.status);

        sqlx::query(
            r#"
            INSERT INTO plans (plan_id, design_id, created_at, status, segments, implementation_list, dynamic_insertions)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
            ON CONFLICT(plan_id) DO UPDATE SET
                status = ?4,
                segments = ?5,
                dynamic_insertions = ?7
            "#,
        )
        .bind(plan.plan_id.to_string())
        .bind(plan.design_id.to_string())
        .bind(plan.created_at.to_rfc3339())
        .bind(status_str)
        .bind(segments_json)
        .bind(impl_list_json)
        .bind(insertions_json)
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to save plan: {}", e)))?;

        Ok(())
    }

    async fn get_plan(&self, plan_id: Uuid) -> EasResult<Option<ExecutionPlan>> {
        // 实现类似get_design的逻辑
        // 为简洁起见，这里留空，实际使用时需要完整实现
        Ok(None)
    }

    async fn update_step(&self, step: &Step) -> EasResult<()> {
        let inputs_json =
            serde_json::to_string(&step.inputs).map_err(|e| EasError::Serialization(e))?;
        let outputs_json = step
            .outputs
            .as_ref()
            .map(|o| serde_json::to_string(o))
            .transpose()
            .map_err(|e| EasError::Serialization(e))?;
        let status_str = format!("{:?}", step.status);

        sqlx::query(
            r#"
            UPDATE steps SET
                status = ?1,
                outputs = ?2,
                executed_at = ?3
            WHERE step_id = ?4
            "#,
        )
        .bind(status_str)
        .bind(outputs_json)
        .bind(step.executed_at.map(|t| t.to_rfc3339()))
        .bind(step.step_id.to_string())
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to update step: {}", e)))?;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_database() {
        let db = SqliteDatabase::new(":memory:").await.unwrap();
        db.initialize().await.unwrap();

        // 测试会在这里添加
    }
}
