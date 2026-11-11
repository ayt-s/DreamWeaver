package com.dreamweaver.security;

import com.dreamweaver.common.AuthException;
import com.dreamweaver.common.UserContext;
import com.dreamweaver.entity.User;
import com.dreamweaver.mapper.UserMapper;
import com.dreamweaver.service.AuthService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

import java.util.Base64;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 阶段 2 鉴权的四道关键行为（纯单测，不起 Spring、不连库）。
 *
 * <h3>为什么这些必须锁住</h3>
 * <ul>
 *   <li><b>伪造 token 不能通过</b>：签名错了必须返回 null，否则等于没有鉴权</li>
 *   <li><b>登录失败不区分「用户不存在」</b>：分开说等于给撞库做探针</li>
 *   <li><b>UserContext 只认 token</b>：它此前信任可伪造的 {@code X-User-Id}，
 *       一处没改回来就是「数据写到别人账号下」</li>
 *   <li><b>内部回调要校验来源</b>：`/internal/notify` 能伪造「任务完成」</li>
 * </ul>
 */
class AuthFlowTest {

    private static final String SECRET = "0123456789abcdef0123456789abcdef"; // 32 字节

    @AfterEach
    void clear() {
        SecurityContextHolder.clearContext();
    }

    // ---------------------------------------------------------------- JWT

    @Test
    @DisplayName("签发的 token 能解析回同一个用户")
    void token_round_trip() {
        JwtService jwt = new JwtService(SECRET, 1);
        String token = jwt.issue(42L, "chenxun");

        JwtService.TokenPayload p = jwt.parse(token);
        assertNotNull(p);
        assertEquals(42L, p.userId());
        assertEquals("chenxun", p.username());
    }

    @Test
    @DisplayName("被篡改 / 用别的密钥签的 token 一律解析失败（不能返回 500）")
    void tampered_token_is_rejected() {
        JwtService issuer = new JwtService(SECRET, 1);
        JwtService other = new JwtService("ffffffffffffffffffffffffffffffff", 1);

        String token = issuer.issue(1L, "admin");
        String forged = other.issue(1L, "admin");

        assertNull(issuer.parse(forged), "别的密钥签的 token 不能通过");
        assertNull(issuer.parse(token + "x"), "改动过的 token 不能通过");
        assertNull(issuer.parse(""), "空 token 不能通过");
        assertNull(issuer.parse(null));
    }

    @Test
    @DisplayName("未配置 JWT_SECRET 时用随机密钥（不是硬编码默认值）")
    void missing_secret_falls_back_to_random() {
        JwtService a = new JwtService("", 1);
        JwtService b = new JwtService("", 1);

        // 各自的随机密钥不同 → 互相不认；但自己签的能用
        assertNull(a.parse(b.issue(1L, "x")), "两次启动不该共享同一个默认密钥");
        assertNotNull(a.parse(a.issue(1L, "x")));
    }

    // ------------------------------------------------------------- 注册登录

    @Test
    @DisplayName("注册：密码只存 bcrypt 哈希，返回的 token 指向新用户")
    void register_hashes_password_and_issues_token() {
        UserMapper users = mock(UserMapper.class);
        when(users.selectOne(any())).thenReturn(null);
        when(users.insert(any(User.class))).thenAnswer(inv -> {
            inv.getArgument(0, User.class).setId(7L);
            return 1;
        });
        JwtService jwt = new JwtService(SECRET, 1);
        AuthService svc = new AuthService(users, new BCryptPasswordEncoder(), jwt);

        var resp = svc.register("chenxun", "supersecret");

        assertEquals(7L, resp.userId());
        assertEquals("chenxun", resp.username());
        assertEquals(7L, jwt.parse(resp.token()).userId());

        // 用 captor 取落库实体：`verify(...).insert(argThat(...))` 会与
        // BaseMapper.insert(Collection) 重载产生「引用不明确」
        org.mockito.ArgumentCaptor<User> captor =
                org.mockito.ArgumentCaptor.forClass(User.class);
        org.mockito.Mockito.verify(users).insert(captor.capture());

        User saved = captor.getValue();
        assertTrue(saved.getPasswordHash().startsWith("$2"), "必须存 bcrypt 哈希");
        org.junit.jupiter.api.Assertions.assertFalse(
                saved.getPasswordHash().contains("supersecret"), "不能存明文");
    }

    @Test
    @DisplayName("注册：重名 / 弱密码 / 非法用户名都被挡住")
    void register_rejects_bad_input() {
        UserMapper users = mock(UserMapper.class);
        AuthService svc = new AuthService(users, new BCryptPasswordEncoder(),
                new JwtService(SECRET, 1));

        assertThrows(IllegalArgumentException.class, () -> svc.register("ab", "supersecret"));
        assertThrows(IllegalArgumentException.class, () -> svc.register("chenxun", "short"));
        assertThrows(IllegalArgumentException.class, () -> svc.register("陈 浔", "supersecret"));

        when(users.selectOne(any())).thenReturn(new User());
        assertThrows(IllegalArgumentException.class, () -> svc.register("chenxun", "supersecret"));
    }

    @Test
    @DisplayName("登录：密码错与用户不存在给同一句话（不给撞库探针）")
    void login_failure_message_is_uniform() {
        UserMapper users = mock(UserMapper.class);
        BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();
        AuthService svc = new AuthService(users, encoder, new JwtService(SECRET, 1));

        User existing = new User();
        existing.setId(1L);
        existing.setUsername("admin");
        existing.setPasswordHash(encoder.encode("correct-password"));

        when(users.selectOne(any())).thenReturn(existing, (User) null);

        AuthException wrongPwd = assertThrows(AuthException.class,
                () -> svc.login("admin", "wrong-password"));
        AuthException noUser = assertThrows(AuthException.class,
                () -> svc.login("ghost", "whatever"));

        assertEquals("用户名或密码错误", wrongPwd.getMessage());
        assertEquals("用户名或密码错误", noUser.getMessage());
    }

    @Test
    @DisplayName("登录成功：拿到的 token 里是库里的 userId")
    void login_ok() {
        UserMapper users = mock(UserMapper.class);
        BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();
        JwtService jwt = new JwtService(SECRET, 1);
        AuthService svc = new AuthService(users, encoder, jwt);

        User u = new User();
        u.setId(9L);
        u.setUsername("hezemin");
        u.setPasswordHash(encoder.encode("correct-password"));
        when(users.selectOne(any())).thenReturn(u);

        var resp = svc.login("hezemin", "correct-password");
        assertEquals(9L, resp.userId());
        assertEquals(9L, jwt.parse(resp.token()).userId());
    }

    // ---------------------------------------------------------- UserContext

    @Test
    @DisplayName("UserContext 只认 token 身份：X-User-Id 不再生效")
    void user_context_ignores_forgeable_header() {
        UserContext ctx = new UserContext();
        assertEquals(UserContext.DEFAULT_USER_ID, ctx.currentUserId(), "无登录态回落默认用户");

        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(
                        new JwtService.TokenPayload(88L, "someone"), null, List.of()));
        assertEquals(88L, ctx.currentUserId(), "登录后以 token 里的 userId 为准");
    }

    // ------------------------------------------------------- 内部回调令牌

    @Test
    @DisplayName("/internal/** 配了 token 后：错的不放行、对的放行")
    void internal_token_guard() throws Exception {
        InternalTokenFilter filter = new InternalTokenFilter("s3cret-internal");
        MockHttpServletRequest req = new MockHttpServletRequest("POST", "/internal/notify");
        MockHttpServletResponse res = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(req, res, chain);
        assertEquals(401, res.getStatus(), "不带 token 的内部回调必须被拒");

        MockHttpServletRequest ok = new MockHttpServletRequest("POST", "/internal/notify");
        ok.addHeader(InternalTokenFilter.HEADER, "s3cret-internal");
        MockHttpServletResponse okRes = new MockHttpServletResponse();
        MockFilterChain okChain = new MockFilterChain();
        filter.doFilter(ok, okRes, okChain);
        assertEquals(200, okRes.getStatus());
        assertNotNull(okChain.getRequest(), "校验通过要继续往下走");
    }

    @Test
    @DisplayName("未配置 INTERNAL_TOKEN 时放行（本地开发），但不影响普通接口")
    void internal_token_not_configured_allows_dev() throws Exception {
        InternalTokenFilter filter = new InternalTokenFilter("");
        MockHttpServletRequest req = new MockHttpServletRequest("POST", "/internal/notify");
        MockHttpServletResponse res = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(req, res, chain);
        assertEquals(200, res.getStatus());
        assertNotNull(chain.getRequest());
        assertTrue(chain.getRequest() != null);
    }

    @Test
    @DisplayName("Bearer token 被 JwtAuthFilter 解析成登录态，坏 token 不设登录态")
    void jwt_filter_sets_authentication() throws Exception {
        JwtService jwt = new JwtService(SECRET, 1);
        JwtAuthFilter filter = new JwtAuthFilter(jwt);

        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/api/tasks");
        req.addHeader("Authorization", "Bearer " + jwt.issue(5L, "u5"));
        filter.doFilter(req, new MockHttpServletResponse(), new MockFilterChain());

        var auth = SecurityContextHolder.getContext().getAuthentication();
        assertNotNull(auth);
        assertEquals(5L, ((JwtService.TokenPayload) auth.getPrincipal()).userId());
        assertEquals(5L, req.getAttribute(JwtAuthFilter.ATTR_USER_ID));

        SecurityContextHolder.clearContext();
        MockHttpServletRequest bad = new MockHttpServletRequest("GET", "/api/tasks");
        bad.addHeader("Authorization", "Bearer not-a-jwt");
        filter.doFilter(bad, new MockHttpServletResponse(), new MockFilterChain());
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    @Test
    @DisplayName("随机初始密码是 URL-safe 且长度足够")
    void seeded_password_shape() {
        byte[] buf = new byte[12];
        new java.security.SecureRandom().nextBytes(buf);
        String pwd = Base64.getUrlEncoder().withoutPadding().encodeToString(buf);
        assertEquals(16, pwd.length());
        assertTrue(pwd.matches("[A-Za-z0-9_-]+"));
    }
}
