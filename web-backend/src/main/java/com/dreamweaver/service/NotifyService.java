package com.dreamweaver.service;

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
}
