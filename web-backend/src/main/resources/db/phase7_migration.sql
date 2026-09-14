-- Phase 7 数据库迁移：可灵式精细控制参数持久化
-- 用途：风格提示词 / 负面提示词 / 总时长 / 镜头数 / 元素语义绑定 落库，
--      保证 regenerate（全量重新生成）从 entity 重建请求时不丢这些参数。
--      注意：这些参数同时也会经 dispatchToAgent 实时透传给 agent，
--      本列只服务于「重新生成」链路的参数还原。
ALTER TABLE creative_task ADD COLUMN gen_params_json LONGTEXT DEFAULT NULL COMMENT '可灵式精细控制参数 JSON（风格/负面词/总时长/镜头数/元素绑定）' AFTER segments_json;
