package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import com.dreamweaver.common.UserContext;
import com.dreamweaver.dto.AuthRequest;
import com.dreamweaver.dto.AuthResponse;
import com.dreamweaver.entity.User;
import com.dreamweaver.service.AuthService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 账号接口（阶段 2）：注册 / 登录 / 当前用户。
 *
 * <p>前两个在 {@code SecurityConfig} 里放行；{@code /me} 需要登录态，
 * 返回的 userId **来自 token**，不接受前端传入。</p>
 */
@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;
    private final UserContext userContext;

    @PostMapping("/register")
    public CommonResult<AuthResponse> register(@Valid @RequestBody AuthRequest req) {
        return CommonResult.ok(authService.register(req.username(), req.password()));
    }

    @PostMapping("/login")
    public CommonResult<AuthResponse> login(@Valid @RequestBody AuthRequest req) {
        return CommonResult.ok(authService.login(req.username(), req.password()));
    }

    @GetMapping("/me")
    public CommonResult<Map<String, Object>> me() {
        User user = authService.requireUser(userContext.currentUserId());
        return CommonResult.ok(Map.of("userId", user.getId(), "username", user.getUsername()));
    }
}
