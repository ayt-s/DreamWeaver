-- 阶段 2：账号表（2026-11-10）
--
-- 历史数据（canvas_project / creative_task / novel_project / api_quota）全部挂在
-- user_id = 1 上；本表空表时由 `config/UserSeeder` 插入 admin（自增必然拿到 id=1），
-- 保证老数据在登录后仍然可见。
--
-- 唯一索引是**并发注册的最后一道防线**：AuthService 的「先查后插」存在竞态窗口。

CREATE TABLE IF NOT EXISTS users (
    id            BIGINT       NOT NULL AUTO_INCREMENT,
    username      VARCHAR(32)  NOT NULL,
    password_hash VARCHAR(100) NOT NULL COMMENT 'bcrypt，绝不存明文',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='账号（阶段 2）';
