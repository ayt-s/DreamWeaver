package com.dreamweaver.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.dreamweaver.entity.ApiQuota;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

/**
 * API 配额 Mapper。
 */
@Mapper
public interface ApiQuotaMapper extends BaseMapper<ApiQuota> {

    /**
     * 原子累加配额：used_count + 1，used_seconds + deltaSeconds。
     * 若记录不存在则插入（on duplicate key update）。
     */
    @Update("INSERT INTO api_quota (user_id, model_name, used_count, used_seconds, updated_at)"
            + " VALUES (#{userId}, #{modelName}, #{usedCount}, #{usedSeconds}, NOW())"
            + " ON DUPLICATE KEY UPDATE"
            + "   used_count   = used_count   + #{usedCount},"
            + "   used_seconds = used_seconds + #{usedSeconds},"
            + "   updated_at   = NOW()")
    int increment(@Param("userId") Long userId,
                  @Param("modelName") String modelName,
                  @Param("usedCount") int usedCount,
                  @Param("usedSeconds") int usedSeconds);
}
