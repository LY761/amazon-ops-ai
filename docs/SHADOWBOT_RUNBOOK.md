# 影刀RPA迁移步骤

## 目标流程

人工在看板确认Listing异常后，影刀读取任务字段，打开Seller Central仿真页，填写SKU、标题、五点描述和备注，保存草稿，刷新页面回读字段，再把成功或失败结果写回任务系统。

## 输入字段

| 字段 | 来源 | 用途 |
| --- | --- | --- |
| task_id | 任务详情接口 | 日志和回传主键 |
| sku | selected_anomaly | 定位商品 |
| suggested_title | listing_advice | 填写标题 |
| suggested_bullets | listing_advice | 换行拼接后填写五点描述 |
| issues | listing_advice | 填写修改备注 |

## 影刀节点顺序

1. 看板审批Listing异常时，后端把任务入队并通过影刀CLI异步启动已发布应用。
2. 影刀调用`POST /api/rpa/claim`领取任务；无任务时直接结束。
3. 打开返回的`job.url`，等待SKU输入框出现。
4. 按字段ID填写`sku`、`title`、`bullets`和`note`。
5. 点击`save-draft`，等待`#saved`可见。
6. 刷新页面并回读三个输入框，与任务字段逐项比较。
7. 调用`POST /api/rpa/tasks/{task_id}/result`回传成功或失败及回读字段。
8. 成功后调用`POST /api/tasks/{task_id}/notify`发送飞书兼容通知。

## 异常规则

- 页面元素超时：截图并重试1次。
- 字段回读不一致：停止任务，不继续发布。
- 接口429或5xx：遵循`Retry-After`，总计最多3次。
- 未审批、库存异常或订单异常：不进入页面填写流程。

## 当前验证状态

影刀6.3.13已完成审批事件触发、任务领取、页面填写、保存、刷新回读和结果回传实测；Playwright用于相同页面闭环的自动化回归测试。
