-- Initialize stock_data database for Stock Platform
-- Langfuse uses 'default' database, we use 'stock_data' for isolation

CREATE DATABASE IF NOT EXISTS stock_data;

-- Structured log table for request-tracing and log analysis
-- Stores JSONL-imported log records with request correlation
CREATE TABLE IF NOT EXISTS stock_data.system_structured_logs
(
    timestamp   DateTime64(3),
    level       LowCardinality(String),
    request_id  String DEFAULT '-',
    user_id     String DEFAULT '-',
    module      String,
    function    String,
    line        UInt32,
    message     String,
    exception   Nullable(String),
    extra       String DEFAULT '{}'
)
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (timestamp, level, request_id)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
