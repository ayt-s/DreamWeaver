package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.NotifyService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

/**
 * 回调处理服务实现。
 *
 * <p>幂等策略：
 * 1. 按 video_id + shot_index 组合查找任务
 * 2. 终态检查：completed/failed 直接丢弃，防止晚到的旧回调覆盖新状态
 * 3. 乐观锁：version 不匹配说明有更新的回调已处理，丢弃
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NotifyServiceImpl implements NotifyService {

    private final TaskMapper taskMapper;

    @Override
    @Transactional
    public void handleCompletion(NotifyRequest request) {
        // 1. 幂等检查：按 video_id + shot_index 组合查找
        Task task = taskMapper.selectOne(
            new LambdaQueryWrapper<Task>()
                .eq(Task::getVideoId, request.getVideo_id())
                .eq(request.getShot_index() != null, Task::getShotIndex, request.getShot_index())
        );

        if (task == null) {
            log.warn("notify 收到未知任务: video_id={}, shot_index={}",
                    request.getVideo_id(), request.getShot_index());
            return;
        }

        // 2. 状态机检查：只处理「生成中」的任务
        // completed / failed 直接丢弃，防止晚到的旧回调覆盖新状态
        if ("completed".equals(task.getStatus()) || "failed".equals(task.getStatus())) {
            log.info("notify 任务 {} 已终态 (status={})，丢弃回调",
                    task.getId(), task.getStatus());
            return;
        }

        // 3. 乐观锁更新：version 不匹配说明有更新的回调已处理，丢弃
        int updated = taskMapper.update(null,
            new LambdaUpdateWrapper<Task>()
                .eq(Task::getId, task.getId())
                .eq(Task::getVersion, task.getVersion())  // 乐观锁
                .set(Task::getStatus, request.getStatus())
                .set(Task::getResultJson, request.getVideo_url())
                .set(Task::getUpdatedAt, LocalDateTime.now())
                .set("failed".equals(request.getStatus()),
                        Task::getErrorMessage, request.getError_message())
        );

        if (updated == 0) {
            log.warn("notify 任务 {} 乐观锁冲突，丢弃（已有更新的回调处理过）", task.getId());
            return;
        }

        log.info("notify 任务 {} 处理完成，状态={}", task.getId(), request.getStatus());
    }
}
