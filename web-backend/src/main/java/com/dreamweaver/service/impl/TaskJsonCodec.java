package com.dreamweaver.service.impl;

import com.dreamweaver.dto.CreateTaskRequest;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * creative_task 表里几个 JSON 列的编解码：`segments_json` / `result_json` /
 * `image_urls` / `gen_params_json`。
 *
 * <p>为什么从 {@link TaskServiceImpl} 抽出来：这些方法原先全是 {@code private}，
 * 外面测不到 —— 而它们恰好承载过几个真实 bug（成片被当成分段、非法 JSON 抛异常、
 * 全空参数写成 {@code "{}"} 导致「是否配置过」判断失效）。抽成独立类才解锁单测，
 * 顺带消掉原先散在方法里的重复 {@code new ObjectMapper()}。
 *
 * <p>全部方法对坏数据**不抛异常**：列里存的是历史遗留/外部写入的内容，
 * 解析失败应按「没有」处理并记 warn，绝不能因此阻断提交或回调链路。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class TaskJsonCodec {

    /** 本地成片路径前缀：`/v1/files/**` 由 FastAPI 静态目录提供 */
    private static final String FINAL_VIDEO_PREFIX = "/v1/files/";

    private final ObjectMapper objectMapper;

    /**
     * 该 URL 是不是「拼接成片」。
     *
     * <p><b>只认 {@code /v1/files/} 前缀，不按文件名猜。</b>口径与前端
     * {@code finalVideoUrl()} 严格一致（前端类型定义里有完整理由）：
     * <ul>
     *   <li>agnes 侧产物走 CDN（{@code platform-outputs.agnes-ai.space}），
     *       永远不会是本地静态目录 → 本地 {@code /v1/files/} 项**按构造**
     *       只可能是 synthesizer / slideshow 拼出的成片</li>
     *   <li>⚠️ 绝不能再加 {@code endsWith("/final.mp4")} 之类的后缀判定：
     *       分段 URL 若恰好以该串结尾，会被误判成成片并从分段列表里剔除
     *       （前端已因此删掉该条件，Java 侧此前一直残留）</li>
     * </ul>
     */
    public static boolean isFinalVideo(String url) {
        if (url == null) {
            return false;
        }
        return url.trim().startsWith(FINAL_VIDEO_PREFIX);
    }

    /** 解析提交时落库的段配置 JSON 数组 */
    public List<Map<String, Object>> parseSegments(String json) {
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        try {
            List<Map<String, Object>> segs = objectMapper.readValue(json,
                    new TypeReference<List<Map<String, Object>>>() {});
            return segs == null ? new ArrayList<>() : segs;
        } catch (Exception e) {
            log.warn("解析 segments_json 失败: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    /**
     * 把可灵式精细控制参数序列化为 JSON 落库。
     *
     * <p>全空时返回 {@code null}（**不是** {@code "{}"}）—— 这是调用方判断
     * 「用户是否配置过精细控制」的依据，写占位符会让「清空参数」永远不生效。
     */
    public String buildGenParamsJson(CreateTaskRequest request) {
        if (request == null) {
            return null;
        }
        boolean empty = isBlank(request.getStylePrompt())
                && isBlank(request.getNegativePrompt())
                && request.getTotalSeconds() == null
                && request.getShotCount() == null
                && isBlank(request.getShotLanguage())
                && isBlank(request.getReferenceBindings());
        if (empty) {
            return null;
        }
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("stylePrompt", request.getStylePrompt());
        params.put("negativePrompt", request.getNegativePrompt());
        params.put("totalSeconds", request.getTotalSeconds());
        params.put("shotCount", request.getShotCount());
        params.put("shotLanguage", request.getShotLanguage());
        params.put("referenceBindings", request.getReferenceBindings());
        try {
            return objectMapper.writeValueAsString(params);
        } catch (Exception e) {
            log.warn("序列化 gen_params_json 失败: {}", e.getMessage());
            return null;
        }
    }

    /** 从落库的 gen_params_json 还原精细控制参数到请求体（regenerate / rework 共用） */
    public void applyGenParamsJson(String genParamsJson, CreateTaskRequest request) {
        if (genParamsJson == null || genParamsJson.isBlank() || request == null) {
            return;
        }
        try {
            Map<String, Object> params = objectMapper.readValue(genParamsJson,
                    new TypeReference<Map<String, Object>>() {});
            if (params == null) {
                return;
            }
            request.setStylePrompt(asText(params.get("stylePrompt")));
            request.setNegativePrompt(asText(params.get("negativePrompt")));
            request.setTotalSeconds(asInt(params.get("totalSeconds")));
            request.setShotCount(asInt(params.get("shotCount")));
            request.setShotLanguage(asText(params.get("shotLanguage")));
            request.setReferenceBindings(asText(params.get("referenceBindings")));
        } catch (Exception e) {
            log.warn("解析 gen_params_json 失败: {}", e.getMessage());
        }
    }

    /**
     * 解析 `result_json` 为各分段视频 URL。
     *
     * <p>格式有两种：
     * <ul>
     *   <li>拼接成功：{@code [final.mp4, seg0, seg1, ...]} —— 首元素是本地成片，丢弃</li>
     *   <li>拼接失败：{@code [seg0, seg1, ...]} —— synthesizer 兜底透传，无成片，全部保留</li>
     * </ul>
     *
     * <p>⚠️ {@code [final.mp4]}（只有成片、没有分段）必须返回**空列表**：
     * 成片不是一个可重生的分段，把它当分段返回会让「按段重生」多出一段成片。
     */
    public List<String> parseResultUrls(String json) {
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        try {
            List<String> urls = objectMapper.readValue(json, new TypeReference<List<String>>() {});
            if (urls == null || urls.isEmpty()) {
                return new ArrayList<>();
            }
            if (isFinalVideo(urls.get(0))) {
                // 首元素是成片 → 丢弃它；只剩成片时返回空列表（不是把成片当分段）
                return new ArrayList<>(urls.subList(1, urls.size()));
            }
            // 无成片：首元素也是分段，全部返回
            return new ArrayList<>(urls);
        } catch (Exception e) {
            log.warn("解析 result_json 失败: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    /**
     * 解析 `result_json` **原始**数组（不剔除成片首项）。坏数据返回空列表。
     *
     * <p>给「是否已经拼接过」这类判断用：{@link #parseResultUrls} 会把
     * {@code [final, seg0, ...]} 的成片丢掉，看不到首项就判断不了幂等。
     */
    public List<String> parseRawResultUrls(String json) {
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        try {
            List<String> urls = objectMapper.readValue(json, new TypeReference<List<String>>() {});
            return urls == null ? new ArrayList<>() : new ArrayList<>(urls);
        } catch (Exception e) {
            log.warn("解析 result_json（raw）失败: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    /** `result_json` 首项是否已是拼接成片（人工拼接入口的幂等判断） */
    public boolean hasFinalVideo(String json) {
        List<String> urls = parseRawResultUrls(json);
        return !urls.isEmpty() && isFinalVideo(urls.get(0));
    }

    /** 解析 `image_urls` JSON 为图片 URL 列表（容错同 {@link #parseResultUrls}） */
    public List<String> parseImageUrls(String json) {
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        try {
            List<String> urls = objectMapper.readValue(json, new TypeReference<List<String>>() {});
            return urls == null ? new ArrayList<>() : new ArrayList<>(urls);
        } catch (Exception e) {
            log.warn("解析 image_urls 失败: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    /** JSON 序列化（段配置数组落库用）；失败返回空数组字符串，避免阻断提交 */
    public String toJsonString(List<?> list) {
        try {
            return objectMapper.writeValueAsString(list);
        } catch (Exception e) {
            log.error("序列化段配置失败", e);
            return "[]";
        }
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }

    static String asText(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    static Integer asInt(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof Number) {
            return ((Number) value).intValue();
        }
        try {
            return Integer.valueOf(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
