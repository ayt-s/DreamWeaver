package com.dreamweaver.config;

import com.dreamweaver.entity.User;
import com.dreamweaver.mapper.UserMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.Base64;

/**
 * 初始化 1 号账号（阶段 2）。
 *
 * <p><b>为什么非要 1 号</b>：阶段 1 的所有历史数据（画布、任务、小说项目、配额）
 * 都挂在 {@code user_id = 1} 上。如果不把它建出来，用户登录后看到的是一片空画布，
 * 会以为数据丢了。</p>
 *
 * <p>空表时插入 admin（自增必然拿到 id=1）；表里已有数据但不含 1 号时，
 * **只报错不擅自创建**（自增不会再给 1，硬插会与现有主键冲突）。</p>
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class UserSeeder implements ApplicationRunner {

    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;

    @Value("${dreamweaver.auth.admin-init-password:}")
    private String initPassword;

    @Override
    public void run(ApplicationArguments args) {
        Long count = userMapper.selectCount(null);
        if (count != null && count > 0) {
            if (userMapper.selectById(1L) == null) {
                log.error("users 表已有 {} 条记录，但**没有 1 号用户** —— 阶段 1 的历史数据"
                          + "（canvas/task/novel）全都归属 user_id=1，这些数据当前无人可见。"
                          + "请手工补一条 id=1 的账号或迁移数据归属。", count);
            }
            return;
        }

        boolean generated = initPassword == null || initPassword.isBlank();
        String password = generated ? randomPassword() : initPassword.trim();

        User admin = new User();
        admin.setUsername("admin");
        admin.setPasswordHash(passwordEncoder.encode(password));
        admin.setCreatedAt(LocalDateTime.now());
        userMapper.insert(admin);

        if (generated) {
            // 只在**生成**时打印一次；配置了 ADMIN_INIT_PASSWORD 就不回显
            log.warn("已创建初始账号 admin / {} —— 请登录后立刻修改。"
                     + "（下次启动不会重复创建；想指定初始密码请设置 ADMIN_INIT_PASSWORD）",
                     password);
        } else {
            log.info("已创建初始账号 admin（密码取自 ADMIN_INIT_PASSWORD）");
        }
    }

    private String randomPassword() {
        byte[] buf = new byte[12];
        new SecureRandom().nextBytes(buf);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(buf);
    }
}
