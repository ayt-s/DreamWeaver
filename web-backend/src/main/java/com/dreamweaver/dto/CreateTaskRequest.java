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
}