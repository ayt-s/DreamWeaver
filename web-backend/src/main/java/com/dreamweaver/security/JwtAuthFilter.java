package com.dreamweaver.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

/**
 * 把 {@code Authorization: Bearer <token>} 解析成登录态（阶段 2）。
 *
 * <p>解析失败**不在这里返回 401**：交给 {@code SecurityConfig} 的入口点统一回 401，
 * 这样「未登录」和「接口不存在」不会混淆。放行匿名请求也让登录接口本身能通过。</p>
 */
@Component
@RequiredArgsConstructor
public class JwtAuthFilter extends OncePerRequestFilter {

    /** 解析出的 userId 同时放进 request attribute，供 UserContext 兜底读取 */
    public static final String ATTR_USER_ID = "dw.userId";

    private final JwtService jwtService;

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res,
                                    FilterChain chain) throws ServletException, IOException {
        String header = req.getHeader("Authorization");
        if (header != null && header.startsWith("Bearer ")) {
            JwtService.TokenPayload payload = jwtService.parse(header.substring(7).trim());
            if (payload != null) {
                var auth = new UsernamePasswordAuthenticationToken(
                        payload, null, List.of(new SimpleGrantedAuthority("ROLE_USER")));
                SecurityContextHolder.getContext().setAuthentication(auth);
                req.setAttribute(ATTR_USER_ID, payload.userId());
            }
        }
        chain.doFilter(req, res);
    }
}
