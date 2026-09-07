# API接入规范

## 适用范围

本项目用同一套HTTP契约演示Amazon、Shopee、TikTok Shop、ERP和物流系统的商品、库存、订单同步。Amazon另保留真实SP-API只读适配器；其余来源通过`{SOURCE}_API_BASE_URL`和`{SOURCE}_API_TOKEN`切换外部HTTPS端点，未配置时使用本地Fixture。

## 请求规范

- 入口：`POST /api/integrations/sync`
- 必填字段：`source`、`resource`、`idempotency_key`
- 支持来源：`amazon`、`shopee`、`tiktok_shop`、`erp`、`logistics`
- 支持资源：`products`、`inventory`、`orders`
- Mock上游认证：`Authorization: Bearer <token>`
- 权限范围：数据同步使用`data:read`，通知使用`notify:write`
- 外部规范化接口：`POST {SOURCE}_API_BASE_URL/{products|inventory|orders}`，携带`Authorization`和`X-Idempotency-Key`

示例：

```json
{
  "source": "shopee",
  "resource": "inventory",
  "idempotency_key": "inventory-20260907-001",
  "failures_before_success": 2
}
```

`failures_before_success`仅用于本地故障演练。设置为2时，上游前两次返回429，客户端读取`Retry-After`并在第三次成功。

## 数据校验

- 商品：SKU、标题、五点描述和类目。
- 库存：SKU、可售数量、补货点；数量不得小于0。
- 订单：订单号、SKU、状态和订单天数；天数不得小于0。

响应先通过Pydantic模型校验，再进入业务分析。字段缺失、类型错误和负库存会中止同步并返回502，避免脏数据进入异常任务。

## 平台规则与RAG边界

- 标题/卖点完整度、低库存和订单超时由确定性规则引擎判定，每个任务记录平台、阈值与规则版本。
- RAG只根据平台和命中证据检索运营SOP，返回引用、解释和处理步骤；不直接决定是否违规。
- 当前阈值是用于可复现演示的运营策略，生产上线前必须根据各平台当期官方规则校准。

## 异常处理

- 429和5xx最多请求3次。
- 4xx参数或权限错误不重试。
- 每次同步携带幂等键，调用方对同一批次复用同一键。
- 生产接入时由平台OAuth或IAM替换本地Bearer令牌，并将密钥放入环境变量或密钥服务。

## 飞书通知

影刀成功回传结果后，后端自动生成飞书自定义机器人文本消息。配置`FEISHU_WEBHOOK_URL`时发送真实Webhook；未配置时发送本地Fixture。`POST /api/tasks/{task_id}/notify`用于失败后手动补发，成功步骤会阻止重复通知。

## 边界

Mock端点只允许回环地址，防止演示配置误发到公网。真实平台接入需要分别补充卖家授权、平台限流规则、签名方式和字段映射测试。
