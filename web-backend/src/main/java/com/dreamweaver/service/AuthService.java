package com.dreamweaver.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.common.AuthException;
import com.dreamweaver.dto.AuthResponse;
import com.dreamweaver.entity.User;
import com.dreamweaver.mapper.UserMapper;
import com.dreamweaver.security.JwtService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.regex.Pattern;

/**
 * 账号注册与登录（阶段 2）。
 *
 * <p><b>安全要点</b>：
 * <ul>
 *   <li>密码只存 bcrypt 哈希；比较用 {@code matches}，不做字符串比较</li>
 *   <li>登录失败统一文案「用户名或密码错误」，不区分"用户不存在"（否则等于给撞库报错）</li>
 *   <li>用户名唯一性靠 DB 唯一索引兜底（并发注册时先查后插仍有竞态）</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class AuthService {

    private static final Pattern USERNAME = Pattern.compile("^[A-Za-z0-9_\\u4e00-\\u9fa5]{3,32}$");
    private static final int MIN_PASSWORD = 8;

    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;

    public AuthResponse register(String rawUsername, String rawPassword) {
        String username = rawUsername == null ? "" : rawUsername.trim();
        String password = rawPassword == null ? "" : rawPassword;

        if (!USERNAME.matcher(username).matches()) {
            throw new IllegalArgumentException("用户名需 3–32 位，仅限字母、数字、下划线、中文");
        }
        if (password.length() < MIN_PASSWORD) {
            throw new IllegalArgumentException("密码至少 " + MIN_PASSWORD + " 位");
        }
        if (findByUsername(username) != null) {
            throw new IllegalArgumentException("用户名已被占用");
        }

        User user = new User();
        user.setUsername(username);
        user.setPasswordHash(passwordEncoder.encode(password));
        user.setCreatedAt(LocalDateTime.now());
        userMapper.insert(user);

        log.info("新用户注册 id={} username={}", user.getId(), username);
        return issue(user);
    }

    public AuthResponse login(String rawUsername, String rawPassword) {
        String username = rawUsername == null ? "" : rawUsername.trim();
        User user = findByUsername(username);
        // 不区分「用户不存在」与「密码错误」：两者都回同一句
        if (user == null || user.getPasswordHash() == null
                || !passwordEncoder.matches(rawPassword == null ? "" : rawPassword,
                                             user.getPasswordHash())) {
            throw new AuthException("用户名或密码错误");
        }
        return issue(user);
    }

    public User requireUser(long userId) {
        User user = userMapper.selectById(userId);
        if (user == null) {
            throw new AuthException("账号不存在或已删除");
        }
        return user;
    }

    private AuthResponse issue(User user) {
        return new AuthResponse(jwtService.issue(user.getId(), user.getUsername()),
                                user.getId(), user.getUsername());
    }

    private User findByUsername(String username) {
        if (username.isBlank()) {
            return null;
        }
        return userMapper.selectOne(new LambdaQueryWrapper<User>()
                .eq(User::getUsername, username)
                .last("limit 1"));
    }
}
