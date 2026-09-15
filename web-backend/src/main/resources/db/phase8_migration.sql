-- Phase 8：任务耗时口径修正（画廊「耗时」= 实际生成耗时，而非首提挂钟时长）
--
-- 背景：前端耗时 = completed_at - created_at，created_at 是「首次提交时刻」且永不改写，
-- 于是排队等待、Agent 停机、看门狗标 interrupted、自动重试冷却、原地重跑的空档
-- 全部计入耗时。实测 id=39：09-14 23:43 提交 → 09-15 12:21 完成，显示 12 时 38 分，
-- 而产物 mp4 全部在 09-15 12:21 生成（真实生成约 9 分钟）。
--
-- 口径：started_at = 最近一次被 Agent 受理（拿到 session_id）的时刻，
-- 新建 / 全量重生 regenerateTask / 段重生 reworkTask 三链路都在 dispatchToAgent 重新打点；
-- 非终态回退（interrupted / queued）清零，迟到完成后前端回退到 created_at 并标注「含等待」。

ALTER TABLE creative_task
    ADD COLUMN started_at DATETIME DEFAULT NULL
    COMMENT '本轮生成起点（Agent 受理时刻）；画廊耗时 = completed_at - started_at'
    AFTER created_at;

-- 存量数据不回填：历史任务没有打点数据，宁可留 NULL 让前端回退到 created_at 并
-- 标注「含排队与中断等待」，也不要回填 created_at 假装是生成耗时（12 小时会被误读成生成慢）。
-- 重跑/重生历史任务时由 dispatchToAgent 重新打点，值自然修正。

-- 修历史脏数据：非终态回退（interrupted → queued）过去未清空 completed_at，
-- 导致「排队中」的任务带着耗时展示（实测 id=36：status=queued 而 completed_at 有值）
UPDATE creative_task
   SET completed_at = NULL
 WHERE completed_at IS NOT NULL
   AND status NOT IN ('completed', 'failed', 'expired');
