-- Phase 9：画布并发保护（乐观锁）
--
-- 场景：两个标签页 / 两台设备同时编辑同一张画布，此前后保存的一方**静默覆盖**前面的改动，
-- 没有任何检测（canvas_project 只有 updated_at，只能看出"被改过"，看不出"改的是哪一版"）。
--
-- 方案：加 version 列 + 条件更新（WHERE version = 期望值），不一致时不写入，
-- 把服务端现状返回给前端，由用户决定「用我的覆盖」还是「放弃我的改动」。
--
-- 不回填历史数据：老项目 version=0，首次保存后自然递增。
ALTER TABLE canvas_project
    ADD COLUMN version INT NOT NULL DEFAULT 0 AFTER scene_refs;
