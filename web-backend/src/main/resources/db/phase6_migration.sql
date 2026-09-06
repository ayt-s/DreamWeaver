-- Phase 6 数据库迁移：草稿/成品区分
-- 用途：is_draft 标记草稿区作品（1=草稿，默认；0=成品）。
--      新生成物默认进草稿区，人工确认或 agent 测评通过后转成品。
--      默认 1 保证所有新任务先进草稿区，不会未经审核直接进成品。
ALTER TABLE creative_task ADD COLUMN is_draft TINYINT NOT NULL DEFAULT 1 COMMENT '草稿标记 0=成品 1=草稿（默认）' AFTER prev_result_json;

ALTER TABLE creative_task ADD COLUMN completed_at DATETIME DEFAULT NULL COMMENT 'terminal completion time; distinct from updated_at' AFTER updated_at;
