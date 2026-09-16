package com.dreamweaver.common;

import jakarta.servlet.http.HttpServletRequest;
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
 * <p><b>取值方式</b>：请求头 {@code X-User-Id}，缺失/非法一律回落 {@link #DEFAULT_USER_ID}。
 * 缺失不报错是刻意的：老前端、脚本、curl 都不带这个头，不能让它们 400。</p>
 *
 * <p><b>⚠️ 这不是鉴权</b>：请求头可以随便伪造。阶段 1 只用它替换常量、不做任何权限判断；
 * 真正的访问控制要等接了用户表 + 登录（阶段 2，届时只改本类，实现从 token 解析）。
 * 在此之前不要把「用户能看到什么」建立在这个值上。</p>
 */
@Component
public class UserContext {

    /** 单用户阶段的归属用户（与历史数据一致的 1） */
    public static final long DEFAULT_USER_ID = 1L;

    /** 当前请求的用户 id；无请求上下文（定时任务/启动期）或头非法时回落默认值。 */
    public Long currentUserId() {
        HttpServletRequest req = currentRequest();
        if (req == null) {
            return DEFAULT_USER_ID;
        }
        String raw = req.getHeader("X-User-Id");
        if (raw == null || raw.isBlank()) {
            return DEFAULT_USER_ID;
        }
        try {
            return Long.parseLong(raw.trim());
        } catch (NumberFormatException e) {
            // 非法值不报错也不注入：回落默认用户，避免把脏值写进库里
            return DEFAULT_USER_ID;
        }
    }

    private HttpServletRequest currentRequest() {
        if (!(RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attrs)) {
            return null;
        }
        return attrs.getRequest();
    }
}
