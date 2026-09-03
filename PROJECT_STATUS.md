# 项目状态

## 已实现

- FastAPI 服务、作品集首页、OpenAPI 与健康检查。
- Listing、FBA 库存和订单时效巡检，规则分析与可选 OpenAI-compatible 结构化分析。
- Playwright Seller Central 仿真 RPA、SQLite 审计、幂等任务、失败重试、截图和 JSON/Markdown 报告。
- `python-amazon-sp-api==2.1.22` 运行时依赖与只读适配器。
- SP-API SDK/凭证就绪状态接口，以及 FBA 库存和非 PII 订单状态预览接口。
- Amazon 官方字段形态的响应映射测试；缺凭证时预览返回 409 且不会创建网络客户端。
- 第三方许可证声明、三分钟面试演示指南和简历描述草稿。

## 验证记录

- `python-amazon-sp-api==2.1.22` 已安装到项目 `.venv`，Python 3.13 环境安装成功。
- `.\.venv\Scripts\python.exe -m pytest -q`：19 passed，2 个第三方依赖弃用警告。
- 测试覆盖原有业务/RPA 工作流，以及 SP-API 凭证保护、官方字段映射、非 PII 输出和集成状态端点。

## 未验证范围

- 没有卖家授权，尚未对 Amazon 生产 SP-API 发出成功请求。
- 未访问真实 Seller Central，也没有执行发布、调价、发货、退款或读取买家 PII。
- OpenAI 适配器已通过无网络测试；中转服务认证和模型列表可用，但模型生成端此前返回 `502 upstream_error`，成功生成仍未验证。

## 最新端到端验证

- 使用 `AMAZONOPS_ANALYZER=deterministic` 和一致的 `AMAZONOPS_BASE_URL` 启动本地服务。
- 正常任务 `f59aa07f-8042-4e17-951a-3fb1618462b9` 最终为 `succeeded`。
- 受控失败任务 `7cb28588-7049-4cbf-8fe7-dc92c9e26e5b` 重试后为 `succeeded`，尝试次数为 2。
- `/health`、首页和 `/simulator` 返回 HTTP 200；真实 Chromium 完成表单填写并生成截图与报告。