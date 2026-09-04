package com.dreamweaver.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.nio.file.Paths;

/**
 * 静态资源映射：上传目录（app.upload-dir）经 /api/uploads/** 对外提供，
 * 供无限画布预览与 Agent 侧取图。
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Value("${app.upload-dir:./uploads}")
    private String uploadDir;

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        String absolute = Paths.get(uploadDir).toAbsolutePath().toString().replace("\\", "/");
        if (!absolute.endsWith("/")) {
            absolute += "/";
        }
        registry.addResourceHandler("/api/uploads/**")
                .addResourceLocations("file:" + absolute);
    }
}