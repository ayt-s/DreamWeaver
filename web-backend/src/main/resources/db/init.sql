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
    video_id      VARCHAR(64)  DEFAULT NULL COMMENT 'Agnes 异步任务 ID（用于幂等判断）',
    shot_index    INT          DEFAULT 0 COMMENT '当前分镜索引',
    error_message VARCHAR(512) DEFAULT NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    version       INT          NOT NULL DEFAULT 0 COMMENT '乐观锁版本号',
    KEY idx_status (status),
    KEY idx_session (session_id),
    KEY idx_video_id (video_id)
) ENGINE = InnoDB COMMENT '创作任务';
-- 无限画布项目（按自定义名称持久化画布）
CREATE TABLE IF NOT EXISTS canvas_project (
    id           BIGINT       NOT NULL AUTO_INCREMENT,
    project_name VARCHAR(64)  NOT NULL COMMENT '项目名称（自定义）',
    user_id      BIGINT       NOT NULL DEFAULT 1,
    nodes_json   LONGTEXT     COMMENT 'React Flow 节点 JSON',
    edges_json   LONGTEXT     COMMENT '连线 JSON',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_cp_user (user_id)
) ENGINE = InnoDB COMMENT '无限画布项目';

-- 小说转漫剧项目（小说预处理 & 分镜落库）
CREATE TABLE IF NOT EXISTS novel_project (
    id                    BIGINT       NOT NULL AUTO_INCREMENT,
    project_name          VARCHAR(64)  NOT NULL COMMENT '项目名称',
    user_id               BIGINT       DEFAULT 1,
    novel_text            LONGTEXT     NOT NULL COMMENT '原始小说文本',
    chapters_json         LONGTEXT     COMMENT '章节切分 JSON',
    analysis_json         LONGTEXT     COMMENT '角色/场景/风格等分析 JSON',
    segments_json         LONGTEXT     COMMENT '分镜片段 JSON 数组',
    visual_style          VARCHAR(128) DEFAULT NULL COMMENT '视觉风格（电影写实/国漫等）',
    canvas_project_id     BIGINT       DEFAULT NULL COMMENT '关联的 canvas_project.id',
    status                VARCHAR(16)  NOT NULL DEFAULT 'draft' COMMENT 'draft/ready/failed',
    error_message         VARCHAR(500) DEFAULT NULL,
    created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_np_user (user_id),
    KEY idx_np_status (status)
) ENGINE = InnoDB COMMENT '小说转漫剧项目';
