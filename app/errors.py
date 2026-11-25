from flask import jsonify


MESSAGES = {
    "EMPTY_FILE": "文件为空",
    "BLANK_FILE": "文件只有空白",
    "HEADER_ONLY": "文件只有表头",
    "HEADER_MISMATCH": "表头与本项目字段不一致",
    "UNCLOSED_QUOTE": "引号未闭合，文件已拒收",
    "TOO_MANY_ROWS": "数据行超过上限",
    "FILE_TOO_LARGE": "文件超过大小上限",
    "KEY_MISSING": "主键为空",
    "CONFLICT_DUPLICATE": "同一键存在冲突内容",
    "SAMPLE_UNKNOWN": "样例不存在",
    "STAGE_UNKNOWN": "阶段不存在",
    "RUN_NOT_FOUND": "批次不存在",
    "STALE_REVIEW": "批次已被更新，请刷新后复核",
    "GATE_BLOCKED": "存在阻断项，不能签发",
    "NOT_APPROVED": "批次尚未签发",
    "BAD_REQUEST": "请求内容不完整",
    "BILL_INVALID": "账单内容无法识别",
    "BILL_ID_MISSING": "账单编号为空",
    "BAD_BILL_AMOUNT": "账单金额无效",
    "BAD_DUE_DATE": "到期日无效",
    "BAD_BOOLEAN": "标记值无效",
    "RECEIPT_INVALID": "回款内容无法识别",
    "BAD_RECEIPT_AMOUNT": "回款金额无效",
    "BAD_DATETIME": "到账时间无效",
    "BAD_DESIGNATION": "指定用途格式无效",
    "BAD_DESIGNATION_AMOUNT": "指定金额无效",
    "DESIGNATION_EXCEEDS_RECEIPT": "指定金额超过回款金额",
    "RECEIPT_CONFLICT": "回款编号相同但内容不同",
    "RECEIPT_AMBIGUOUS": "同内容回款不止一笔，无法判断重跑",
    "RECEIPT_NOT_FOUND": "回款不存在",
    "RECEIPT_ALREADY_REVERSED": "回款已经冲回",
    "RECEIPT_REVERSED": "回款已经冲回",
    "BILL_NOT_FOUND": "账单不存在",
    "BILL_CUSTOMER_LOCKED": "账单已有回款，不能更换客户",
    "BILL_AMOUNT_TOO_SMALL": "账单金额小于已抵金额",
    "BILL_HAS_PAYMENTS": "账单已有回款抵扣，先冲回再改状态",
    "BILL_UNSIGNED": "未签账单不能销账",
    "BILL_DISPUTED": "争议账单不能销账",
    "BILL_SETTLED": "账单已经结清",
    "CUSTOMER_MISMATCH": "回款与账单不属于同一客户",
    "NOTHING_PENDING": "该回款没有待认领金额",
    "BAD_CLAIM_AMOUNT": "认领金额无效",
    "BAD_TEXT": "内容为空",
    "BAD_DATE": "日期无效",
    "BAD_AMOUNT": "金额无效",
}


class DomainIssue(Exception):
    def __init__(self, code: str, status: int = 400, detail: str | None = None):
        self.code = code
        self.status = status
        self.detail = detail
        super().__init__(code)


def error_response(exc: DomainIssue):
    return jsonify(error=exc.detail or MESSAGES.get(exc.code, "请求无法处理"), code=exc.code), exc.status
