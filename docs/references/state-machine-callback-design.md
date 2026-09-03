# DreamWeaver 项目状态机与回调设计

> 记录 Phase 2 Java 回调的实际实现决策，供后续开发参考。

## 状态机设计（实际 vs 理想）

### 理想设计（Phase 3+）
```
queued → video_generating → completed/failed
         ↑                     ↓
      创建任务            FastAPI 回调
```

### Phase 1 实际（内联轮询）
```
queued → completed/failed
```
**原因**：FastAPI 内联轮询，视频生成完后直接回调 Java，无中间状态通知。

### Phase 2 预留扩展
转移表已包含 `video_generating` 状态，未来改为独立 Poller 后可自然支持完整三态。

## 回调通知时序

```
1. 用户创建任务 → Java INSERT (status='queued')
2. Java POST /v1/tasks/video → FastAPI
3. FastAPI 内联轮询 Agnes API，每 5s 查询一次
4. 生成完成 → FastAPI POST /internal/notify → Java
5. Java NotifyServiceImpl:
   - 按 video_id + shot_index 查找任务
   - 终态检查（completed/failed 直接丢弃）
   - 状态机校验（queued → completed/failed）
   - 聚合 result_json（按 shot_index 写数组）
   - updateById() 触发乐观锁 version+1
```

## 幂等键设计

- **主键**：`video_id + shot_index` 组合
- **video_id**：Agnes 返回的异步任务 ID（格式 `task_xxx`）
- **shot_index**：镜次索引（单镜任务为 null）
- **null 安全**：selectList + stream filter，避免 TooManyResultsException

## 乐观锁实现

- **字段**：`Task.version` 加 `@Version` 注解
- **配置**：`MybatisPlusConfig` 注册 `OptimisticLockerInnerInterceptor`
- **更新方式**：必须用 `updateById(task)`，不能手动写 wrapper
- **冲突处理**：updated=0 时丢弃回调并打日志

## 相关文件

- `agent-service/app/callback/java_notify.py` — 回调通知函数
- `agent-service/app/nodes/video.py` — 视频生成节点（含回调 fire-and-forget）
- `web-backend/src/main/java/com/dreamweaver/service/impl/NotifyServiceImpl.java` — 回调处理核心逻辑
- `web-backend/src/main/java/com/dreamweaver/config/MybatisPlusConfig.java` — 乐观锁配置
