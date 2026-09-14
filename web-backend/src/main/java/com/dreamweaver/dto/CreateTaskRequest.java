package com.dreamweaver.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

/**
 * 创建视频任务请求 dto。
 * Phase 1 只收 prompt；画幅/时长/档位等参数 Phase 2 加（由能力目录驱动校验）。
 */
@Data
public class CreateTaskRequest {

    @NotBlank(message = "prompt 不能为空")
    @Size(max = 2000, message = "prompt 过长（≤2000）")
    private String prompt;

    /** 用户 ID（数字字符串；空 = 游客）。@Pattern 防止非数字字符串导致 Long.valueOf NFE */
    @Pattern(regexp = "(?:\\d+)?", message = "userId 必须为数字")
    private String userId;

    /** 生成类型：text_video(纯文本视频)/image_video(图生视频)/text_image(文生图) */
    private String genType;

    /** 用户上传的参考图片 URL 数组（图生视频模式） */
    private String referenceImages;

    /**
     * 无限画布图生视频：片段数组 JSON 字符串 [{image_url, prompt, seconds}]。
     * 每段一张参考图 + 一段视频内容描述，生成几秒小视频后由模型侧拼接成长视频。
     */
    private String segments;

    /** 可选视频模型（空 = agent 配置默认 agnes-video-2.5-flash），如 agnes-video-2.5 */
    private String videoModel;

    /**
     * 图片合成视频：图片 URL 数组 JSON 字符串。
     * 非空时 agent 走 image_slideshow 节点（ffmpeg 幻灯片拼接），不消耗 agnes 额度。
     */
    private String slideshowImages;

    /** 图片合成视频：单张停留秒数（1~10，默认 3） */
    private Double slideSeconds;

    // === 可灵式精细控制（全链路透传给 agent） ===

    /** 全局风格提示词（如「3D写实国漫风，虚幻5，OC渲染」）；折进每镜提示词正文 */
    private String stylePrompt;

    /** 负面提示词（如「手指畸形、穿模、水印」）；agnes 无此字段，折成「避免出现：…」 */
    private String negativePrompt;

    /** 时间轴：期望总时长（秒）。给了则覆盖 LLM 对时长的自由估计 */
    private Integer totalSeconds;

    /** 时间轴：期望镜头数。给了则约束分镜数量，每镜时长 = 总时长 / 镜头数 */
    private Integer shotCount;

    /**
     * 全局运镜倾向 JSON 字符串：{shot_size, angle, movement}（标准模式用）。
     * agent 翻译成确定性英文运镜片段注入每镜提示词；画布模式用段级 cameraSpec，不受此影响。
     */
    private String shotLanguage;

    /**
     * 元素语义绑定 JSON 字符串：[{name, imageIndex}]，imageIndex 为 1-based。
     * agent 转成 agnes reference 模式的 &lt;Picture N&gt; 占位符，保证角色/道具跨镜一致。
     */
    private String referenceBindings;
}