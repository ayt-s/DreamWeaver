package com.dreamweaver.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * 保护 {@code /internal/**}（agent → Java 的回调）。
 *
 * <p><b>为什么需要</b>：这些接口此前**没有任何防伪** —— 任何人 POST
 * {@code /internal/notify} 就能伪造「任务完成」并把任意 URL 写进结果，
 * 或者调 {@code /internal/quota} 改配额。阶段 1 之前靠"只在本机跑"糊过去，
 * 一旦对外就成立刻被利用的入口。</p>
 *
 * <p><b>开关</b>：{@code INTERNAL_TOKEN} 未配置时**保持放行**（本地开发不用改启动脚本），
 * 但会打一次显著 WARN。生产必须配置，届时 agent 侧的 `INTERNAL_TOKEN` 要一致。</p>
 */
@Slf4j
@Component
public class InternalTokenFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Internal-Token";

    private final byte[] expected;
    private volatile boolean warned;

    public InternalTokenFilter(@Value("${dreamweaver.internal.token:}") String token) {
        this.expected = token == null ? new byte[0]
                : token.trim().getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith("/internal/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res,
                                    FilterChain chain) throws ServletException, IOException {
        if (expected.length == 0) {
            if (!warned) {
                warned = true;
                log.warn("/internal/** 未配置 INTERNAL_TOKEN —— 回调接口当前**不校验来源**，"
                         + "仅可用于本地开发；上线前必须设置该环境变量（agent 侧同名）。");
            }
            chain.doFilter(req, res);
            return;
        }
        String given = req.getHeader(HEADER);
        byte[] actual = given == null ? new byte[0] : given.trim().getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(expected, actual)) {
            log.warn("拒绝未授权的内部回调: {} {} from {}", req.getMethod(), req.getRequestURI(),
                     req.getRemoteAddr());
            res.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            res.setContentType("application/json;charset=UTF-8");
            res.getWriter().write("{\"code\":401,\"message\":\"内部接口鉴权失败\",\"data\":null}");
            return;
        }
        chain.doFilter(req, res);
    }
}
