package com.dreamweaver.service.impl;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * 「按段重生」的段组装：把用户勾选的段标为待重生、未勾选的段写回复用字段。
 *
 * <p>从 {@link TaskServiceImpl#doReworkCore} 抽出。抽出的理由不是「代码整洁」，
 * 而是这段逻辑承载过两个真实 bug、却因为藏在 {@code private} 方法里而无法单测：
 *
 * <ol>
 *   <li><b>索引错位</b>（提交 {@code 949cf41}）：历史产物与段数不匹配时，
 *       按前缀错位对齐会把产物挂到错误的段上 —— 表现为「重生 A 段结果 B 段变了」</li>
 *   <li><b>缺产物的段被静默跳过</b>：曾把「没有历史产物」的段当成「可复用」，
 *       于是它永远不会被重生（用户点了重生但那段没变）</li>
 * </ol>
 *
 * <p>核心不变式：**未勾选的段只有在确实拿到历史产物时才复用**；拿不到就必须
 * 补进重生列表（{@link ReworkPlan#effectiveRework()} 会成为「实际重生段」的口径）。
 *
 * <p>图片类任务与视频类任务的产物来源不同：图片在 {@code image_urls} 字段、
 * 视频在 {@code result_json}。复用时写回的键也不同
 * （{@code existing_image_url} vs {@code existing_video_url}）——
 * 下游 agent 的 {@code video.py} 只认 {@code existing_video_url}，
 * 画布/图片链路认 {@code existing_image_url}，写错键等于没复用。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class SegmentReworkPlanner {

    /** 产物落在 {@code image_urls} 字段的生成类型（其余走 {@code result_json}） */
    private static final Set<String> IMAGE_GEN_TYPES = Set.of("text_image", "comic_video");

    /** 复用字段名（下游 agent 按这两个键决定是否跳过重新生成） */
    static final String KEY_EXISTING_VIDEO = "existing_video_url";
    static final String KEY_EXISTING_IMAGE = "existing_image_url";
    /** 改了中文描述后旧译文失效，必须删掉让 agent 重新翻译 */
    static final String KEY_PROMPT_EN = "prompt_en";

    private final TaskJsonCodec taskJsonCodec;

    /** 该生成类型的产物是否在 {@code image_urls} 字段 */
    public static boolean isImageTask(String genType) {
        return genType != null && IMAGE_GEN_TYPES.contains(genType);
    }

    /**
     * 组装混合模式段配置。
     *
     * @param taskId         仅用于日志
     * @param segmentsJson   原段配置（提交时落库的 JSON）
     * @param genType        生成类型（决定产物字段与复用键）
     * @param resultJson     视频类历史产物（{@code result_json}）
     * @param imageUrls      图片类历史产物（{@code image_urls}）
     * @param reworkIndices  用户勾选要重生的段索引
     * @param editedPrompts  勾选段的新中文描述（key = 段索引字符串；可为 null）
     */
    public ReworkPlan plan(Long taskId,
                           String segmentsJson,
                           String genType,
                           String resultJson,
                           String imageUrls,
                           List<Integer> reworkIndices,
                           Map<String, String> editedPrompts) {

        List<Map<String, Object>> segs = taskJsonCodec.parseSegments(segmentsJson);
        boolean imageTask = isImageTask(genType);

        // 已有产物：图片任务读 image_urls，视频任务读 result_json。
        // ⚠️ parseResultUrls 会剔除首元素「拼接成片」，所以得到的**不是**原始数组——
        //    这正是它与段索引对齐的前提（成片不是一个可重生的段）。
        List<String> existingUrls = imageTask
                ? taskJsonCodec.parseImageUrls(imageUrls)
                : taskJsonCodec.parseResultUrls(resultJson);

        if (existingUrls.size() != segs.size()) {
            log.warn("重生成段：历史产物与段数不匹配，按索引尽力对齐（id={} 段数={} 已有{}={}），"
                            + "无产物段将自动补重生",
                    taskId, segs.size(), imageTask ? "图片" : "视频", existingUrls.size());
        }

        Set<Integer> reworkSet = new HashSet<>(reworkIndices == null
                ? Collections.emptyList() : reworkIndices);
        // 用户勾选段 + 因缺失历史产物而被迫重生的段（TreeSet：日志里顺序稳定）
        Set<Integer> effectiveRework = new TreeSet<>();
        List<Map<String, Object>> out = new ArrayList<>();

        for (int i = 0; i < segs.size(); i++) {
            Map<String, Object> seg = new HashMap<>(segs.get(i));
            if (reworkSet.contains(i)) {
                applyEditedPrompt(seg, editedPrompts, i);
                // ⚠️ 重生段**无条件**清掉 prompt_en（即便是没改描述的情况）：
                //    它是「用户修改前的中文描述」的旧译文，重生场景下不能信任。
                //    （原实现同样是无条件删除，重构时别把它挪进 if 里 —— 会回归。）
                seg.remove(KEY_PROMPT_EN);
                clearReuseFields(seg);
                effectiveRework.add(i);
            } else if (i < existingUrls.size() && isUsable(existingUrls.get(i))) {
                // 未勾选且有历史产物 → 复用（下游不再重新生成，省额度）
                seg.put(imageTask ? KEY_EXISTING_IMAGE : KEY_EXISTING_VIDEO, existingUrls.get(i));
            } else {
                // 未勾选但**拿不到**可复用的历史产物（历史上生成失败/产物缺失/索引越界）：
                // 必须视为需要重生 —— 否则这一段永远修不好
                seg.remove(KEY_PROMPT_EN);
                clearReuseFields(seg);
                effectiveRework.add(i);
            }
            out.add(seg);
        }

        if (effectiveRework.size() > reworkSet.size()) {
            log.warn("重生成段：以下段缺少可复用历史产物，已自动补入重生列表 id={} 补重生段={}",
                    taskId, effectiveRework);
        }

        return new ReworkPlan(out, taskJsonCodec.toJsonString(out), effectiveRework);
    }

    /** 用用户改过的中文描述覆盖 prompt（清 prompt_en 由调用方统一做，见上面的说明） */
    private static void applyEditedPrompt(Map<String, Object> seg,
                                          Map<String, String> editedPrompts, int index) {
        String edited = editedPrompts == null ? null : editedPrompts.get(String.valueOf(index));
        if (edited == null || edited.isBlank()) {
            return;
        }
        seg.put("prompt", edited);
    }

    private static void clearReuseFields(Map<String, Object> seg) {
        seg.remove(KEY_EXISTING_VIDEO);
        seg.remove(KEY_EXISTING_IMAGE);
    }

    /**
     * 把段配置里的**运行期复用键**剥干净（落库基线专用）。
     *
     * <p>★ 2026-09-19 修：`doReworkCore` 此前把带复用键的段配置**同时**用于派发与落库，
     * 于是 `existing_video_url` 永久写进了 `segments_json`；而它是「按段重生」的输入源，
     * 下一次**全量重生**会把这份基线原样发给 agent，agent 见到该键就
     * `continue`（`nodes/video.py`「该段已有视频 URL → 直接复用，不提交 agnes」）
     * ⇒ 用户点「全量重生」只有上次勾选过的段真的重跑，其余段静默复用旧视频。
     * 复用键**只对「本次段重生」的那一次派发有意义**，绝不能进基线。
     *
     * <p>实测污染证据：库中 id=54/38/36/29 的 `segments_json` 均含 `existing_video_url`
     * （LOCATE 命中 987/1628/837/456）。
     */
    public String stripReuseKeys(String segmentsJson) {
        if (segmentsJson == null || segmentsJson.isBlank()) {
            return segmentsJson;
        }
        List<Map<String, Object>> segs = taskJsonCodec.parseSegments(segmentsJson);
        for (Map<String, Object> seg : segs) {
            clearReuseFields(seg);
        }
        return taskJsonCodec.toJsonString(segs);
    }

    private static boolean isUsable(String url) {
        return url != null && !url.isBlank();
    }

    /**
     * 组装结果。
     *
     * @param segments        新段配置（已按索引对齐）
     * @param segmentsJson    {@code segments} 的落库 JSON
     * @param effectiveRework 实际会重生的段（用户勾选 + 自动补充）
     */
    public record ReworkPlan(List<Map<String, Object>> segments,
                             String segmentsJson,
                             Set<Integer> effectiveRework) {

        /** 复用的段数（日志用） */
        public int reusedCount() {
            return segments.size() - effectiveRework.size();
        }
    }
}
