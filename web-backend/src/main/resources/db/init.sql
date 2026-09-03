-- DreamWeaver 数据库初始化（Phase 1 最小集）
CREATE DATABASE IF NOT EXISTS dreamweaver DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE dreamweaver;

-- 创作任务表（对应 Java entity/Task.java）
CREATE TABLE IF NOT EXISTS creative_task (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id    VARCHAR(32)  DEFAULT NULL COMMENT 'FastAPI 侧 LangGraph thread_id',
    user_id       BIGINT       DEFAULT NULL,
    status        VARCHAR(32)  NOT NULL DEFAULT 'pending' COMMENT 'pending/queued/.../completed/failed',
    prompt        TEXT         COMMENT '用户原始需求',
    result_json   TEXT         COMMENT '模型侧产物（视频 URL 数组等）',
    error_message VARCHAR(512) DEFAULT NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_status (status),
    KEY idx_session (session_id)
) ENGINE = InnoDB COMMENT '创作任务';