package com.dreamweaver.common;

import com.dreamweaver.security.JwtAuthFilter;
import com.dreamweaver.security.JwtService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

/**
 * 当前用户解析（阶段 1：把散落的硬编码收成一处）。
 *
 * <p><b>为什么需要</b>：{@code DEFAULT_USER_ID = 1L} 此前硬编码在 3 个文件 13 处
 * （CanvasController 6、NovelPreprocessController 2、NovelPreprocessServiceImpl 5）。
 * 以后接真登录时要一处一处翻，而且很容易漏——漏一处就是「数据写到了别人的账号下」。</p>
 *
 * <p><b>阶段 2（2026-11-10）：身份改由 JWT 决定。</b>取值顺序：</p>
 * <ol>
 *   <li>SecurityContext 里的 token 身份（正常请求）</li>
 *   <li>request attribute（过滤链已解析过，兜底）</li>
 *   <li>{@link #DEFAULT_USER_ID}（**没有请求上下文**时才会走到：定时任务、启动恢复、内部任务）</li>
 * </ol>
 *
 * <p><b>⚠️ 已不再信任 {@code X-User-Id} 请求头。</b>它此前可被任意伪造 ——
 * 「用户能看到什么」从阶段 2 起只由签名过的 token 决定。</p>
 */
@Component
public class UserContext {

    /** 单用户阶段的归属用户（与历史数据一致的 1） */
    public static final long DEFAULT_USER_ID = 1L;

    /** 当前请求的用户 id；**优先取 token 里的身份**，无请求上下文时回落默认值。 */
    public Long currentUserId() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof JwtService.TokenPayload payload) {
            return payload.userId();
        }
        HttpServletRequest req = currentRequest();
        if (req != null) {
            Object fromFilter = req.getAttribute(JwtAuthFilter.ATTR_USER_ID);
            if (fromFilter instanceof Long id) {
                return id;
            }
        }
        // 定时任务 / 启动恢复 / 内部调用：没有登录态
        return DEFAULT_USER_ID;
    }

    private HttpServletRequest currentRequest() {
        if (!(RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attrs)) {
            return null;
        }
        return attrs.getRequest();
    }
}
