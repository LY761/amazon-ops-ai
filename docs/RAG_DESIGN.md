# 平台规则RAG设计与验证

## 业务边界

确定性规则引擎根据订单、库存和Listing字段判定异常。RAG只负责找到规则依据、解释原因并生成处理建议，不能新增异常、直接发布、调价、补货、退款或发送买家消息。所有后台写入动作进入人工审批。

知识文档均标注为内部演示规则，不冒充Amazon、Shopee或TikTok Shop官方政策。当前4份文档分别覆盖三平台Listing和跨平台库存、订单、售后、RPA审批边界，版本统一为`demo-2026-09`。

## 文档切分

1. 读取`knowledge/*.md`，提取一级标题作为文档标题。
2. 提取平台、主题和版本字段，和文档ID一起写入Chunk元数据。
3. 二级标题定义业务章节，例如发布前完整度检查、违规风险与处理、库存与补货。
4. 按Markdown列表项或完整段落形成规则条款，再组合到约420字符。
5. 超出长度时保留上一条完整规则作为重叠上下文，不使用固定字符截断，避免把条件和处理动作切开。

当前4份文档生成9个Chunk。每个Chunk保存`id`、`document_id`、`title`、`platform`、`topic`、`rule_version`、`heading`和`text`。

## 混合检索与重排

```text
用户问题 + 平台过滤
  ├─ BM25稀疏召回：关键词、SKU、平台术语
  └─ BGE向量召回：BAAI/bge-small-zh-v1.5 → Qdrant
            ↓
        RRF排名融合
            ↓
  FlashRank ms-marco-MultiBERT-L-12 Cross-Encoder评分
            ↓
       Cross-Encoder与RRF组合排序
            ↓
           Top-3
```

Qdrant使用本地持久化目录`data/qdrant`。启动时计算包含Chunk全文、元数据、切分版本和Embedding模型的语料哈希；内容或模型变化时重建索引，避免读取旧向量。检索结果通过`POST /api/rag/search`公开`sparse_rank`、`dense_rank`、`rrf_score`、`rerank_score`和`final_score`，可以直接检查每一步排序。

本地持久化模式用于单进程作品集演示；评测脚本使用独立内存Qdrant，因此可以在后端运行时执行。生产多进程部署时改用独立Qdrant服务。

## 生成与幻觉控制

重排后的证据以`id`、标题和原文注入OpenAI兼容模型，要求返回结构化字段：候选摘要、引用ID、置信度、是否人工审核和推荐动作。模型的自由文本不直接展示；服务端根据通过校验的引用ID取回原始规则条款，再结合受控动作模板生成最终答复。

返回后再执行确定性校验：

- 引用ID必须属于本次Top-K，否则丢弃模型输出并回退到证据模板。
- 动作只允许`save_listing_draft`或`create_manual_review`，其他动作全部回退。
- 回答出现无需审批、自动发布、自动退款、自动调价等危险表述时回退。
- 后台写入始终强制`requires_human_review=true`。
- 即使模型返回带合法引用ID的错误自由文本，最终用户答复也只包含被选中的规则原文和受控动作说明。
- 问题未命中知识库已覆盖的Listing、库存、订单、售后或RPA治理意图，或明确属于其他领域时，拒答并创建人工审核任务；Amazon广告、秒杀、税务等未收录主题不会被强行匹配到现有规则。
- 模型接口失败时使用检索证据生成模板建议，任务不中断。

## 向量库选型

| 方案 | 适合场景 | 本项目决策 |
| --- | --- | --- |
| Qdrant | 本地嵌入运行，也能平滑切换独立服务；支持向量、Payload过滤和持久化 | 采用。Windows作品集一键运行，接口与生产服务形态接近 |
| Chroma | 单机原型和Notebook，接入简单 | 未采用。当前Qdrant同样能本地运行，并保留更清晰的服务化迁移路径 |
| Milvus | 大规模分布式向量检索 | 未采用。4份规则文档无需引入独立集群和运维成本 |

## 可复现评测

运行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_rag.py
```

2026-09-08实测：

| 指标 | 结果 |
| --- | ---: |
| 检索问题 | 30 |
| Recall@1 | 0.8667 |
| Recall@3 | 1.0 |
| MRR | 0.9333 |
| 目标引用命中率 | 1.0 |
| 无证据问题 | 12 |
| 无证据拒答率 | 1.0 |

评测按目标文档和目标章节标注正确Chunk，避免仅按文档ID统计造成虚高。负样本包含员工请假审批、网站标题SEO、餐厅订单等域外问题，以及广告竞价、秒杀活动、发票税率、直播佣金等知识库未覆盖的电商问题。该评测验证当前内部演示规则集的检索和引用链路，不代表生产店铺政策覆盖率。真实上线前需要导入企业SOP和已授权平台政策，并用运营人员标注的历史问题重新评测。

## 实现参考

- Qdrant Python Client与FastEmbed：https://qdrant.tech/documentation/interfaces/
- Qdrant本地模式：https://qdrant.tech/documentation/frameworks/langchain/
- FlashRank：https://github.com/PrithivirajDamodaran/FlashRank
