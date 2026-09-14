package com.dreamweaver.service;

import com.dreamweaver.dto.HeartbeatRequest;
import com.dreamweaver.dto.NotifyRequest;

import java.util.Map;

/**
 * 回调处理服务接口。
 * 接收 FastAPI /internal/notify 回调，幂等更新任务状态。
 */
public interface NotifyService {

    /**
     * 处理视频生成完成回调。
     * 幂等设计：video_id + shot_index 组合键 + 乐观锁防乱序覆盖。
     *
     * @param request 回调请求体
     */
    void handleCompletion(NotifyRequest request);

    /**
     * 处理 Agent 心跳：按 session_id 重新武装看门狗 TTL（长任务续期），
     * 并把「该会话是否仍被认领」回给 Agent。
     *
     * <p>返回值用途（P1-3）：Agent 自动恢复一个会话后，若用户已把任务「全量重生」
     * （换了新 session_id）或删除，原会话继续跑只会白烧 agnes 额度，且结果回调会因
     * session_id 不匹配被丢弃。Agent 据此中止旧会话。
     *
     * <p>不抛异常（不影响 Agent 生成流程），失败一律按「无法判定」处理。
     *
     * @param request 心跳请求体（{"session_id": "..."}）
     * @return {"tracked": true/false}；false = 任务查不到或已终态
     */
    Map<String, Object> handleHeartbeat(HeartbeatRequest request);
}
