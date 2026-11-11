package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 账号（阶段 2 用户体系，2026-11-10）。
 *
 * <p>历史数据全部归属 {@code id = 1}（见 {@code UserContext.DEFAULT_USER_ID}），
 * 所以初始化时会把 1 号用户建出来（{@code config/UserSeeder}），否则老用户登录后
 * 看到的是一片空画布。</p>
 */
@Data
@TableName("users")
public class User {

    @TableId(type = IdType.AUTO)
    private Long id;

    private String username;

    /** bcrypt 哈希，**绝不存明文** */
    private String passwordHash;

    private LocalDateTime createdAt;
}
