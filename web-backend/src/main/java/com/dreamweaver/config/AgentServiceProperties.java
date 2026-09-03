package com.dreamweaver.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * FastAPI（模型侧）连接配置，读 application.yml 的 dreamweaver.agent-service。
 */
@Data
@Component
@ConfigurationProperties(prefix = "dreamweaver.agent-service")
public class AgentServiceProperties {

    private String baseUrl;
}