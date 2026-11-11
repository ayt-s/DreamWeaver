package com.dreamweaver.common;

/**
 * 认证/授权类错误（映射 401/403）。
 *
 * <p>与 {@link IllegalArgumentException}（映射 400）分开：登录失败不是「参数不合法」，
 * 客户端据此决定「跳登录页」还是「提示改输入」。</p>
 */
public class AuthException extends RuntimeException {

    private final boolean forbidden;

    public AuthException(String message) {
        this(message, false);
    }

    public AuthException(String message, boolean forbidden) {
        super(message);
        this.forbidden = forbidden;
    }

    public boolean isForbidden() {
        return forbidden;
    }
}
