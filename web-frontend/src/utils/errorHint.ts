/**
 * 给失败原因补一句「下一步该怎么办」（P3）。
 *
 * ## 为什么需要
 *
 * 后端这几轮把「失败原因可诊断」修好了（gateway 的 `_describe_transport_error`、
 * `errors.py` 的映射表），但前端一直是**把 `errorMessage` 原样抛给用户**：
 * 看到「连接生成平台失败（网络或代理不通）」，用户知道**发生了什么**，
 * 却不知道该**做什么** —— 于是要么干等，要么重启服务（有时还重启错的那个）。
 *
 * ## 设计取舍
 *
 * - **纯映射表（正则 → 一句话）**，命中才渲染；
 * - **不改写后端文案**：那句话是权威诊断（后端能看到异常类型、平台 code、排队时长），
 *   这里只在其后追加行动建议，两件事不混在一起；
 * - **不命中就什么都不加** —— 宁可不提示，也不给一句「请稍后重试」这种等于没说的废话。
 *
 * 与后端的分工：后端负责「是什么 + 为什么」（可诊断），前端负责「所以呢」。
 */
export function errorHint(errorMessage?: string | null): string | null {
  if (!errorMessage) return null;
  const m = errorMessage;

  // 顺序有讲究：先说「能自己动手修的」，再说「只能等的」
  if (/网络或代理|网络不可达|域名解析|连接生成平台被重置/.test(m)) {
    return '检查本机网络（或 Clash 代理）是否正常，再点「重新生成」';
  }
  if (/队列繁忙|请求过于频繁|平台排队|限流/.test(m)) {
    return '平台在排队/限流（免费额度只有 RPM 限制），等 1~2 分钟再点「重新生成」';
  }
  if (/超时|timed out/.test(m)) {
    return '平台可能正在排队，不是故障；稍等再点「重新生成」';
  }
  if (/余额不足|insufficient/.test(m)) {
    return '需要给生成平台账户充值后再重试';
  }
  if (/密钥|鉴权|未授权|unauthorized/.test(m)) {
    return '检查 agent-service/.env 里的 API Key 是否有效';
  }
  if (/未通过质检/.test(m)) {
    return '点「按段重生」只重做有问题的镜，不用整条重跑';
  }
  if (/拼接失败|合成失败/.test(m)) {
    return '分段产物仍可单独下载；也可以在画布页重新拼接';
  }
  if (/Agent 服务/.test(m)) {
    return '确认 agent 服务（端口 8000）在运行';
  }
  if (/已中止|被取消/.test(m)) {
    return '点「重新生成」再提交一次';
  }
  return null;
}
