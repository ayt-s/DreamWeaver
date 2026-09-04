package com.dreamweaver.dto;

import jakarta.validation.constraints.NotBlank;
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
}