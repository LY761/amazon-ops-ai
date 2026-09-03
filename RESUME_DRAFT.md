# 简历描述草稿

**AmazonOps AI｜Amazon 运营智能巡检与 RPA 草稿助手**  
Python / FastAPI / Playwright / SQLite / Pydantic / Amazon SP-API

- 设计并实现 Amazon 运营巡检闭环，对 Listing 完整度、FBA 库存阈值和订单时效进行结构化分析，输出标题、五点描述及风险建议。
- 使用 Playwright 自动填写 Seller Central 仿真页面，通过显式等待、截图取证和 JSON/Markdown 报告保留 RPA 执行证据。
- 实现带幂等键的任务状态机、步骤级持久化和失败恢复，使重试复用已完成的数据校验、AI 分析或浏览器步骤。
- 集成 MIT 开源 `python-amazon-sp-api` 2.1.22，完成 LWA 凭证就绪检查、FBA 库存与非 PII 订单状态的只读适配，并依据 Amazon 官方模型编写无网络契约测试。
- 构建可替换的确定性/OpenAI-compatible 分析层，使用 Pydantic 强校验模型结构化输出，保证无卖家账号或无模型密钥时仍可复现完整 Demo。

**面试表述边界：** 当前验证环境为官方字段结构样例、本地 Seller Central 仿真页和无网络测试；真实卖家 SP-API 生产联调待授权后完成。
