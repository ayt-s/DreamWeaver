package com.dreamweaver.controller;

import com.dreamweaver.dto.HeartbeatRequest;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.service.NotifyService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * FastAPI 回调接收 Controller。
 * 只做事务性更新 + 日志，不写业务逻辑。
 */
@RestController
@RequestMapping("/internal")
@RequiredArgsConstructor
public class NotifyController {

    private final NotifyService notifyService;

    /**
     * FastAPI 完成回调入口。
     * 幂等设计：video_id + shot_index 组合键 + 乐观锁防乱序覆盖。
     */
    @PostMapping("/notify")
    public void handleNotify(@RequestBody NotifyRequest request) {
        notifyService.handleCompletion(request);
    }

    /**
     * Agent 心跳续期入口（长任务不被固定 TTL 误杀）。
     * 按 session_id 重置看门狗；找不到任务或已终态也返回 200，
     * Agent 侧不关心结果，心跳异常不能影响其生成流程。
     */
    @PostMapping("/heartbeat")
    public void handleHeartbeat(@RequestBody HeartbeatRequest request) {
        notifyService.handleHeartbeat(request);
    }
}
