package com.dreamweaver.service.impl;

import com.dreamweaver.dto.CreateTaskRequest;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * {@link TaskJsonCodec} 的 L1 单测：纯 POJO，不启 Spring 上下文。
 *
 * <p>用例全部对应**真实出过的 bug**，不是为了覆盖率凑数：
 * <ul>
 *   <li>成片被当成分段（P2-6，前端 {@code c7f695e} 已修、Java 一直没修）</li>
 *   <li>非法 JSON 让解析抛异常 → 阻断回调／提交链路</li>
 *   <li>全空参数写成 {@code "{}"} → 「是否配置过精细控制」永远判为 true</li>
 * </ul>
 */
class TaskJsonCodecTest {

    private final TaskJsonCodec codec = new TaskJsonCodec(new ObjectMapper());

    // ---------------------------------------------------------- 成片判定

    @Test
    @DisplayName("isFinalVideo：只认 /v1/files/ 前缀（与前端 finalVideoUrl 严格同口径）")
    void isFinalVideoDetection() {
        assertTrue(TaskJsonCodec.isFinalVideo("/v1/files/sess/final.mp4"));
        assertTrue(TaskJsonCodec.isFinalVideo("  /v1/files/x/final.mp4  "), "应 trim 后判定");
        // 任何 /v1/files/ 项按构造都是成片：分段永远来自 agnes CDN
        assertTrue(TaskJsonCodec.isFinalVideo("/v1/files/sess/seg_000.mp4"));

        // ⚠️ 关键回归护栏：不能按 /final.mp4 后缀猜 ——
        // 分段 URL 恰好以该串结尾时会被误判成成片、从分段列表里被剔除。
        // 前端已因此删掉后缀条件，Java 侧此前一直残留（F3）。
        assertFalse(TaskJsonCodec.isFinalVideo("https://cdn.agnes-ai.space/o/final.mp4"));
        assertFalse(TaskJsonCodec.isFinalVideo("http://host/a/b/final.mp4"));
        assertFalse(TaskJsonCodec.isFinalVideo("https://cdn/o/final.mp4?v=2"));

        assertFalse(TaskJsonCodec.isFinalVideo(null));
        assertFalse(TaskJsonCodec.isFinalVideo(""));
    }

    @Test
    @DisplayName("parseResultUrls：以 /final.mp4 结尾的分段不能被当成成片剔除（F3 回归）")
    void segmentEndingWithFinalMp4IsKept() {
        // 这是「按文件名猜成片」会造成的真实数据损坏：坏一个 URL 就少一段
        List<String> urls = codec.parseResultUrls(
                "[\"https://cdn.agnes-ai.space/o/final.mp4\",\"https://cdn.agnes-ai.space/o/seg1.mp4\"]");
        assertEquals(2, urls.size(), "两段都应保留，实际: " + urls);
    }

    // ------------------------------------------------------ parseResultUrls

    @Test
    @DisplayName("parseResultUrls：只有成片 [final.mp4] → 空分段列表（不能把成片当分段）")
    void singleFinalVideoYieldsNoSegments() {
        // P2-6：Java 侧原来带 `urls.size() > 1`，单元素成片会被整条返回成分段，
        // 于是「按段重生」多出一段成片。前端已修，这里锁定 Java 口径。
        List<String> urls = codec.parseResultUrls(
                "[\"/v1/files/sess/final.mp4\"]");
        assertTrue(urls.isEmpty(), "成片不是分段，应返回空列表，实际: " + urls);
    }

    @Test
    @DisplayName("parseResultUrls：成片 + 分段 → 丢掉首元素成片，只留分段")
    void finalVideoIsDroppedFromMixedList() {
        List<String> urls = codec.parseResultUrls(
                "[\"/v1/files/sess/final.mp4\",\"https://cdn/seg0.mp4\",\"https://cdn/seg1.mp4\"]");
        assertEquals(List.of("https://cdn/seg0.mp4", "https://cdn/seg1.mp4"), urls);
    }

    @Test
    @DisplayName("parseResultUrls：无成片（拼接失败兜底透传）→ 全量返回")
    void listWithoutFinalVideoIsReturnedWhole() {
        List<String> urls = codec.parseResultUrls(
                "[\"https://cdn/seg0.mp4\",\"https://cdn/seg1.mp4\"]");
        assertEquals(List.of("https://cdn/seg0.mp4", "https://cdn/seg1.mp4"), urls);
    }

    @Test
    @DisplayName("parseResultUrls：坏数据一律返回空列表，不抛异常")
    void parseResultUrlsIsFaultTolerant() {
        assertTrue(codec.parseResultUrls(null).isEmpty());
        assertTrue(codec.parseResultUrls("").isEmpty());
        assertTrue(codec.parseResultUrls("   ").isEmpty());
        assertTrue(codec.parseResultUrls("not json at all").isEmpty());
        assertTrue(codec.parseResultUrls("{}").isEmpty());          // 对象不是数组
        assertTrue(codec.parseResultUrls("[]").isEmpty());
        // [null]：既不是空数组，首元素也不构成成片 → 原样返回（不 NPE）
        assertEquals(1, codec.parseResultUrls("[null]").size());
    }

    // ------------------------------------------------------- parseImageUrls

    @Test
    @DisplayName("parseImageUrls：不过滤成片语义，原样返回；坏数据为空列表")
    void parseImageUrlsKeepsEverything() {
        assertEquals(List.of("https://cdn/a.png", "https://cdn/b.png"),
                codec.parseImageUrls("[\"https://cdn/a.png\",\"https://cdn/b.png\"]"));
        assertTrue(codec.parseImageUrls(null).isEmpty());
        assertTrue(codec.parseImageUrls("坏").isEmpty());
    }

    // -------------------------------------------------------- parseSegments

    @Test
    @DisplayName("parseSegments：解析段配置数组；坏数据为空列表")
    void parseSegmentsBasics() {
        List<Map<String, Object>> segs = codec.parseSegments(
                "[{\"index\":0,\"prompt_en\":\"a cat\"},{\"index\":1,\"prompt_en\":\"a dog\"}]");
        assertEquals(2, segs.size());
        assertEquals("a cat", segs.get(0).get("prompt_en"));

        assertTrue(codec.parseSegments(null).isEmpty());
        assertTrue(codec.parseSegments("").isEmpty());
        assertTrue(codec.parseSegments("[[[").isEmpty());     // 截断的 JSON 数组
        assertTrue(codec.parseSegments("{\"a\":1}").isEmpty()); // 对象不是数组
    }

    // ---------------------------------------------------- buildGenParamsJson

    @Test
    @DisplayName("buildGenParamsJson：全空 → null（不是 \"{}\"）")
    void allEmptyParamsYieldNull() {
        // 这是「用户是否配置过精细控制」的判断依据：写 "{}" 会让「清空参数」永远不生效
        assertNull(codec.buildGenParamsJson(new CreateTaskRequest()));
        assertNull(codec.buildGenParamsJson(null));

        CreateTaskRequest blank = new CreateTaskRequest();
        blank.setStylePrompt("   ");
        blank.setNegativePrompt("");
        assertNull(codec.buildGenParamsJson(blank), "只有空白字符也算空");
    }

    @Test
    @DisplayName("buildGenParamsJson：有一项就给全量 JSON")
    void anySingleFieldProducesJson() {
        CreateTaskRequest req = new CreateTaskRequest();
        req.setShotLanguage("en");
        String json = codec.buildGenParamsJson(req);

        assertNotNull(json);
        assertTrue(json.contains("\"shotLanguage\":\"en\""), "实际: " + json);
        // 未设置的字段也要在（保证结构稳定，applyGenParamsJson 才能整体还原）
        assertTrue(json.contains("\"stylePrompt\":null"), "实际: " + json);
    }

    @Test
    @DisplayName("gen_params_json 往返：build → apply 后各字段一致")
    void genParamsRoundTrip() {
        CreateTaskRequest src = new CreateTaskRequest();
        src.setStylePrompt("赛博朋克");
        src.setNegativePrompt("模糊");
        src.setTotalSeconds(12);
        src.setShotCount(4);
        src.setShotLanguage("zh");
        src.setReferenceBindings("[{\"role\":\"hero\"}]");

        String json = codec.buildGenParamsJson(src);
        CreateTaskRequest restored = new CreateTaskRequest();
        codec.applyGenParamsJson(json, restored);

        assertEquals("赛博朋克", restored.getStylePrompt());
        assertEquals("模糊", restored.getNegativePrompt());
        assertEquals(12, restored.getTotalSeconds());
        assertEquals(4, restored.getShotCount());
        assertEquals("zh", restored.getShotLanguage());
        assertEquals("[{\"role\":\"hero\"}]", restored.getReferenceBindings());
    }

    @Test
    @DisplayName("applyGenParamsJson：空/坏 JSON 不抛异常，也不清掉请求里已有的值")
    void applyGenParamsJsonIsFaultTolerant() {
        CreateTaskRequest req = new CreateTaskRequest();
        req.setStylePrompt("原值");

        codec.applyGenParamsJson(null, req);
        codec.applyGenParamsJson("", req);
        codec.applyGenParamsJson("不是 json", req);

        assertEquals("原值", req.getStylePrompt());
    }

    @Test
    @DisplayName("applyGenParamsJson：数字以字符串形态落库也能还原（历史数据兼容）")
    void applyGenParamsJsonCoercesNumericStrings() {
        CreateTaskRequest req = new CreateTaskRequest();
        codec.applyGenParamsJson("{\"totalSeconds\":\"15\",\"shotCount\":\"3\"}", req);

        assertEquals(15, req.getTotalSeconds());
        assertEquals(3, req.getShotCount());
    }

    // ---------------------------------------------------------- toJsonString

    @Test
    @DisplayName("toJsonString：正常序列化；失败时返回 \"[]\" 而不是抛异常")
    void toJsonStringBasics() {
        assertEquals("[{\"index\":0}]", codec.toJsonString(List.of(Map.of("index", 0))));
        assertEquals("[]", codec.toJsonString(List.of()));

        // 自引用结构 → Jackson 抛递归异常 → 兜底 "[]"，避免阻断提交
        List<Object> selfRef = new java.util.ArrayList<>();
        selfRef.add(selfRef);
        assertEquals("[]", codec.toJsonString(selfRef));
    }
}
