# DreamWeaver — 工程规范

> 适用全部代码模块。面试项目，规范本身就是加分项（面试官翻代码最先看结构）。

## 1. Java（web-backend）分层规范（强制）

```
com.dreamweaver
 ├── controller/     # 只做参数接收 + 响应组装，不写业务逻辑
 ├── service/        # 业务接口
 │    └── impl/      # 业务实现（事务、跨模块编排都在这里）
 ├── mapper/         # MyBatis-Plus Mapper 接口 + XML（SQL 不写进 service）
 ├── entity/         # 数据库实体（与表一一对应）
 ├── dto/            # 请求/响应对象（禁止 entity 直接出参）
 ├── config/         # 配置类（CORS、客户端、拦截器）
 ├── common/         # 统一返回体、异常、工具
 └── DreamWeaverApplication.java
```

### 分层铁律
- **controller → service → mapper → entity**，依赖只允许向下，禁止 controller 直接碰 mapper/entity 仓储
- entity 是数据映射，绝不出现在 controller 返回里；对外一律用 dto
- service 里跨模块业务（如「提交任务 → 调 FastAPI → 落库」）编排，不在 controller 里堆
- mapper XML 里写复杂 SQL；简单 CRUD 用 MyBatis-Plus 内置方法
- 统一返回：`CommonResult<T>{code, message, data}`，controller 不裸返回对象

## 2. Python（agent-service）分层规范（强制）

```
app/
 ├── gateway/    # 外部 API 封装（agnes client），唯一接触第三方的地方
 ├── tools/      # Agent 可调用的工具（MCP 风格，供节点调用）
 ├── nodes/      # LangGraph 节点（一个文件一个节点）
 ├── graph.py    # 图装配（只描述节点/边，不写业务）
 ├── state.py    # State 类型定义
 ├── config.py   # 环境变量配置（密钥只在这里）
 └── main.py     # FastAPI 入口 + 路由
```

### 分层铁律
- 节点不直接碰 agnes API，一律走 gateway / tools
- 图定义（graph.py）不写节点实现，节点不写图结构
- 所有密钥走 config.py 环境变量，禁止散落

## 3. 前端（web-frontend）规范（强制）

```
src/
 ├── api/        # axios 封装 + 接口定义（集中管理 baseURL/interceptor）
 ├── components/ # 可复用组件
 ├── pages/      # 页面级组件（路由挂载）
 ├── hooks/      # 自定义 hooks（SSE 订阅、任务轮询等）
 ├── store/      # 状态管理（zustand/pinia 选型后定）
 └── types/      # TS 类型（与后端 dto 对应）
```

### 规范
- 组件不直接发请求，一律走 `api/`
- SSE 订阅封装成 hook（`useTaskEvents`），组件里只消费
- 无 `any`：接口类型在 `types/` 统一定义

## 4. 通用规范
- 命名：Java/Python `snake_case` 表字段 ↔ `camelCase` 实体；Python 函数 `snake_case`；JS/TS 组件 `PascalCase`
- 配置中心思想：模型名、端点、限流参数一律配置化（Java 侧读 DB `model_config` 表，Python 侧读 config/env），禁止硬编码
- 密钥：任何语言都不许把 API Key 写进代码/前端；Java 侧若需转发密钥，走环境变量注入

## 5. Java↔FastAPI 接口契约（强制）

所有跨服务通信统一使用包裹体，禁止直接返回裸对象。

### 5.1 FastAPI 响应格式

```python
class ApiResponse(BaseModel):
    code: int = 0          # 0=成功，非0=失败
    message: str = "ok"    # 成功时为"ok"，失败时为错误描述
    data: Optional[dict] = None  # 成功时携带数据，失败时为null
```

示例：
```json
// POST /v1/tasks/video 成功
{"code": 0, "message": "ok", "data": {"session_id": "abc123", "status": "pending"}}

// GET /v1/tasks/{id} 成功
{"code": 0, "message": "ok", "data": {"session_id": "...", "status": "video_generating", ...}}

// 失败
{"code": 422, "message": "prompt 不能为空", "data": null}
```

### 5.2 Java 消费方

Java 侧 `CommonResult<T>` 直接反序列化上述结构：
```java
// TaskServiceImpl.java
CommonResult<Map<String, Object>> resp = webClientBuilder.build()
    .post().uri(agentBase + "/v1/tasks/video")
    .bodyValue(body).retrieve()
    .bodyToMono(CommonResult.class).block();

// 取值：resp.getData().get("session_id")
String sessionId = (String) resp.getData().get("session_id");
```

**关键约定**：
- Java 只判断 `resp.getCode() == 0` 为成功，HTTP 状态码仅作传输层用
- FastAPI 失败统一返回 `code != 0`，Java 不需判断 HTTP 4xx/5xx（异常由 FastAPI 拦截器转 CommonResult）
- `/internal/notify` 回调也使用相同包裹格式：`{"code": 0, "message": "ok", "data": {"task_id": "..."}}`

### 5.3 价格信息归档（面试口径来源）

来源：https://www.agnes-ai.com/zh-Hans/docs/pricing

| 模型 | 现价 | 刊例价 | 备注 |
|------|------|--------|------|
| agnes-2.5-flash | $0 | $0.03/$0.15 | 阶段性免费 |
| agnes-image-2.5-flash | $0 | - | 阶段性免费 |
| agnes-video-2.5-flash | $0 | $0.025/秒 | 限时免费，720P only |
| agnes-video-2.5 | $0.025/秒(720P) | - | 有960P($0.04/秒)/2K($0.055/秒) |
| reference 图(>5张) | $0.005/张 | $0.003/张 | 仅 video-2.5 支持参考视频 |

面试口径：「免费是阶段性优惠，方案按刊例价做成本预估，flash 用于预览、2.5 用于成片是架构设计决策，不是成本驱动。」