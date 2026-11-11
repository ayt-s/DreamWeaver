package com.dreamweaver.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Date;

/**
 * JWT 签发与校验（阶段 2）。
 *
 * <p><b>密钥来源</b>：{@code JWT_SECRET} 环境变量。未配置时**生成一个随机的**（不是硬编码默认值）：
 * 开发期能直接跑，但进程重启后旧 token 全部失效，日志里会明确警告。
 * 生产必须显式配置，否则多实例之间签出的 token 互不认。</p>
 */
@Slf4j
@Service
public class JwtService {

    private static final String CLAIM_USERNAME = "username";

    private final SecretKey key;
    private final long expireHours;

    public JwtService(@Value("${dreamweaver.auth.jwt-secret:}") String secret,
                      @Value("${dreamweaver.auth.jwt-expire-hours:72}") long expireHours) {
        this.expireHours = expireHours > 0 ? expireHours : 72;
        String material = secret == null ? "" : secret.trim();
        if (material.length() < 32) {
            // 48 字节随机 → Base64 后 64 字符，满足 HS256 的 32 字节下限
            byte[] random = new byte[48];
            new SecureRandom().nextBytes(random);
            material = Base64.getEncoder().encodeToString(random);
            log.warn("未配置 JWT_SECRET：已生成临时随机密钥 —— **进程重启后所有登录态失效**。"
                     + "生产环境必须显式设置 JWT_SECRET（≥32 字符）。");
        }
        this.key = Keys.hmacShaKeyFor(material.getBytes(StandardCharsets.UTF_8));
    }

    /** 签发 token（sub = userId）。 */
    public String issue(long userId, String username) {
        long now = System.currentTimeMillis();
        return Jwts.builder()
                .subject(String.valueOf(userId))
                .claim(CLAIM_USERNAME, username)
                .issuedAt(new Date(now))
                .expiration(new Date(now + expireHours * 3600_000L))
                .signWith(key)
                .compact();
    }

    /**
     * 校验并解析 token。
     *
     * @return 解析结果；**签名不对 / 过期 / 结构损坏一律返回 null**（不抛异常，调用方按未登录处理）
     */
    public TokenPayload parse(String token) {
        if (token == null || token.isBlank()) {
            return null;
        }
        try {
            Claims c = Jwts.parser().verifyWith(key).build()
                    .parseSignedClaims(token).getPayload();
            long uid = Long.parseLong(c.getSubject());
            return new TokenPayload(uid, String.valueOf(c.get(CLAIM_USERNAME)));
        } catch (JwtException | IllegalArgumentException e) {
            // 过期/伪造/格式错误：都不是「服务出错」，不需要 500
            log.debug("token 校验失败: {}", e.getMessage());
            return null;
        }
    }

    /** token 里的身份。 */
    public record TokenPayload(long userId, String username) {
    }
}
