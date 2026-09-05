package com.dreamweaver.service;

import com.dreamweaver.config.AgentServiceProperties;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.service.impl.NotifyServiceImpl;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Agent 本地 fallback 同步器：agent 回调 Java 失败时会写本地 JSONL，
 * 本组件每 60s 拉取一次并落库，Java 重启期间丢失的回调自动补回。
 *
 * <p>端点：
 * - GET  {agentUrl}/v1/internal/sync-fallback         列出待同步记录
 * - POST {agentUrl}/v1/internal/sync-fallback/ack     标记已同步
 *
 * <p>幂等：NotifyServiceImpl 按 session_id 查任务 + 状态机 + 乐观锁，
 * 重复 ack 不会重复落库。
 *
 * <p>注意：用 HTTP/1.1（uvicorn 只支持 HTTP/1.1，Java 默认 HTTP/2 会 400）。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class FallbackSyncScheduler {

    private final NotifyServiceImpl notifyService;
    private final ObjectMapper objectMapper;
    private final AgentServiceProperties agentProps;

    private final HttpClient http = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    /** 每 60s 拉一次 agent fallback，首次延迟 20s（让 agent 先启动完）。 */
    @Scheduled(fixedDelayString = "${dreamweaver.fallback-sync.interval-ms:60000}",
               initialDelayString = "${dreamweaver.fallback-sync.initial-delay-ms:20000}")
    public void syncFallback() {
        String base = (agentProps.getBaseUrl() == null || agentProps.getBaseUrl().isBlank())
                ? "http://127.0.0.1:8000" : agentProps.getBaseUrl();
        String listUrl = base.replaceFirst("/+$", "") + "/v1/internal/sync-fallback";
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(listUrl))
                    .timeout(Duration.ofSeconds(10))
                    .GET()
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                log.debug("fallback 拉取 HTTP {}，跳过本轮", resp.statusCode());
                return;
            }
            Map<String, Object> body = objectMapper.readValue(resp.body(),
                    new TypeReference<Map<String, Object>>() {});
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> records = (List<Map<String, Object>>) body.get("data");
            if (records == null || records.isEmpty()) {
                return;
            }
            log.info("fallback 拉取到 {} 条待同步记录", records.size());

            List<String> ackedIds = new ArrayList<>();
            for (Map<String, Object> rec : records) {
                String id = String.valueOf(rec.get("id"));
                @SuppressWarnings("unchecked")
                Map<String, Object> payload = (Map<String, Object>) rec.get("payload");
                if (payload == null) {
                    ackedIds.add(id);
                    continue;
                }
                try {
                    NotifyRequest nr = objectMapper.convertValue(payload, NotifyRequest.class);
                    notifyService.handleCompletion(nr);
                    ackedIds.add(id);
                } catch (Exception e) {
                    log.warn("fallback 记录 {} 落库失败（下次重试）: {}", id, e.getMessage());
                }
            }

            if (!ackedIds.isEmpty()) {
                String ackUrl = base.replaceFirst("/+$", "") + "/v1/internal/sync-fallback/ack";
                String ackBody = objectMapper.writeValueAsString(Map.of("ids", ackedIds));
                HttpRequest ackReq = HttpRequest.newBuilder()
                        .uri(URI.create(ackUrl))
                        .timeout(Duration.ofSeconds(10))
                        .header("Content-Type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(ackBody))
                        .build();
                http.send(ackReq, HttpResponse.BodyHandlers.ofString());
                log.info("fallback 已 ack {} 条记录", ackedIds.size());
            }
        } catch (Exception e) {
            log.debug("fallback 同步异常（agent 可能未启动）: {}", e.getMessage());
        }
    }
}
