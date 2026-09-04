package com.dreamweaver.controller;

import com.dreamweaver.service.impl.ImageCacheService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 文生图产物本地缓存出口：画廊/画布 <img> 用
 * {@code GET /api/images/cache?url=&lt;原始URL&gt;} 展示图片，
 * 命中 Redis 直接返回，未命中回源拉取后回填。
 * 原始 URL 仍保留给 agnes（生成/参考图必须公网可访问）。
 */
@Slf4j
@RestController
@RequestMapping("/api/images")
@RequiredArgsConstructor
public class ImageCacheController {

    private final ImageCacheService imageCacheService;

    @GetMapping("/cache")
    public ResponseEntity<byte[]> cached(@RequestParam("url") String url) {
        ImageCacheService.ImageResult result = imageCacheService.cached(url);
        if (result == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok()
                .contentType(result.contentType())
                .cacheControl(CacheControl.maxAge(java.time.Duration.ofDays(7)))
                .body(result.body());
    }
}