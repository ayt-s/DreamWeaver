package com.dreamweaver.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.dreamweaver.entity.User;
import org.apache.ibatis.annotations.Mapper;

/** 账号 Mapper。简单 CRUD 走 MyBatis-Plus 内置方法。 */
@Mapper
public interface UserMapper extends BaseMapper<User> {
}
