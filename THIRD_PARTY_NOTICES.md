# 第三方开源组件

本项目保留自己的业务建模、任务编排、RPA、审计与测试实现，并使用以下开源项目作为明确边界内的依赖或规范来源：

- [saleweaver/python-amazon-sp-api](https://github.com/saleweaver/python-amazon-sp-api)，MIT：运行时 SP-API Python SDK，用于只读库存与订单状态接口。
- [amzn/selling-partner-api-models](https://github.com/amzn/selling-partner-api-models)，Apache-2.0：Amazon 官方 OpenAPI 模型，作为响应字段和契约测试的规范来源。
- [amzn/selling-partner-api-samples](https://github.com/amzn/selling-partner-api-samples)，MIT-0：Amazon 官方示例，仅作为集成方式参考。
- [qdrant/qdrant-client](https://github.com/qdrant/qdrant-client)，Apache-2.0：本地持久化向量索引与检索客户端。
- [qdrant/fastembed](https://github.com/qdrant/fastembed)，Apache-2.0：通过ONNX Runtime运行`BAAI/bge-small-zh-v1.5`文本向量模型。
- [PrithivirajDamodaran/FlashRank](https://github.com/PrithivirajDamodaran/FlashRank)，Apache-2.0：运行多语言Cross-Encoder重排模型。

本项目没有复制上述仓库的完整应用代码，也不宣称与 Amazon 官方有关联。
