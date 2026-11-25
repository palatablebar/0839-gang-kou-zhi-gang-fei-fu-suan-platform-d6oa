# 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `container_id` | text | 主键 |
| `gate_in` | date | 业务字段 |
| `gate_out` | date | 业务字段 |
| `free_days` | int | 业务字段 |
| `invoice_cents` | int | 业务字段 |

空字符串、`-`、`NA`、`null` 和 `未提供` 按空值处理。金额字段在整理层转为整数分或整数最小单位；日期按 ISO 日期解析；带时间的字段按本地时间解析。标识字段保持为文本，前导零不会被去除。

## 回款抵账字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `bill_id` | text | 账单号 |
| `customer_id` | text | 客户 |
| `amount_cents` | int | 账单金额（分），也接受 `amount`（元） |
| `due_date` | date | 到期日，未指定用途的款项按此排序 |
| `signed` | bool | 是否已签；未签不能销账 |
| `disputed` | bool | 是否有争议；有争议不能销账 |
| `receipt_id` | text | 回款编号，可省略 |
| `bank_ref` | text | 银行流水号，用于识别同一笔回款 |
| `received_at` | datetime | 到账时间，缺省取入账时刻 |
| `designated_bill_id` / `designations` | text / list | 付款人指定的账单；`designations` 为 `[{bill_id, amount_cents}]` |
| `designated_cents` | int | 单张指定账单的金额（分），也接受 `designated`（元）；留空表示全额指定 |

回款记录额外返回 `status`（`ACTIVE`/`REVERSED`）、`allocations`（已抵明细）、`pending`（待认领明细）、`allocated_cents`、`pending_cents`。
