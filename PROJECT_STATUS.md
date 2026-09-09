# 项目状态

更新时间：2026-09-08

## 已实现

- 固定生成100个商品、800条订单、3000条库存快照及24个带标准答案的异常。
- Amazon、Shopee、TikTok Shop、ERP和物流统一HTTP同步契约，覆盖商品、库存和订单。
- Bearer令牌、`data:read`与`notify:write`权限范围、幂等键、429和5xx最多3次重试、Pydantic响应校验。
- Qdrant本地持久化规则知识库：Markdown标题与完整条款切分、BGE中文Embedding、BM25与向量双路召回、RRF融合、FlashRank重排、带引用生成与域外拒答。
- Playwright填写标题和五点描述，保存草稿后刷新回读，归档截图、Trace、JSON和Markdown报告。
- Amazon、Shopee和TikTok Shop规则引擎负责异常判定，RAG只检索SOP、解释命中证据和生成处理建议。
- 影刀结果回传后自动调用飞书Webhook或本地Fixture，任务看板每5秒刷新成功率、失败数、恢复数和平均处理时长。
- Amazon SP-API只读适配器及无凭证零网络请求测试。

## 当前验证

- `pytest -q`：41 passed，2个第三方依赖弃用警告。
- 真实本地向量链路：4份规则文档、9个Chunk，`BAAI/bge-small-zh-v1.5`写入Qdrant，FlashRank多语言Cross-Encoder完成重排。
- 30条Chunk级RAG评测：Recall@1 0.8667、Recall@3 1.0、MRR 0.9333、目标引用命中率1.0；12条无证据问题覆盖域外意图及未收录的广告竞价、秒杀、税务场景，拒答率1.0。
- OpenAI兼容Responses实调返回HTTP 200；端到端任务`4cfacb72-1210-43af-9b18-7dcef54f8dec`生成带有效Chunk引用的Listing处理建议，来源为`local_qdrant_rag_openai`，动作被限制为转人工或保存草稿。
- HTTP同步：Shopee库存前2次返回429，第3次成功，校验100条记录。
- RPA任务`2782ac2a-e7d5-4696-9165-6e9b75fdcd81`：首次受控失败，第2次Chromium执行成功，重试次数1。
- RPA证据：`artifacts/2782ac2a-e7d5-4696-9165-6e9b75fdcd81/seller-central-draft.png`和`.zip` Trace。
- 飞书兼容通知：返回`mock delivered`并写入任务步骤。

## 未验证边界

- 没有Amazon卖家授权，未调用SP-API生产环境。
- Shopee、TikTok Shop、ERP、物流和飞书未使用生产凭证，已完成外部HTTPS端点配置、本地Fixture回退和HTTP契约测试。
- 影刀客户端流程已部署并能领取后端任务；生产电商后台仍未连接。
- 没有人工操作基线，不写提效比例。
