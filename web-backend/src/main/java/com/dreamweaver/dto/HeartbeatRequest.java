package com.dreamweaver.dto;

import lombok.Data;

/**
 * Agent 心跳续期请求体（POST /internal/heartbeat）。
 * 长任务生成期间 Agent 周期性上报，Java 侧据此重置看门狗 TTL，避免固定超时误杀。
 */
@Data
public class HeartbeatRequest {

    /** LangGraph 会话 ID（与 /internal/notify 同一关联键） */
    private String session_id;
}
