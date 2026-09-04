package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * 图片上传（无限画布图生视频的本地参考图来源）。
 *
 * <p>产物通过 {@code /api/uploads/**} 静态映射对外提供（见 WebConfig）。
 * ⚠️ 平台限制：agnes 生成接口要求参考图为<b>公网可访问 URL</b>，
 * 本地上传图只能用于画布预览；提交生成会被平台拒绝（400）。
 * 历史作品图（agnes 平台产物 URL）不受此限制。
 */
@Slf4j
@RestController
@RequestMapping("/api/uploads")
public class UploadController {

    private static final long MAX_BYTES = 10L * 1024 * 1024;
    private static final Set<String> ALLOWED_EXT = Set.of("jpg", "jpeg", "png", "webp");

    @Value("${app.upload-dir:./uploads}")
    private String uploadDir;

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public CommonResult<Map<String, String>> upload(@RequestParam("file") MultipartFile file) {
        if (file.isEmpty()) {
            throw new IllegalArgumentException("上传文件为空");
        }
        if (file.getSize() > MAX_BYTES) {
            throw new IllegalArgumentException("图片过大（≤10MB）");
        }
        String originalName = file.getOriginalFilename() == null ? "" : file.getOriginalFilename();
        String ext = originalName.contains(".")
                ? originalName.substring(originalName.lastIndexOf('.') + 1).toLowerCase(Locale.ROOT)
                : "";
        if (!ALLOWED_EXT.contains(ext)) {
            throw new IllegalArgumentException("仅支持 jpg/png/webp 图片");
        }

        File dir = new File(uploadDir);
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IllegalStateException("无法创建上传目录: " + uploadDir);
        }
        String fileName = UUID.randomUUID().toString().replace("-", "") + "." + ext;
        File dest = new File(dir, fileName);
        try (var in = file.getInputStream()) {
            java.nio.file.Files.copy(in, dest.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        } catch (Exception e) {
            log.error("上传文件保存失败: {}", e.toString());
            throw new IllegalStateException("上传保存失败: " + e.getMessage());
        }
        String url = "http://localhost:8080/api/uploads/" + fileName;
        log.info("上传成功: {} → {}", originalName, url);
        return CommonResult.ok(Map.of(
                "url", url,
                "name", originalName
        ));
    }
}