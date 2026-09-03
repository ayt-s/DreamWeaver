package com.dreamweaver.controller;

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
}
