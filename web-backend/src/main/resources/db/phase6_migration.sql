-- Phase 6 数据库迁移：草稿/成品区分
-- 用途：is_draft 标记草稿区作品（0=成品，默认；1=草稿）。
--      草稿是用户意图标记（"我还要改"），不是状态派生——status 无法区分。
--      默认 0 保证历史作品全部保持「成品」，只有手动标记的才进草稿区。
ALTER TABLE creative_task ADD COLUMN is_draft TINYINT NOT NULL DEFAULT 0 COMMENT '草稿标记 0=成品 1=草稿' AFTER prev_result_json;

ALTER TABLE creative_task ADD COLUMN completed_at DATETIME DEFAULT NULL COMMENT 'terminal completion time; distinct from updated_at' AFTER updated_at;
