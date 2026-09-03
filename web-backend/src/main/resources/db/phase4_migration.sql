-- Phase 4 P0 数据库迁移：新增生成类型字段
-- 用途：区分文生图/图生视频/纯文本视频等任务类型
ALTER TABLE creative_task ADD COLUMN gen_type VARCHAR(32) DEFAULT 'text_video' COMMENT '生成类型：text_video(纯文本视频)/image_video(图生视频)/novel_image(小说转图)' AFTER status;
ALTER TABLE creative_task ADD COLUMN image_urls TEXT COMMENT '文生图产出的图片 URL 数组（JSON 格式）' AFTER result_json;
