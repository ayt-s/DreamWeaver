package com.dreamweaver.config;

import com.baomidou.mybatisplus.core.handlers.MetaObjectHandler;
import org.apache.ibatis.reflection.MetaObject;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * MyBatis-Plus 自动填充处理器：所有 insert 自动填 version/isDraft/createdAt/updatedAt，
 * 避免各处手动设默认值（submitNewTask 已有冗余设置作为兜底）。
 */
@Component
public class MyMetaObjectHandler implements MetaObjectHandler {

    @Override
    public void insertFill(MetaObject metaObject) {
        // version 列 NOT NULL DEFAULT 0：MyBatis-Plus insert 时若字段为 null 违反约束
        if (this.getFieldValByName("version", metaObject) == null) {
            this.setFieldValByName("version", 0, metaObject);
        }
        // isDraft 列 NOT NULL DEFAULT 1（新生成物默认进草稿区，人工确认后转成品）
        if (this.getFieldValByName("isDraft", metaObject) == null) {
            this.setFieldValByName("isDraft", 1, metaObject);
        }
        // createdAt / updatedAt
        if (this.getFieldValByName("createdAt", metaObject) == null) {
            this.setFieldValByName("createdAt", LocalDateTime.now(), metaObject);
        }
        if (this.getFieldValByName("updatedAt", metaObject) == null) {
            this.setFieldValByName("updatedAt", LocalDateTime.now(), metaObject);
        }
    }

    @Override
    public void updateFill(MetaObject metaObject) {
        // 统一刷新 updatedAt
        this.setFieldValByName("updatedAt", LocalDateTime.now(), metaObject);
    }
}
