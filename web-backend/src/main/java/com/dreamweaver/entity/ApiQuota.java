package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * API 配额实体（对应 api_quota 表）。
 */
@Data
@TableName("api_quota")
public class ApiQuota {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long userId;

    private String modelName;

    /** 已调用次数 */
    private Integer usedCount;

    /** 已消耗秒数 */
    private Integer usedSeconds;

    private LocalDateTime updatedAt;
}
