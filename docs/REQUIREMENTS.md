# AmazonOps AI 作品集项目需求文档

## 1. 文档信息

- 版本：2.0
- 状态：开发与验收基线
- 项目定位：用于应聘电商 AI 自动化、Python RPA 或 Agent 应用开发岗位的可运行作品集
- 运行环境：Windows 10/11，Python 3.11+

## 2. 项目目标

AmazonOps AI 是一个 Amazon 运营智能巡检与 RPA 草稿助手。系统读取商品、FBA 库存和订单状态，识别 Listing、库存与履约风险，生成结构化建议，通过 Playwright 操作 Seller Central 仿真页面，并保存审计记录和执行证据。

项目必须在没有 Amazon 卖家账号和大模型密钥时完整演示，同时提供真实 SP-API 只读适配器，使面试者可以明确解释生产接入边界。

## 3. 主要用户故事

1. 作为运营人员，我可以一键运行店铺巡检并看到 Listing、低库存和超时订单风险。
2. 作为审核人员，我可以查看建议、RPA 保存的草稿截图和 JSON/Markdown 报告。
3. 作为工程师，我可以演示任务幂等、失败记录、重试和步骤复用。
4. 作为未来卖家，我可以配置 LWA 凭证，通过成熟开源 SDK 读取 Amazon 数据预览。

## 4. 功能需求

### FR-01 可复现离线演示

- 默认使用 `fixture_demo` 数据源和确定性分析器。
- 不读取外部凭证时仍能执行完整业务闭环。
- 提供健康检查、作品集首页和 OpenAPI 文档。

### FR-02 电商业务分析

- 固定生成100个商品、800条订单、30天共3000条库存快照，以及24个标注异常。
- 检查标题与五点描述完整度、FBA低库存和超过3天未发货订单，生成Listing、库存、订单各8条异常队列项。
- 输出分数、问题、标题建议、五点描述建议和风险摘要。

### FR-03 AI Provider

- 默认规则分析可重复、零成本。
- 可选 OpenAI-compatible Responses 或 Chat Completions。
- 每个输入 SKU 必须恰有一条通过 Pydantic 校验的结构化建议。
- 缺少密钥时不得创建模型客户端或发外部请求。

### FR-04 RPA 执行与证据

- Playwright 仅控制 HTTP loopback 上的 Seller Central 仿真页。
- 自动填写 SKU、标题、五点描述和运营备注并保存草稿。
- 每次成功执行必须回读SKU、标题和五点字段，保存截图与Playwright Trace；不得访问真实Seller Central或发布商品。

### FR-05 任务可靠性

- 状态包含 `pending`、`running`、`succeeded`、`failed`。
- 步骤包含数据校验、运营分析、浏览器填表和报告生成。
- 相同幂等键返回同一任务。
- 支持受控首次失败；重试不得重复已成功的分析或 RPA 步骤。
- SQLite保存任务、异常队列、选中异常、步骤、尝试历史、商品与分析结果。

### FR-06 Amazon SP-API 开源适配器

- 固定依赖 `python-amazon-sp-api==2.1.22`。
- SDK 客户端惰性创建；查看状态与运行离线 Demo 不得访问 Amazon。
- 检查 `SP_API_REFRESH_TOKEN`、`LWA_APP_ID`、`LWA_CLIENT_SECRET`，仅返回缺失变量名。
- 授权后支持只读 FBA 库存汇总与订单状态预览。
- 不请求或保存买家姓名、地址、电话等 PII。
- 使用 Amazon 官方模型字段编写无网络契约测试。

### FR-07 管理页面与 API

- 首页展示项目定位、技术栈、数据风险摘要、SP-API 状态和最近任务。
- 用户可以运行正常 Demo、失败恢复 Demo，查看报告与截图。
- 提供 README 中列出的稳定 API 路径和可读 HTTP 错误。

## 5. 非功能需求

- 模块按 domain、services、adapters、infrastructure 分层。
- 默认只绑定本机；日志和响应不得输出密钥、Cookie 或 PII。
- 开源依赖必须记录来源和许可证。
- 自动化测试不得依赖 Amazon、Seller Central 或大模型网络服务。

## 6. 验收标准

1. 全新虚拟环境按 README 安装后测试全部通过。
2. `/health`、首页和 `/docs` 返回 HTTP 200。
3. 正常Demo生成24条异常队列，允许选择任意Listing异常审批，并生成截图、Trace和两种报告。
4. 失败Demo首次为failed，重试后为succeeded，复用已选择的SKU和已完成步骤。
5. 无 SP-API 凭证时状态端点可访问，预览端点返回 409 且不触网。
6. 契约测试能把官方字段形态映射为低库存与订单关注项，不包含 PII。
7. 固定合成标注集输出Precision、Recall和F1，指标与真实店铺效果分开表述。
8. PROJECT_STATUS.md分开记录已验证与未验证范围。

## 7. 不在当前范围

- Amazon 生产店铺联调和 Restricted Data Token。
- 真实发布、调价、发货、退款或客服回复。
- 多租户权限、分布式队列和云部署。
- 宣称使用真实销售数据取得业务增长。
