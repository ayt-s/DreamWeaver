package com.dreamweaver.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.dreamweaver.entity.CanvasProject;
import org.apache.ibatis.annotations.Mapper;

/**
 * 画布项目 Mapper。简单 CRUD 用 MyBatis-Plus 内置方法。
 */
@Mapper
public interface CanvasProjectMapper extends BaseMapper<CanvasProject> {
}