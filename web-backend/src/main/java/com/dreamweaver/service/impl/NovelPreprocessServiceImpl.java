package com.dreamweaver.service.impl;

import com.dreamweaver.dto.CanvasProjectView;
import com.dreamweaver.dto.NovelPreprocessRequest;
import com.dreamweaver.dto.NovelProjectResponse;
import com.dreamweaver.dto.NovelSegment;
import com.dreamweaver.dto.NovelSegmentUpdateRequest;
import com.dreamweaver.dto.PreparedStoryboard;
import com.dreamweaver.dto.ToCanvasResult;
import com.dreamweaver.entity.CanvasProject;
import com.dreamweaver.entity.NovelProject;
import com.dreamweaver.mapper.NovelProjectMapper;
import com.dreamweaver.service.CanvasProjectService;
import com.dreamweaver.service.NovelPreprocessService;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
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

    /** agent 预处理超时（秒）：切章 + 综合分析 + 分镜 + 拼装，实测 30~90s，给足余量 */
    private static final long PREPROCESS_TIMEOUT_S = 180;

    /**
     * 预处理。
     *
     * <p>⚠️ 刻意**不加** {@code @Transactional}：方法体内有最长 180s 的同步 HTTP 调用，
     * 包在事务里会长时间占着数据库连接（并发几个请求就能打满连接池）。
     * 方法内只有最后一条 insert，本身不需要事务边界。</p>
     */
    @Override
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
            // 空 = 自动：由 agent 侧 analyzer 分析出的 visual_style 决定
            body.put("style", req.getVisualStyle() == null ? "" : req.getVisualStyle().trim());
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
                    .timeout(Duration.ofSeconds(PREPROCESS_TIMEOUT_S))
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
        } catch (java.net.http.HttpTimeoutException e) {
            // HttpTimeoutException 是 IOException 的子类，必须排在前面单独捕
            errorMsg = "预处理超时（" + PREPROCESS_TIMEOUT_S + " 秒）：agent-service 未在规定时间内返回";
            log.warn("novel preprocess 超时（{}s）", PREPROCESS_TIMEOUT_S);
        } catch (IOException | InterruptedException e) {
            if (e instanceof InterruptedException) Thread.currentThread().interrupt();
            errorMsg = "调用 agent-service 失败: " + e.getMessage();
            log.error("novel preprocess HTTP 调用失败", e);
        }

        if (storyboard != null) {
            p.setAnalysisJson(buildAnalysisJson(storyboard));
            p.setSegmentsJson(safeJson(storyboard.getSegments()));
            // 用 agent 实际生效的风格（空入参时即 AI 识别的 visual_style）；
            // 回退 DEFAULT_STYLE 只为兜底「agent 未返回该字段」的情况
            String styleFromAgent = storyboard.getVisualStyle();
            p.setVisualStyle(styleFromAgent == null || styleFromAgent.isBlank()
                    ? DEFAULT_STYLE : styleFromAgent.trim());
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
    @Transactional
    public void delete(Long id) {
        NovelProject p = mapper.selectById(id);
        if (p == null) {
            throw new IllegalArgumentException("小说项目不存在: " + id);
        }
        mapper.deleteById(id);
        log.info("novel 项目删除: id={} name={}（关联画布 {} 不受影响）",
                id, p.getProjectName(), p.getCanvasProjectId());
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
        r.setFidelity(extractFidelity(p.getAnalysisJson()));
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
    public ToCanvasResult saveToCanvas(Long novelProjectId, String characterRefs, String sceneRefs,
            boolean force, boolean saveAsNew) {
        NovelProject p = mapper.selectById(novelProjectId);
        if (p == null) {
            throw new IllegalArgumentException("小说项目不存在: " + novelProjectId);
        }
        List<NovelSegment> segments = parseSegments(p.getSegmentsJson());
        if (segments == null || segments.isEmpty()) {
            throw new IllegalArgumentException("该项目尚未生成分镜，无法同步到画布");
        }

        // 布局：图片行 y=60 起每行 3 列，成片节点在右侧
        int[] colX = {120, 460, 800};
        int imageY = 60;
        int rowStep = 200;
        int composeX = 1300;
        int composeY = 200;

        List<Map<String, Object>> nodes = new ArrayList<>();
        for (int i = 0; i < segments.size(); i++) {
            NovelSegment seg = segments.get(i);
            int x = colX[i % 3];
            int row = i / 3;

            // 图片节点（type 必须匹配前端 nodeTypes 注册的 "imageNode"，字段名 ratio/imageUrl 也要匹配）
            Map<String, Object> imgNode = new LinkedHashMap<>();
            imgNode.put("id", "img" + i);
            imgNode.put("type", "imageNode");
            imgNode.put("position", Map.of("x", x, "y", imageY + row * rowStep));
            Map<String, Object> imgData = new LinkedHashMap<>();
            imgData.put("prompt", seg.getImagePrompt());
            imgData.put("ratio", "16:9");
            imgData.put("imageUrl", "");
            // 结构化镜头写进 cameraSpec：画布节点的「景别/机位/运镜」下拉即可预填。
            // 不写的话用户在画布上看到三行「不指定」，会以为预处理没给镜头信息
            // （镜头描述其实在 prompt 文本里，但被超长的 [角色锚] 挤到卡片折叠之外了）。
            Map<String, Object> cameraSpec = new LinkedHashMap<>();
            putIfNotBlank(cameraSpec, "shot_size", seg.getShotSize());
            putIfNotBlank(cameraSpec, "angle", seg.getAngle());
            putIfNotBlank(cameraSpec, "movement", seg.getMovement());
            if (!cameraSpec.isEmpty()) {
                imgData.put("cameraSpec", cameraSpec);
            }
            imgNode.put("data", imgData);
            nodes.add(imgNode);
        }

        // 成片节点（type 用 "videoNode"——前端唯一注册的成片组件，UI 显示"成片·长视频合成"）
        // 所有 imageNode 连到它，提交成片时 agent 按 x 坐标顺序逐段生成视频并 xfade 拼接
        Map<String, Object> composeNode = new LinkedHashMap<>();
        composeNode.put("id", "compose");
        composeNode.put("type", "videoNode");
        composeNode.put("position", Map.of("x", composeX, "y", composeY));
        Map<String, Object> composeData = new LinkedHashMap<>();
        composeData.put("seconds", 5);
        composeNode.put("data", composeData);
        nodes.add(composeNode);

        List<Map<String, Object>> edges = new ArrayList<>();
        for (int i = 0; i < segments.size(); i++) {
            edges.add(Map.of("id", "e" + i + "-ic", "source", "img" + i, "target", "compose"));
        }

        // 统一成 {nodes:[...]} 包装（前端 serializeCanvas 也是这个格式）。
        // 此前写裸数组，两种格式混在库里，外部脚本按 {nodes} 解析就会炸。
        String nodesJson = "{\"nodes\":" + safeJson(nodes) + "}";
        String edgesJson = safeJson(edges);

        // 幂等：项目已绑定画布 → 复用更新；否则新建。
        // （此前无条件 createProject，点 N 次「转入画布」就在库里留 N 个同名项目）
        CanvasProject target = null;
        if (p.getCanvasProjectId() != null) {
            target = canvasProjectService.getProject(p.getCanvasProjectId(), DEFAULT_USER_ID);
            if (target != null) {
                log.info("novel -> canvas 复用已有画布: novelId={} canvasId={}",
                        p.getId(), p.getCanvasProjectId());
            }
        }
        // 覆盖保护：「转入」本身是确定性的（同一份分镜产出同样的 JSON），所以内容不等 =
        // 画布被改过（手工调整，或上次转的是另一版分镜）。此时静默覆盖会吞掉手工成果。
        if (target != null && !force && !saveAsNew && !sameJson(target.getNodesJson(), nodesJson)) {
            log.info("novel -> canvas 需确认覆盖: novelId={} canvasId={} 现有节点={} 本次节点={}",
                    p.getId(), target.getId(), countNodes(target.getNodesJson()), nodes.size());
            ToCanvasResult ask = new ToCanvasResult();
            ask.setNeedConfirm(true);
            ask.setCanvasId(target.getId());
            ask.setCanvasName(target.getProjectName());
            ask.setCanvasNodeCount(countNodes(target.getNodesJson()));
            ask.setCanvasUpdatedAt(target.getUpdatedAt());
            ask.setIncomingNodeCount(nodes.size());
            return ask;
        }

        if (target == null || saveAsNew) {
            // 新建 / 另存为新画布（保留原画布不动）
            target = canvasProjectService.createProject(
                    saveAsNew ? copyName(p.getProjectName()) : p.getProjectName(),
                    DEFAULT_USER_ID);
        }
        // 锚定图一并落库：刷新画布/换设备都还在（此前只走 URL query + localStorage）
        // 名字：普通转入沿用小说名（既有语义）；另存为副本时保留刚生成的副本名 ——
        // 否则 saveProject 会把它改回小说名，用户分不清哪张是哪张。
        String canvasName = saveAsNew ? target.getProjectName() : p.getProjectName();
        // 内部覆盖语义：不传 expectedVersion（版本校验由上层「转入画布」确认框把关）
        CanvasProjectView saved = canvasProjectService.saveProject(
                target.getId(), DEFAULT_USER_ID, canvasName, nodesJson, edgesJson,
                characterRefs, sceneRefs, null).getCanvas();

        // 回填 canvasProjectId 关联
        NovelProject patch = new NovelProject();
        patch.setId(p.getId());
        patch.setCanvasProjectId(saved.id());
        mapper.updateById(patch);

        log.info("novel -> canvas 同步完成: novelId={} canvasId={} nodes={} edges={}",
                p.getId(), saved.id(), nodes.size(), edges.size());

        ToCanvasResult ok = new ToCanvasResult();
        ok.setCanvas(saved);
        return ok;
    }

    /**
     * 两份 nodesJson 是否一致。
     * <p>先串比较（快路径），不等再解析成节点数组比较 —— 历史数据是裸数组、
     * 新数据是 {@code {nodes:[...]}} 包装，格式差异不该被误判成「画布被改过」。</p>
     */
    private boolean sameJson(String a, String b) {
        if (a == null || b == null) return false;
        if (a.trim().equals(b.trim())) return true;
        try {
            return nodesOf(a).equals(nodesOf(b));
        } catch (Exception e) {
            return false;
        }
    }

    /** 取 nodesJson 里的节点数组（兼容 {nodes:[...]} 与裸数组）。 */
    private JsonNode nodesOf(String json) throws IOException {
        JsonNode root = OM.readTree(json);
        return root.isArray() ? root : root.get("nodes");
    }

    /** 读画布 nodesJson 的节点数（兼容 {nodes:[...]} 与裸数组两种历史格式）。 */
    private int countNodes(String nodesJson) {
        if (nodesJson == null || nodesJson.isBlank()) {
            return 0;
        }
        try {
            JsonNode root = OM.readTree(nodesJson);
            JsonNode arr = root.isArray() ? root : root.get("nodes");
            return arr == null || !arr.isArray() ? 0 : arr.size();
        } catch (Exception e) {
            log.warn("画布 nodesJson 解析失败: {}", e.getMessage());
            return 0;
        }
    }

    /** 另存为新画布时的名字：base · 副本 / 副本2 / 副本3…（避开同名）。 */
    private String copyName(String base) {
        List<String> names = new ArrayList<>();
        for (CanvasProject c : canvasProjectService.listProjects(DEFAULT_USER_ID)) {
            names.add(c.getProjectName());
        }
        String name = base + " · 副本";
        for (int i = 2; names.contains(name) && i < 100; i++) {
            name = base + " · 副本" + i;
        }
        return name;
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

    /** 从 analysis_json 里取出 fidelity（老数据没有该键 → null，前端不提示）。 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> extractFidelity(String analysisJson) {
        if (analysisJson == null || analysisJson.isBlank()) {
            return null;
        }
        try {
            Map<String, Object> analysis = OM.readValue(analysisJson, Map.class);
            Object f = analysis.get("fidelity");
            return f instanceof Map ? (Map<String, Object>) f : null;
        } catch (Exception e) {
            log.warn("analysis_json 解析 fidelity 失败: {}", e.getMessage());
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
        // 忠实度结论一起落 analysis_json：刷新/换设备后仍能在「转入画布」前提示（不必新增列）
        analysis.put("fidelity", sb.getFidelity());
        return safeJson(analysis);
    }

    private List<NovelSegment> safeSegments(PreparedStoryboard sb) {
        return sb.getSegments() != null ? sb.getSegments() : new ArrayList<>();
    }

    /** 仅当值非空时写入 map（空串/空白视为「不指定」） */
    private static void putIfNotBlank(Map<String, Object> map, String key, String value) {
        if (value != null && !value.isBlank()) {
            map.put(key, value.trim());
        }
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
