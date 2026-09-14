package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import com.dreamweaver.dto.HeartbeatRequest;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.service.NotifyService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

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
     * 按 session_id 重置看门狗，并回 {"tracked": bool} 告知该会话是否仍被认领
     * ——Agent 据此中止已被用户「全量重生 / 删除」的旧会话，避免白烧 agnes 额度。
     * 找不到任务或已终态也返回 200，心跳异常不能影响 Agent 的生成流程。
     */
    @PostMapping("/heartbeat")
    public CommonResult<Map<String, Object>> handleHeartbeat(@RequestBody HeartbeatRequest request) {
        return CommonResult.ok(notifyService.handleHeartbeat(request));
    }
}
