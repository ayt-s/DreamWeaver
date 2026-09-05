package com.dreamweaver.service.impl;

import com.dreamweaver.dto.CanvasProjectView;
import com.dreamweaver.dto.NovelPreprocessRequest;
import com.dreamweaver.dto.NovelProjectResponse;
import com.dreamweaver.dto.NovelSegment;
import com.dreamweaver.dto.NovelSegmentUpdateRequest;
import com.dreamweaver.dto.PreparedStoryboard;
import com.dreamweaver.entity.CanvasProject;
import com.dreamweaver.entity.NovelProject;
import com.dreamweaver.mapper.NovelProjectMapper;
import com.dreamweaver.service.CanvasProjectService;
import com.dreamweaver.service.NovelPreprocessService;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 小说预处理服务实现。
 * <p>同步调用 agent-service 做分镜拆分；成功后落库并把 status 置 ready；失败置 failed。</p>
 * <p>把预处理结果同步到画布项目：image/video/compose 网格布局。</p>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NovelPreprocessServiceImpl implements NovelPreprocessService {

    private static final String AGENT_URL = "http://localhost:8000/v1/novel/preprocess";
    private static final String DEFAULT_STYLE = "电影写实";
    private static final long DEFAULT_USER_ID = 1L;
    private static final int DEFAULT_SEGMENTS = 6;

    private final NovelProjectMapper mapper;
    private final CanvasProjectService canvasProjectService;

    private static final ObjectMapper OM = buildMapper();

    private static ObjectMapper buildMapper() {
        ObjectMapper m = new ObjectMapper();
        m.registerModule(new JavaTimeModule());
        m.setSerializationInclusion(JsonInclude.Include.NON_NULL);
        m.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
        return m;
    }

    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)  // agent 8000 (uvicorn) 只支持 HTTP/1.1，强制降级避免请求被拒
            .connectTimeout(Duration.ofSeconds(10))
            .build();

    // ========== 1. 预处理 ==========

    @Override
    @Transactional
    public NovelProject preprocess(Long userId, NovelPreprocessRequest req) {
        NovelProject p = new NovelProject();
        p.setUserId(userId != null ? userId : DEFAULT_USER_ID);
        p.setProjectName(req.getProjectName().trim());
        p.setNovelText(req.getNovelText());
        p.setStatus("draft");

        String requestBody;
        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("novel_text", req.getNovelText());
            body.put("target_segments", req.getTargetSegments() != null ? req.getTargetSegments() : 6);
            body.put("seconds_per_segment", req.getSecondsPerSegment() != null ? req.getSecondsPerSegment() : 5);
            body.put("style", DEFAULT_STYLE);
            body.put("generate_character_portrait",
                    req.getGenerateCharacterPortrait() != null && req.getGenerateCharacterPortrait());
            requestBody = OM.writeValueAsString(body);
        } catch (Exception e) {
            p.setStatus("failed");
            p.setErrorMessage("请求构造失败: " + e.getMessage());
            mapper.insert(p);
            return p;
        }

        PreparedStoryboard storyboard = null;
        String errorMsg = null;
        try {
            HttpRequest httpReq = HttpRequest.newBuilder()
                    .uri(URI.create(AGENT_URL))
                    .timeout(Duration.ofSeconds(60))
                    .header("Content-Type", "application/json; charset=utf-8")
                    .POST(HttpRequest.BodyPublishers.ofString(requestBody, java.nio.charset.StandardCharsets.UTF_8))
                    .build();
            HttpResponse<String> resp = HTTP.send(httpReq, HttpResponse.BodyHandlers.ofString());
            int code = resp.statusCode();
            String body = resp.body();
            log.info("agent-service preprocess http={} bodyHead={}",
                    code, body != null ? body.substring(0, Math.min(400, body.length())) : "(null)");
            if (code < 200 || code >= 300) {
                errorMsg = "agent-service 返回 " + code + ": " + truncate(body, 400);
                log.warn("novel preprocess agent 非 2xx: {}", errorMsg);
            } else {
                // 解析 {code, message, data:{...}}
                Map<String, Object> wrapper = OM.readValue(body, Map.class);
                Object innerCode = wrapper.get("code");
                Object innerData = wrapper.get("data");
                if (innerCode == null || !Integer.valueOf(0).equals(toInt(innerCode))) {
                    errorMsg = "agent-service 业务错误: code=" + innerCode
                            + " message=" + wrapper.get("message");
                    log.warn("novel preprocess 业务错误: {}", errorMsg);
                } else {
                    storyboard = OM.convertValue(innerData, PreparedStoryboard.class);
                }
            }
        } catch (IOException | InterruptedException e) {
            if (e instanceof InterruptedException) Thread.currentThread().interrupt();
            errorMsg = "调用 agent-service 失败: " + e.getMessage();
            log.error("novel preprocess HTTP 调用失败", e);
        }

        if (storyboard != null) {
            p.setAnalysisJson(buildAnalysisJson(storyboard));
            p.setSegmentsJson(safeJson(storyboard.getSegments()));
            p.setVisualStyle(DEFAULT_STYLE);
            // 分镜时长回填
            for (NovelSegment seg : safeSegments(storyboard)) {
                if (seg.getSeconds() == null) seg.setSeconds(5);
            }
            p.setStatus("ready");
            p.setErrorMessage(null);
        } else {
            p.setStatus("failed");
            p.setErrorMessage(truncate(errorMsg, 500));
        }

        mapper.insert(p);
        log.info("novel preprocess 落库: id={} status={} name={}",
                p.getId(), p.getStatus(), p.getProjectName());
        return p;
    }

    // ========== 2. 查询 ==========

    @Override
    public NovelProject get(Long id) {
        return mapper.selectById(id);
    }

    @Override
    public java.util.List<NovelProjectResponse> listByUser(Long userId) {
        // 轻量查询：按 updatedAt 倒序，上限 50；只回填 id/name/status/createdAt/updatedAt/visualStyle/canvasProjectId
        var rows = mapper.selectList(
                new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<NovelProject>()
                        .eq(NovelProject::getUserId, userId)
                        .orderByDesc(NovelProject::getUpdatedAt)
                        .last("LIMIT 50")
        );
        java.util.List<NovelProjectResponse> out = new java.util.ArrayList<>(rows.size());
        for (NovelProject p : rows) {
            NovelProjectResponse r = new NovelProjectResponse();
            r.setId(p.getId());
            r.setProjectName(p.getProjectName());
            // 不回填 novelText / chaptersJson / analysisJson / segments，避免大对象
            r.setVisualStyle(p.getVisualStyle());
            r.setCanvasProjectId(p.getCanvasProjectId());
            r.setStatus(p.getStatus());
            r.setCreatedAt(p.getCreatedAt());
            r.setUpdatedAt(p.getUpdatedAt());
            out.add(r);
        }
        return out;
    }

    @Override
    public NovelProjectResponse toResponse(NovelProject p) {
        if (p == null) return null;
        NovelProjectResponse r = new NovelProjectResponse();
        r.setId(p.getId());
        r.setProjectName(p.getProjectName());
        r.setNovelText(p.getNovelText());
        r.setChaptersJson(p.getChaptersJson());
        r.setAnalysisJson(p.getAnalysisJson());
        r.setVisualStyle(p.getVisualStyle());
        r.setCanvasProjectId(p.getCanvasProjectId());
        r.setStatus(p.getStatus());
        r.setErrorMessage(p.getErrorMessage());
        r.setCreatedAt(p.getCreatedAt());
        r.setUpdatedAt(p.getUpdatedAt());
        r.setSegments(parseSegments(p.getSegmentsJson()));
        return r;
    }

    // ========== 3. 更新分镜 ==========

    @Override
    @Transactional
    public NovelProjectResponse updateSegments(Long id, List<NovelSegment> segments) {
        NovelProject p = mapper.selectById(id);
        if (p == null) {
            throw new IllegalArgumentException("小说项目不存在: " + id);
        }
        NovelProject patch = new NovelProject();
        patch.setId(id);
        patch.setSegmentsJson(safeJson(segments));
        mapper.updateById(patch);
        log.info("novel 分镜更新: id={} count={}", id, segments == null ? 0 : segments.size());
        return toResponse(mapper.selectById(id));
    }

    // ========== 4. 同步到画布 ==========

    @Override
    @Transactional
    public CanvasProjectView saveToCanvas(Long novelProjectId) {
        NovelProject p = mapper.selectById(novelProjectId);
        if (p == null) {
            throw new IllegalArgumentException("小说项目不存在: " + novelProjectId);
        }
        List<NovelSegment> segments = parseSegments(p.getSegmentsJson());
        if (segments == null || segments.isEmpty()) {
            throw new IllegalArgumentException("该项目尚未生成分镜，无法同步到画布");
        }

        // 布局：图片行 y=60，视频行 y=460；每行 3 列 x=120/460/800，第二行整体下移 200
        int[] colX = {120, 460, 800};
        int imageY = 60;
        int videoY = 460;
        int rowStep = 200;
        int composeX = 1200;
        int composeY = 400;

        List<Map<String, Object>> nodes = new ArrayList<>();
        for (int i = 0; i < segments.size(); i++) {
            NovelSegment seg = segments.get(i);
            int x = colX[i % 3];
            int row = i / 3;

            // 图片节点
            Map<String, Object> imgNode = new LinkedHashMap<>();
            imgNode.put("id", "img" + i);
            imgNode.put("type", "image");
            imgNode.put("position", Map.of("x", x, "y", imageY + row * rowStep));
            Map<String, Object> imgData = new LinkedHashMap<>();
            imgData.put("type", "image");
            imgData.put("prompt", seg.getImagePrompt());
            imgData.put("aspectRatio", "16:9");
            imgData.put("nodeLabel", "图" + (i + 1));
            imgData.put("imageUrls", new ArrayList<String>());
            imgNode.put("data", imgData);
            nodes.add(imgNode);

            // 视频节点
            Map<String, Object> vidNode = new LinkedHashMap<>();
            vidNode.put("id", "vid" + i);
            vidNode.put("type", "video");
            vidNode.put("position", Map.of("x", x, "y", videoY + row * rowStep));
            Map<String, Object> vidData = new LinkedHashMap<>();
            vidData.put("type", "video");
            vidData.put("prompt", seg.getVideoPrompt());
            vidData.put("aspectRatio", "16:9");
            vidData.put("nodeLabel", "片" + (i + 1));
            vidData.put("seconds", seg.getSeconds() != null ? seg.getSeconds() : 5);
            vidData.put("description", seg.getPlot());
            vidNode.put("data", vidData);
            nodes.add(vidNode);
        }

        Map<String, Object> composeNode = new LinkedHashMap<>();
        composeNode.put("id", "compose");
        composeNode.put("type", "compose");
        composeNode.put("position", Map.of("x", composeX, "y", composeY));
        Map<String, Object> composeData = new LinkedHashMap<>();
        composeData.put("type", "compose");
        composeData.put("nodeLabel", "成片·" + p.getProjectName());
        composeData.put("aspectRatio", "16:9");
        composeNode.put("data", composeData);
        nodes.add(composeNode);

        List<Map<String, Object>> edges = new ArrayList<>();
        for (int i = 0; i < segments.size(); i++) {
            edges.add(Map.of("id", "e" + i + "-iv", "source", "img" + i, "target", "vid" + i));
            edges.add(Map.of("id", "e" + i + "-vc", "source", "vid" + i, "target", "compose"));
        }

        String nodesJson = safeJson(nodes);
        String edgesJson = safeJson(edges);

        // 两步法：先建空项目拿 id，再保存 nodes/edges
        CanvasProject created = canvasProjectService.createProject(p.getProjectName(), DEFAULT_USER_ID);
        CanvasProject saved = canvasProjectService.saveProject(
                created.getId(), DEFAULT_USER_ID, p.getProjectName(), nodesJson, edgesJson);

        // 回填 canvasProjectId 关联
        NovelProject patch = new NovelProject();
        patch.setId(p.getId());
        patch.setCanvasProjectId(saved.getId());
        mapper.updateById(patch);

        log.info("novel -> canvas 同步完成: novelId={} canvasId={} nodes={} edges={}",
                p.getId(), saved.getId(), nodes.size(), edges.size());

        return new CanvasProjectView(
                saved.getId(), saved.getProjectName(), saved.getUpdatedAt(),
                saved.getNodesJson(), saved.getEdgesJson());
    }

    // ========== 工具方法 ==========

    private List<NovelSegment> parseSegments(String json) {
        if (json == null || json.isBlank()) return new ArrayList<>();
        try {
            return OM.readValue(json,
                    OM.getTypeFactory().constructCollectionType(List.class, NovelSegment.class));
        } catch (Exception e) {
            log.warn("segments_json 解析失败: {}", e.getMessage());
            return new ArrayList<>();
        }
    }

    private String safeJson(Object obj) {
        if (obj == null) return null;
        try {
            return OM.writeValueAsString(obj);
        } catch (Exception e) {
            log.warn("JSON 序列化失败: {}", e.getMessage());
            return null;
        }
    }

    private String buildAnalysisJson(PreparedStoryboard sb) {
        Map<String, Object> analysis = new LinkedHashMap<>();
        analysis.put("novelSummary", sb.getNovelSummary());
        analysis.put("characters", sb.getCharacters());
        analysis.put("scenes", sb.getScenes());
        analysis.put("totalSegments", sb.getTotalSegments());
        analysis.put("totalDurationSeconds", sb.getTotalDurationSeconds());
        return safeJson(analysis);
    }

    private List<NovelSegment> safeSegments(PreparedStoryboard sb) {
        return sb.getSegments() != null ? sb.getSegments() : new ArrayList<>();
    }

    private static String truncate(String s, int max) {
        if (s == null) return null;
        return s.length() <= max ? s : s.substring(0, max);
    }

    private static Integer toInt(Object o) {
        if (o == null) return null;
        if (o instanceof Number) return ((Number) o).intValue();
        try { return Integer.valueOf(o.toString()); } catch (Exception e) { return null; }
    }
}
