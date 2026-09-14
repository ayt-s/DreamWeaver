package com.dreamweaver.service;

import com.dreamweaver.dto.HeartbeatRequest;
import com.dreamweaver.dto.NotifyRequest;

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
     * 处理 Agent 心跳：按 session_id 重新武装看门狗 TTL（长任务续期）。
     * 找不到任务或任务已终态时安静返回，不抛异常（不影响 Agent 生成）。
     *
     * @param request 心跳请求体（{"session_id": "..."}）
     */
    void handleHeartbeat(HeartbeatRequest request);
}
