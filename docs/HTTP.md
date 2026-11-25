# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

回款抵账：

- `GET /api/settlement`：账单、回款、合计与对账校验；可选 `customer_id`、`as_of`。
- `POST /api/settlement/bills`：登记或更新账单，body 为 `{"bills":[...]}` 或单张账单。
- `POST /api/settlement/receipts`：入账一笔回款；重复提交返回 `replayed=true`。
- `POST /api/settlement/receipts/{id}/reverse`：冲回回款，恢复它抵掉的欠款。
- `POST /api/settlement/receipts/{id}/claim`：把待认领款认领到账单，body 为 `{"bill_id","amount_cents"?}`。
- `POST /api/settlement/seed`：载入演示数据。
- `POST /api/settlement/reset`：清空回款与账单。
