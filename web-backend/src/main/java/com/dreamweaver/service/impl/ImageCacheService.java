package com.dreamweaver.service.impl;

import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.Optional;

/**
 * 文生图图片 Redis 缓存：画廊/画布展示走本地缓存 URL，避免每次直连 agnes 产物 CDN 加载慢。
 *
 * <p>缓存策略：
 * - key = sha256(原始 URL)，字节存 {@code img:b:&lt;sha&gt;}，content-type 存 {@code img:t:&lt;sha&gt;}，TTL 7 天
 * - 未命中 → 后台从原始 URL 拉取并回填（首访仍然走一次原图，此后全部命中 Redis）
 * - 任务完成后 NotifyServiceImpl 触发预取（warm），用户看画廊时基本零等待
 *
 * <p>重要边界：这里缓存只为「展示」，传给 agnes 的参考图 / 片段 URL 永远用<b>原始地址</b>，
 * 因为 agnes 视频接口要求公网可访问 URL（本地 Redis 服务地址不可达）。
 */
@Slf4j
@Service
public class ImageCacheService {

    private static final Duration TTL = Duration.ofDays(7);
    private static final String BODY_PREFIX = "img:b:";
    private static final String TYPE_PREFIX = "img:t:";
    private static final long MAX_IMAGE_BYTES = 15L * 1024 * 1024;

    private final RedissonClient redisson;
    private final HttpClient http = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)  // 强制 HTTP/1.1，避免 HTTP/2 与不支持的远端握手失败
            .connectTimeout(Duration.ofSeconds(10))
            .followRedirects(HttpClient.Redirect.NORMAL)
            .build();

    public ImageCacheService(RedissonClient redisson) {
        this.redisson = redisson;
    }

    public record ImageResult(byte[] body, MediaType contentType) {
    }

    /** 查缓存；未命中则拉取原始 URL 并回填。返回 null 表示拉取失败。 */
    public ImageResult cached(String url) {
        if (url == null || url.isBlank()) {
            return null;
        }
        String sha = sha256(url);
        RBucket<byte[]> bodyBucket = redisson.getBucket(BODY_PREFIX + sha);
        RBucket<String> typeBucket = redisson.getBucket(TYPE_PREFIX + sha);
        byte[] body = bodyBucket.get();
        String type = typeBucket.get();
        if (body != null && type != null) {
            return new ImageResult(body, MediaType.parseMediaType(type));
        }
        // 未命中：从源头拉取（小程序/图片不会太大，15MB 上限）
        Optional<byte[]> fetched = fetchOrigin(url);
        if (fetched.isEmpty()) {
            log.warn("ImageCache 拉取失败: {}", url);
            return null;
        }
        body = fetched.get();
        type = guessContentType(url);
        bodyBucket.set(body, TTL);
        typeBucket.set(type, TTL);
        log.info("ImageCache 回填: {} -> {} bytes", url, body.length);
        return new ImageResult(body, MediaType.parseMediaType(type));
    }

    /** 预取（异步调用方自行 submit；失败静默，下次展示时再懒加载）。 */
    public void warm(String url) {
        if (url == null || url.isBlank()) {
            return;
        }
        String sha = sha256(url);
        RBucket<byte[]> bodyBucket = redisson.getBucket(BODY_PREFIX + sha);
        if (bodyBucket.get() != null) {
            return;
        }
        Optional<byte[]> fetched = fetchOrigin(url);
        if (fetched.isEmpty() || fetched.get().length == 0) {
            return;
        }
        bodyBucket.set(fetched.get(), TTL);
        redisson.getBucket(TYPE_PREFIX + sha).set(guessContentType(url), TTL);
    }

    private Optional<byte[]> fetchOrigin(String url) {
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .GET()
                    .timeout(Duration.ofSeconds(20))
                    .header("User-Agent", "DreamWeaver-Gallery/1.0")
                    .build();
            HttpResponse<byte[]> resp = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
            if (resp.statusCode() != 200 || resp.body().length == 0
                    || resp.body().length > MAX_IMAGE_BYTES) {
                return Optional.empty();
            }
            return Optional.of(resp.body());
        } catch (Exception e) {
            log.debug("ImageCache 拉取异常 {}: {}", url, e.toString());
            return Optional.empty();
        }
    }

    private String guessContentType(String url) {
        String lower = url.toLowerCase(java.util.Locale.ROOT);
        if (lower.endsWith(".png")) return "image/png";
        if (lower.endsWith(".webp")) return "image/webp";
        if (lower.endsWith(".gif")) return "image/gif";
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
        return "image/jpeg";
    }

    private String sha256(String value) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(64);
            for (byte b : digest) {
                sb.append(Character.forDigit((b >> 4) & 0xF, 16));
                sb.append(Character.forDigit(b & 0xF, 16));
            }
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }
}