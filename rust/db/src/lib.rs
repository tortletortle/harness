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
            serde_json::to_string(&design.part1_design).map_err(EasError::Serialization)?;
        let assessment_json = serde_json::to_string(&design.part2_tool_assessment)
            .map_err(EasError::Serialization)?;

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
            .map_err(EasError::Serialization)?;

            let part2_tool_assessment: ToolAssessment = serde_json::from_str(
                row.try_get("part2_tool_assessment")
                    .map_err(|e| EasError::Database(e.to_string()))?,
            )
            .map_err(EasError::Serialization)?;

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
            serde_json::to_string(&plan.segments).map_err(EasError::Serialization)?;
        let impl_list_json =
            serde_json::to_string(&plan.implementation_list).map_err(EasError::Serialization)?;
        let insertions_json =
            serde_json::to_string(&plan.dynamic_insertions).map_err(EasError::Serialization)?;
        let status_str = format!("{:?}", plan.status);
        let mut transaction =
            self.pool.begin().await.map_err(|e| {
                EasError::Database(format!("Failed to begin plan transaction: {}", e))
            })?;

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
        .execute(&mut *transaction)
        .await
        .map_err(|e| EasError::Database(format!("Failed to save plan: {}", e)))?;

        sqlx::query("DELETE FROM steps WHERE plan_id = ?1")
            .bind(plan.plan_id.to_string())
            .execute(&mut *transaction)
            .await
            .map_err(|e| EasError::Database(format!("Failed to replace plan steps: {}", e)))?;

        for segment in &plan.segments {
            for step in &segment.steps {
                let inputs =
                    serde_json::to_string(&step.inputs).map_err(EasError::Serialization)?;
                let outputs = step
                    .outputs
                    .as_ref()
                    .map(serde_json::to_string)
                    .transpose()
                    .map_err(EasError::Serialization)?;
                sqlx::query(
                    r#"
                    INSERT INTO steps (
                        step_id, plan_id, segment_id, step_order, description, tool,
                        inputs, check_criteria, outputs, status, executed_at
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)
                    "#,
                )
                .bind(step.step_id.to_string())
                .bind(plan.plan_id.to_string())
                .bind(segment.segment_id.to_string())
                .bind(step.order)
                .bind(&step.description)
                .bind(&step.tool)
                .bind(inputs)
                .bind(&step.check_criteria)
                .bind(outputs)
                .bind(format!("{:?}", step.status))
                .bind(step.executed_at.map(|value| value.to_rfc3339()))
                .execute(&mut *transaction)
                .await
                .map_err(|e| EasError::Database(format!("Failed to save plan step: {}", e)))?;
            }
        }

        transaction
            .commit()
            .await
            .map_err(|e| EasError::Database(format!("Failed to commit plan: {}", e)))?;

        Ok(())
    }

    async fn get_plan(&self, plan_id: Uuid) -> EasResult<Option<ExecutionPlan>> {
        let row = sqlx::query(
            "SELECT design_id, created_at, status, segments, implementation_list, dynamic_insertions FROM plans WHERE plan_id = ?1",
        )
        .bind(plan_id.to_string())
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to get plan: {}", e)))?;

        let Some(row) = row else {
            return Ok(None);
        };

        let design_id_text: String = row
            .try_get("design_id")
            .map_err(|e| EasError::Database(e.to_string()))?;
        let design_id = Uuid::parse_str(&design_id_text)
            .map_err(|e| EasError::Database(format!("Invalid design_id in plan: {}", e)))?;

        let created_at_text: String = row
            .try_get("created_at")
            .map_err(|e| EasError::Database(e.to_string()))?;
        let created_at = chrono::DateTime::parse_from_rfc3339(&created_at_text)
            .map_err(|e| EasError::Database(format!("Invalid plan created_at: {}", e)))?
            .with_timezone(&chrono::Utc);

        let status_text: String = row
            .try_get("status")
            .map_err(|e| EasError::Database(e.to_string()))?;
        let status: PlanStatus = serde_json::from_str(&format!("\"{}\"", status_text))
            .map_err(EasError::Serialization)?;

        let mut segments: Vec<PlanSegment> = serde_json::from_str(
            row.try_get::<&str, _>("segments")
                .map_err(|e| EasError::Database(e.to_string()))?,
        )
        .map_err(EasError::Serialization)?;
        let implementation_list: Vec<String> = serde_json::from_str(
            row.try_get::<&str, _>("implementation_list")
                .map_err(|e| EasError::Database(e.to_string()))?,
        )
        .map_err(EasError::Serialization)?;
        let dynamic_insertions: Vec<DynamicInsertion> = serde_json::from_str(
            row.try_get::<&str, _>("dynamic_insertions")
                .map_err(|e| EasError::Database(e.to_string()))?,
        )
        .map_err(EasError::Serialization)?;

        let step_rows = sqlx::query(
            "SELECT step_id, status, outputs, executed_at FROM steps WHERE plan_id = ?1",
        )
        .bind(plan_id.to_string())
        .fetch_all(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to get plan steps: {}", e)))?;
        let mut persisted = std::collections::HashMap::new();
        for step_row in step_rows {
            let step_id_text: String = step_row
                .try_get("step_id")
                .map_err(|e| EasError::Database(e.to_string()))?;
            let step_id = Uuid::parse_str(&step_id_text)
                .map_err(|e| EasError::Database(format!("Invalid step_id in plan: {}", e)))?;
            let status_text: String = step_row
                .try_get("status")
                .map_err(|e| EasError::Database(e.to_string()))?;
            let status: StepStatus = serde_json::from_str(&format!("\"{}\"", status_text))
                .map_err(EasError::Serialization)?;
            let outputs_text: Option<String> = step_row
                .try_get("outputs")
                .map_err(|e| EasError::Database(e.to_string()))?;
            let outputs = outputs_text
                .map(|value| serde_json::from_str(&value))
                .transpose()
                .map_err(EasError::Serialization)?;
            let executed_at_text: Option<String> = step_row
                .try_get("executed_at")
                .map_err(|e| EasError::Database(e.to_string()))?;
            let executed_at = executed_at_text
                .map(|value| {
                    chrono::DateTime::parse_from_rfc3339(&value)
                        .map(|parsed| parsed.with_timezone(&chrono::Utc))
                        .map_err(|e| EasError::Database(format!("Invalid step executed_at: {}", e)))
                })
                .transpose()?;
            persisted.insert(step_id, (status, outputs, executed_at));
        }
        for segment in &mut segments {
            for step in &mut segment.steps {
                if let Some((status, outputs, executed_at)) = persisted.remove(&step.step_id) {
                    step.status = status;
                    step.outputs = outputs;
                    step.executed_at = executed_at;
                }
            }
        }

        Ok(Some(ExecutionPlan {
            plan_id,
            design_id,
            created_at,
            status,
            segments,
            implementation_list,
            dynamic_insertions,
        }))
    }

    async fn update_step(&self, step: &Step) -> EasResult<()> {
        let inputs_json = serde_json::to_string(&step.inputs).map_err(EasError::Serialization)?;
        let outputs_json = step
            .outputs
            .as_ref()
            .map(serde_json::to_string)
            .transpose()
            .map_err(EasError::Serialization)?;
        let status_str = format!("{:?}", step.status);

        let result = sqlx::query(
            r#"
            UPDATE steps SET
                status = ?1,
                inputs = ?2,
                outputs = ?3,
                executed_at = ?4
            WHERE step_id = ?5
            "#,
        )
        .bind(status_str)
        .bind(inputs_json)
        .bind(outputs_json)
        .bind(step.executed_at.map(|t| t.to_rfc3339()))
        .bind(step.step_id.to_string())
        .execute(&self.pool)
        .await
        .map_err(|e| EasError::Database(format!("Failed to update step: {}", e)))?;

        if result.rows_affected() == 0 {
            return Err(EasError::Database(format!(
                "Step {} does not exist",
                step.step_id
            )));
        }

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

        let design_id = Uuid::new_v4();
        let design = DesignDocument {
            design_id,
            user_request: "round trip".into(),
            timestamp: chrono::Utc::now(),
            part1_design: DesignPart1 {
                architecture: Architecture {
                    components: vec![],
                    interactions: vec![],
                },
                difficulty: Difficulty {
                    overall: "small".into(),
                    breakdown: serde_json::json!({}),
                },
            },
            part2_tool_assessment: ToolAssessment {
                required_tools: vec![],
                overall_tool_completeness: 1.0,
                risks: vec![],
            },
            user_approval: Some(true),
            user_comments: None,
        };
        db.save_design(&design).await.unwrap();

        let plan = ExecutionPlan {
            plan_id: Uuid::new_v4(),
            design_id,
            created_at: chrono::Utc::now(),
            status: PlanStatus::Approved,
            segments: vec![PlanSegment {
                segment_id: Uuid::new_v4(),
                description: "segment".into(),
                steps: vec![Step {
                    step_id: Uuid::new_v4(),
                    order: 1,
                    description: "step".into(),
                    tool: "test".into(),
                    inputs: serde_json::json!({"input": true}),
                    check_criteria: "ok".into(),
                    outputs: None,
                    status: StepStatus::Retry,
                    executed_at: None,
                }],
            }],
            implementation_list: vec!["item".into()],
            dynamic_insertions: vec![],
        };

        db.save_plan(&plan).await.unwrap();
        let loaded = db.get_plan(plan.plan_id).await.unwrap().unwrap();
        assert_eq!(loaded.status, PlanStatus::Approved);
        assert_eq!(loaded.segments[0].steps[0].status, StepStatus::Retry);
        assert_eq!(loaded.implementation_list, vec!["item"]);

        let mut updated_step = loaded.segments[0].steps[0].clone();
        updated_step.status = StepStatus::Success;
        updated_step.outputs = Some(serde_json::json!({"done": true}));
        updated_step.executed_at = Some(chrono::Utc::now());
        db.update_step(&updated_step).await.unwrap();
        let updated = db.get_plan(plan.plan_id).await.unwrap().unwrap();
        assert_eq!(updated.segments[0].steps[0].status, StepStatus::Success);
        assert_eq!(
            updated.segments[0].steps[0].outputs,
            Some(serde_json::json!({"done": true}))
        );
        assert!(db.get_plan(Uuid::new_v4()).await.unwrap().is_none());
    }
}
