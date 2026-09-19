package com.dreamweaver.config;

import io.netty.channel.ChannelOption;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

import java.time.Duration;

/**
 * WebClient 用于调用 FastAPI（模型侧）。
 *
 * <p>⚠️ 2026-09-19 修（#16）：原先是裸 {@code WebClient.builder()} —— **零超时**。
 * 调用点是 {@code .block()} 且在 {@code @Transactional} 方法内，于是 agent 只要"半死"
 * （TCP 可连、不响应；实测本机日志里就有多次这样的窗口），用户请求会**永挂**：
 * 事务不提交 → {@code creative_task} 该行被 reset UPDATE 锁住 → Hikari 连接被逐个占满
 * → 整个后端不可用。这里补两层下限：
 * <ul>
 *   <li>**连接超时 5s**：目标不可达时快速失败，不再等操作系统默认的分钟级重试。</li>
 *   <li>**响应超时 10min**：兜底上限 —— 保证任何调用都不会永远挂住。
 *       （/concat 是唯一合理的长调用，另有 5 分钟 block 上限，见
 *       {@code TaskServiceImpl.callAgentConcat}，10min 不会误伤它。）</li>
 * </ul>
 * 注意：**快调用不能只靠这个兜底**（10 分钟在事务里仍然致命），所以取消类调用在调用点
 * 自己又加了 10s 的 {@code block} 上限 —— 见 {@code TaskServiceImpl.cancelAgentSession}。
 */
@Configuration
public class WebClientConfig {

    /** 连接超时（毫秒）：不可达时快速失败。 */
    private static final int CONNECT_TIMEOUT_MS = 5_000;

    /** 响应超时：兜底上限，保证不出现"永远挂住"的调用。 */
    private static final Duration RESPONSE_TIMEOUT = Duration.ofMinutes(10);

    @Bean
    public WebClient.Builder webClientBuilder() {
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, CONNECT_TIMEOUT_MS)
                .responseTimeout(RESPONSE_TIMEOUT);
        return WebClient.builder()
                .clientConnector(new ReactorClientHttpConnector(httpClient));
    }
}