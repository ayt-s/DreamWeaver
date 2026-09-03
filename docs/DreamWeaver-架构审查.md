# DreamWeaver 架构方案审查意见

> 审查人：@hermes-link
> 基于：pluggable-customer-service-agent-v2.md 的状态机设计经验
> 对照：DreamWeaver-架构方案.md v1.0

---

## 一、任务状态机审查

### 1.1 当前状态机定义（方案第4节）

```
PENDING → QUEUED → SUBMITTED → POLLING → COMPLETED / FAILED / EXPIRED
```

**问题1：状态粒度不够，缺少关键节点**

当前状态机有两个"黑盒段"：
- `QUEUED → SUBMITTED`：限流等待 + 实际调用 Agnes API，这两个子阶段的行为完全不同，前端需要区分展示
- `POLLING → COMPLETED/FAILED`：轮询中可能有重试，失败原因多样（参数错误/API限流/超时），需要更细的切分

**建议改为**：

```
┌─────────────────────────────────────────────────────────────────┐
│                        PENDING                                  │
│                        （任务创建，未进入队列）                   │
└────────────────────┬────────────────────────────────────────────┘
                     │ create
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        QUEUED                                   │
│                        （在限流队列中，等待令牌）                  │
│  前端显示：排队中 #N                                            │
└────────────────────┬────────────────────────────────────────────┘
                     │ dequeue + rate_limit_acquire
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        SUBMITTING                               │
│                        （正在调用 Agnes API，等待响应）            │
│  前端显示：提交中...                                            │
└────────────────────┬────────────────────────────────────────────┘
                     │ api_response (video_id received)
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        POLLING                                  │
│                        （每5s轮询 Agnes 查询接口）                │
│  前端显示：生成中 {progress}%                                   │
└───────┬──────────────────┬──────────────────────────────────────┘
        │                  │
        ▼                  ▼
┌──────────────┐   ┌──────────────────────────────────────────┐
│ COMPLETED    │   │  FAILED (可分类)                          │
│              │   │  - PARAM_ERROR: 参数校验失败              │
│ 产物落MinIO  │   │  - RATE_LIMITED: 触发限流（可重试）        │
│ SSE: done    │   │  - API_ERROR: Agnes 服务端错误            │
│              │   │  - TIMEOUT: 轮询超时（可续单）            │
└──────────────┘   │  - EXPIRED: 任务过期                      │
                   └──────────────────────────────────────────┘
                        │ retry?
                        ▼
                   ┌──────────┐
                   │ POLLING  │ ← 重试继续轮询
                   └──────────┘
```

### 1.2 状态枚举实现（Java）

```java
public enum TaskStatus {
    PENDING,        // 刚创建
    QUEUED,         // 进入限流队列
    SUBMITTING,     // 正在调用Agnes API
    POLLING,        // 轮询中
    COMPLETED,      // 完成，产物已落库
    FAILED,         // 失败（需记录errorCode和errorMessage）
    EXPIRED;        // 超时过期
    
    // 允许的状态转移
    public static boolean canTransitionTo(TaskStatus from, TaskStatus to) {
        return Switch.of(from)
            .case_(PENDING,   Queued.of(QUEUED))
            .case_(QUEUED,    Queued.of(SUBMITTING))
            .case_(SUBMITTING, Submitted.of(POLLING, FAILED))
            .case_(POLLING,   Polling.of(COMPLETED, FAILED, EXPIRED))
            .case_(FAILED,    Failed.of(POLLING))  // 可重试
            .build();
    }
}
```

### 1.3 状态持久化建议

状态变更必须写库，不能只放内存：
- 每次状态变更写 `task_status_history` 表（用于排查和问题定位）
- `task` 表存当前状态（快速查询）
- 状态变更事件通过 Spring Event 发布，订阅者处理副作用（如发送SSE通知）

---

## 二、SSE 设计审查

### 2.1 当前方案的问题

方案第4节只写了 `GET /api/tasks/{id}/events`，但缺少：
1. **连接管理**：单用户多标签页如何隔离？
2. **断线重连**：前端 reconnect 后如何补漏？
3. **事件格式**：具体传什么字段？

### 2.2 完整 SSE 设计

**连接隔离**：每个 sessionId + taskId 绑定一个 SSE 通道

```java
// Spring WebSocket/SSE 实现
@Controller
public class TaskEventController {
    
    private final SseEmitterRegistry emitterRegistry;
    
    @GetMapping("/api/tasks/{taskId}/events")
    public SseEmitter events(
        @PathVariable String taskId,
        @RequestParam(defaultValue = "0") long lastEventId
    ) {
        // 1. 创建 SseEmitter（设置超时）
        SseEmitter emitter = new SseEmitter(60_000L);
        
        // 2. 注册到该用户的连接池
        emitterRegistry.register(taskId, emitter);
        
        // 3. 补发遗漏事件（断线重连）
        List<TaskEvent> missedEvents = taskEventService.getMissedEvents(
            taskId, lastEventId
        );
        for (TaskEvent event : missedEvents) {
            emitter.send(SseEmitter.event()
                .id(String.valueOf(event.getEventId()))
                .data(event)
            );
        }
        
        // 4. 完成/错误时自动注销
        emitter.onCompletion(() -> emitterRegistry.unregister(taskId, emitter));
        emitter.onTimeout(() -> emitterRegistry.unregister(taskId, emitter));
        
        return emitter;
    }
}
```

**事件数据结构**：

```json
{
  "id": 1001,
  "taskId": "task-uuid",
  "type": "progress",
  "timestamp": 1696123456789,
  "data": {
    "status": "POLLING",
    "progress": 45,
    "queuedPosition": null,
    "retryCount": 0,
    "error": null
  }
}
```

**事件类型**：

| type | 含义 | 前端行为 |
|------|------|----------|
| `queued` | 进入队列 | 显示排队序号 |
| `submitted` | 提交成功 | 显示"生成中" |
| `progress` | 轮询进度 | 更新进度条 |
| `completed` | 完成 | 展示视频，隐藏进度 |
| `failed` | 失败 | 显示失败原因，提供重试按钮 |
| `expired` | 超时 | 提示续单 |

### 2.3 前端 SSE 连接管理

```typescript
// React hook 示例
function useTaskEvents(taskId: string, onEvent: (event: TaskEvent) => void) {
  useEffect(() => {
    const evtSource = new EventSource(`/api/tasks/${taskId}/events`);
    
    evtSource.onmessage = (e) => {
      const event = JSON.parse(e.data);
      onEvent(event);
    };
    
    evtSource.onerror = () => {
      // 断线重连，带 lastEventId
      evtSource.close();
      setTimeout(() => {
        const newSource = new EventSource(
          `/api/tasks/${taskId}/events?lastEventId=${lastEventId}`
        );
        // ...
      }, 1000);
    };
    
    return () => evtSource.close();
  }, [taskId]);
}
```

---

## 三、方案缺口与补全建议

### 3.1 缺少：任务取消机制

用户排队后想取消怎么办？当前状态机没有 `CANCELING` / `CANCELED` 状态。

**建议**：
- 添加 `CANCEL` 状态转换
- 取消请求走 FastAPI 层：若还在 QUEUED/SUBMITTING，直接拒绝；若已在 POLLING，发取消请求给 Agnes（如果支持）或标记为 CANCELED
- 前端显示"取消成功/已无法取消"

### 3.2 缺少：任务重试/续单机制

`FAILED` 状态后用户能做什么？当前方案没说。

**建议**：
- `PARAM_ERROR`：不允许重试，提示修改参数
- `RATE_LIMITED`：自动入队重试（有限次数）
- `API_ERROR`：允许手动重试
- `EXPIRED`：允许续单（重新进入队列）

### 3.3 缺少：批量生成设计

Phase 3 提到批量生成，但没设计。批量场景的特殊性：
- 一个任务组（batch）包含多个子任务
- 整体进度 = 子任务完成数 / 总数
- 批量任务的限流需要特殊处理（不占用单个用户的配额）

**建议数据结构**：

```java
// 批量任务
@Table
class BatchTask {
    String batchId;
    String userId;
    Integer totalItems;
    Integer completedItems;
    BatchStatus status;  // PENDING, RUNNING, COMPLETED, FAILED
}

// 子任务
@Table
class BatchItem {
    String itemId;
    String batchId;
    String taskId;  // 关联到普通task
    Integer sortOrder;
    ItemStatus status;
}
```

### 3.4 缺少：成本统计与配额管理

方案第5.4节提到配额，但没说具体怎么扣减。

**建议**：
- 任务创建时预扣配额（乐观锁）
- 任务完成时结算实际用量（按秒计费）
- 任务失败时返还预扣配额
- 每日/每月用量报表（用于账单）

### 3.5 FastAPI Worker 失败恢复

方案说"503/429 指数退避"，但没说 worker 进程崩溃怎么办。

**建议**：
- Worker 使用分布式锁（Redis RedLock）确保只有一个实例在轮询某个 video_id
- 轮询任务持久化到 DB，重启后可从 `POLLING` 状态恢复
- 使用 `@Scheduled` 定时扫描超期任务（超过 N 分钟仍在 POLLING 的 → EXPIRED）

```java
// Spring Boot 定时任务
@Scheduled(fixedRate = 60_000)
public void scanExpiredPollingTasks() {
    LocalDateTime threshold = LocalDateTime.now().minusMinutes(15);
    List<Task> expired = taskRepository
        .findPollingBefore(threshold);
    for (Task task : expired) {
        task.setStatus(TaskStatus.EXPIRED);
        taskRepository.save(task);
        sseEmitterRegistry.sendEvent(task.getId(), 
            new TaskEvent("expired", task));
    }
}
```

---

## 四、状态机与SSE的交互时序

```
时间轴 ───────────────────────────────────────────────────────►

用户点击"生成"
    │
    ▼
[Java] 创建任务 PENDING → 写DB → 发布TaskCreatedEvent
    │
    ▼
[Java] 事务提交 → 异步入队（Redis Stream）
    │
    ▼
[Java] 发布 TaskQueuedEvent → SSE: {type: queued, position: 3}
    │
    ▼
[Java] 调度器取单 → 调FastAPI
    │
    ▼
[Java] 状态 SUBMITTING → SSE: {type: submitted}
    │
    ▼
[FastAPI] 限流获取令牌 → 调Agnes API → 拿到 video_id
    │
    ▼
[FastAPI] 状态 POLLING → 回调Java → Java更新状态 → SSE: {type: progress, p: 0%}
    │
    ▼
[FastAPI] 每5s轮询 → 完成 → 下载视频到MinIO
    │
    ▼
[FastAPI] 回调Java /internal/notify → Java: COMPLETED → SSE: {type: completed, url: "..."}
    │
    ▼
[前端] 收到completed事件 → 展示视频，隐藏进度条
```

---

## 五、总结：需要补充到方案的内容

1. **状态机细化**：PENDING→QUEUED→SUBMITTING→POLLING→COMPLETED/FAILED/EXPIRED，FAILED支持重试分支
2. **SSE完整设计**：事件格式、断线重连、连接隔离
3. **取消机制**：任务取消的状态转换和限制条件
4. **重试/续单机制**：不同失败原因的处理策略
5. **Worker容错**：分布式锁、崩溃恢复、超期扫描
6. **批量生成设计**：BatchTask + BatchItem 双层结构
7. **配额扣减逻辑**：预扣+结算模式

以上几点不影响 Phase 1 的主体开发，但建议在 Phase 1 实现时把状态机预留好扩展点，否则 Phase 2 加取消/重试功能会改动核心逻辑。
