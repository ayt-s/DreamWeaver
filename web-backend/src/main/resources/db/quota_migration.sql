-- API 配额表
CREATE TABLE IF NOT EXISTS api_quota (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     BIGINT       NOT NULL,
    model_name  VARCHAR(64)  NOT NULL,
    used_count  INT          NOT NULL DEFAULT 0 COMMENT '已调用次数',
    used_seconds INT         NOT NULL DEFAULT 0 COMMENT '已消耗秒数',
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_model (user_id, model_name)
) ENGINE = InnoDB COMMENT 'API 配额统计';
