# DreamWeaver 项目进度报告
**时间：** 2026-09-04 00:15  
**截止：** 2026-09-04 05:10  
**仓库：** https://github.com/ayt-s/DreamWeaver.git

---

## 一、完成度概览

| 模块 | 状态 | 负责人 |
|------|------|--------|
| Python agent-service | ✅ 完成 | @hermes |
| Java web-backend | ✅ 完成 | @hermes + @hermes-link |
| React web-frontend | ✅ 完成 | @hermes |
| 设计文档 | ✅ 完成 | @hermes-link |
| 代码审查 | ✅ 通过 | @hermes-review |

---

## 二、Git 提交记录

```
3b7076c feat(frontend): 技术栈升级 + 联调补齐
6e100d8 fix: 回调链路契约修复（review 阻塞项 1/2/3）
efbfeae chore: ignore *.tsbuildinfo, remove build cache from repo
3153825 feat(frontend): React 骨架 — api/components/pages/hooks/store/types
b9bec74 feat(Phase 2): add FastAPI→Java callback notification
4957f69 fix(Phase 2): state machine transfer table instead of whitelist
1fc2e52 fix(Phase 2): fix optimistic lock + state machine + result aggregation
03c8d4b feat(Phase 2): add NotifyController + optimistic lock for callback ordering
3d8dd01 docs: fix NotifyService with optimistic lock for callback ordering
ccc4efe chore: add .env.example template + update gitignore
7318465 feat: .env.example 模板 + README + 统一退避重试到 _with_retry
39ae7ab init: DreamWeaver AI 短视频创作 Agent — Phase 1 骨架
```

---

## 三、三端验证结果

### Python agent-service
- ✅ 测试 3/3 绿（pytest tests/）
- ✅ FastAPI 响应格式对齐 Java CommonResult
- ✅ Agnes API 真实联调成功（拿到成品视频 URL）
- ✅ 429/5xx 指数退避重试已落地

### Java web-backend
- ✅ mvn compile BUILD SUCCESS
- ✅ Layered architecture 完整（controller/service/mapper/entity/dto）
- ✅ NotifyController + NotifyServiceImpl 乐观锁实现
- ✅ 状态转移表语义正确（Map<from, Set<to>>）
- ✅ MybatisPlusConfig 注册 OptimisticLockerInnerInterceptor

### React web-frontend
- ✅ npm run build 通过
- ✅ TypeScript 严格模式无 any
- ✅ Tailwind CSS + TanStack Query + React Hook Form + Zustand
- ✅ TrajectoryPanel 轮询兜底 + 视频播放
- ✅ CreatePanel 表单校验 + 提交

---

## 四、核心修复记录

### 阻断性问题（已全部修复）
1. **Java 查询键错误** → 改按 session_id 查（createTask 唯一回写过的键）
2. **video_id 伪造** → generate_video_tool 返回真实 video_id
3. **多镜数据丢失** → 整会话一次回调带全量 video_urls
4. **乐观锁失效** → 改用 updateById + OptimisticLockerInnerInterceptor
5. **状态机断层** → 转移表支持 queued → completed/failed

### 次要问题（Phase 3 优化）
1. fire-and-forget 无重试 → VideoPoller 接入时补锁+重试
2. SSE 无 lastEventId 补漏
3. events 数组无限增长无截断

---

## 五、架构完整性验证

```
React (5173) ──proxy──▶ Spring Boot (8080) ──REST──▶ FastAPI (8000) ──HTTPS──▶ Agnes AI
                                    ▲                                   │
                                    └────── /internal/notify 回调 ◀──────┘
```

- 端到端数据流完整
- 回调契约对齐（session_id 关联 + 全量 URL 数组）
- 乐观锁 + 状态转移表 + 终态检查三重防护

---

## 六、下一步优化方案

### Phase 3 规划
1. **VideoPoller 独立轮询服务**：FastAPI 不再内联等待，改为 submit 后立即返回，Poller 异步轮询并回调 Java
2. **SSE 实时轨迹**：Java /internal/events 端点，支持断线重连和 lastEventId
3. **OpenCV QC 规则层**：`analyze_video_frames` 工具注册进 LangGraph，做黑帧/模糊/人脸检测
4. **前端画廊页**：已完成视频列表 + 筛选 + 详情
5. **配额管理**：Agnes API 调用次数/时长统计 + 告警

### 面试准备要点
1. **乐观锁 vs 悲观锁**：为什么选乐观锁（回调场景写多读少，冲突率低）
2. **状态转移表设计**：Map<from, Set<to>> 比硬编码 switch 更灵活
3. **幂等键选择**：session_id 而非 video_id（关联主键 vs 审计键）
4. **回调契约演进**：从逐镜通知到整会话通知的取舍

---

## 七、风险点

| 风险 | 缓解措施 |
|------|----------|
| FastAPI 回调失败（Java 宕机） | Phase 3 VideoPoller 兜底 |
| 单镜任务 vs 多镜任务语义差异 | 整会话回调自然兼容 |
| session_id 在 Java 侧唯一性假设 | selectList + stream 取第一条，实际单任务场景无冲突 |

---

**报告完成时间：** 2026-09-04 00:15  
**距离截止：** 约 5 小时 5 分钟  
**建议：** 项目已可进入联调测试阶段，Phase 3 优化可按优先级逐步推进
