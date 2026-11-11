package com.dreamweaver.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 注册 / 登录请求体（阶段 2）。
 *
 * <p>注册时 {@code password} 有长度下限；登录时不校验长度（老账号可能短），
 * 统一走 {@code AuthService} 的规则，避免「登录接口把注册规则当参数校验」而 400。</p>
 */
public record AuthRequest(
        @NotBlank(message = "用户名不能为空")
        @Size(max = 32, message = "用户名最长 32 个字符")
        String username,

        @NotBlank(message = "密码不能为空")
        @Size(max = 72, message = "密码最长 72 个字符")  // bcrypt 只取前 72 字节
        String password
) {
}
