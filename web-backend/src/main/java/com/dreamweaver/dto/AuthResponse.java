package com.dreamweaver.dto;

/**
 * 登录/注册成功的返回体。
 *
 * <p>{@code userId} 一并返回，前端不必解 token；但**服务端永远以 token 里的 uid 为准**
 * （前端传什么都不可信）。</p>
 */
public record AuthResponse(String token, long userId, String username) {
}
