# 项目状态

更新时间：2026-09-08

## 已实现

- 固定生成100个商品、800条订单、3000条库存快照及24个带标准答案的异常。
- Amazon、Shopee、TikTok Shop、ERP和物流统一HTTP同步契约，覆盖商品、库存和订单。
- Bearer令牌、`data:read`与`notify:write`权限范围、幂等键、429和5xx最多3次重试、Pydantic响应校验。
- AI建议与本地SOP引用，人工选择Listing异常并审批后才进入RPA。
- Playwright填写标题和五点描述，保存草稿后刷新回读，归档截图、Trace、JSON和Markdown报告。
- Amazon、Shopee和TikTok Shop规则引擎负责异常判定，RAG只检索SOP和解释命中证据。
- 影刀结果回传后自动调用飞书Webhook或本地Fixture，任务看板每5秒刷新成功率、失败数、恢复数和平均处理时长。
- Amazon SP-API只读适配器及无凭证零网络请求测试。

## 当前验证

- `pytest -q`：32 passed，2个第三方依赖弃用警告。
- HTTP同步：Shopee库存前2次返回429，第3次成功，校验100条记录。
- RPA任务`2782ac2a-e7d5-4696-9165-6e9b75fdcd81`：首次受控失败，第2次Chromium执行成功，重试次数1。
- RPA证据：`artifacts/2782ac2a-e7d5-4696-9165-6e9b75fdcd81/seller-central-draft.png`和`.zip` Trace。
- 飞书兼容通知：返回`mock delivered`并写入任务步骤。

## 未验证边界

- 没有Amazon卖家授权，未调用SP-API生产环境。
- Shopee、TikTok Shop、ERP、物流和飞书未使用生产凭证，已完成外部HTTPS端点配置、本地Fixture回退和HTTP契约测试。
- 影刀节点迁移文档已完成，尚未在影刀客户端实机创建流程。
- 没有人工操作基线，不写提效比例。
