package com.dreamweaver.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.dreamweaver.entity.Task;
import org.apache.ibatis.annotations.Mapper;

/**
 * 任务 Mapper。简单 CRUD 用 MyBatis-Plus 内置方法；
 * 复杂 SQL（如断点恢复扫描「status 不在终态」）写在 mapper/TaskMapper.xml。
 */
@Mapper
public interface TaskMapper extends BaseMapper<Task> {
}