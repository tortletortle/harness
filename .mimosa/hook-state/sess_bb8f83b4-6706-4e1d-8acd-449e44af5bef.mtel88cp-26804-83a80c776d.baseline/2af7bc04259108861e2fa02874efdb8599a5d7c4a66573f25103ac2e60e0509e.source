use thiserror::Error;

#[derive(Error, Debug)]
pub enum EasError {
    #[error("Database error: {0}")]
    Database(String),

    #[error("API error: {0}")]
    Api(String),

    #[error("Validation error: {0}")]
    Validation(String),

    #[error("Tool error: {0}")]
    Tool(String),

    #[error("Plugin error: {0}")]
    Plugin(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("Unknown error: {0}")]
    Unknown(String),
}

pub type EasResult<T> = Result<T, EasError>;
