-- Phase 5 数据库迁移：穿帮段重新生成（按段重生 + 重新拼接）
-- 用途：segments_json 保存提交时的段配置（重生输入源）；
--      prev_result_json 保存覆盖前的旧成片产物（回滚用）。
ALTER TABLE creative_task ADD COLUMN segments_json LONGTEXT COMMENT '提交时的段配置数组 JSON（重生输入源）' AFTER image_urls;
ALTER TABLE creative_task ADD COLUMN prev_result_json LONGTEXT COMMENT '重生覆盖前的旧 result_json（回滚用）' AFTER segments_json;
