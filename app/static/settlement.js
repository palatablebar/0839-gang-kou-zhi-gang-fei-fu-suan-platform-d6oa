(function () {
  var $ = function (id) { return document.getElementById(id); };
  var state = { snapshot: null, claim: null };
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
    });
  }
  function money(cents) { return (Number(cents || 0) / 100).toFixed(2); }
  function setNotice(text) { var el = $('notice'); if (el) el.textContent = text || ''; }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function billOptions() {
    if (!state.snapshot) return [];
    return state.snapshot.bills.filter(function (bill) {
      return bill.outstanding_cents > 0 && bill.signed && !bill.disputed;
    });
  }
  function renderKpis(snapshot) {
    $('s-received').textContent = money(snapshot.totals.received_active_cents);
    $('s-allocated').textContent = money(snapshot.totals.allocated_cents);
    $('s-pending').textContent = money(snapshot.totals.pending_cents);
    $('s-overdue').textContent = money(snapshot.totals.overdue_cents);
    $('settlement-asof').textContent = '截至 ' + snapshot.as_of;
    $('settlement-checks').innerHTML = snapshot.checks.map(function (check) {
      return '<div class="ledger-row"><span>' + esc(check.label) + '</span><strong class="' +
        (check.ok ? 'check-ok' : 'check-bad') + '">' + (check.ok ? '通过' : '异常') + '</strong></div>';
    }).join('');
    $('settlement-identity').textContent = '已抵 ' + money(snapshot.totals.allocated_cents) +
      ' + 待认领 ' + money(snapshot.totals.pending_cents) +
      ' = 未冲回实收 ' + money(snapshot.totals.received_active_cents) +
      '（已冲回 ' + money(snapshot.totals.received_reversed_cents) + ' 不计入）';
  }
  function renderBills(snapshot) {
    $('bill-count').textContent = snapshot.bills.length + ' 张';
    $('bill-head').innerHTML = '<tr><th>账单</th><th>客户</th><th>到期</th><th>金额</th><th>已抵</th><th>未结</th><th>状态</th></tr>';
    $('bill-body').innerHTML = snapshot.bills.length ? snapshot.bills.map(function (bill) {
      var flags = [bill.signed ? '已签' : '未签'];
      if (bill.disputed) flags.push('争议');
      return '<tr class="' + (bill.overdue ? 'overdue' : '') + '"><td>' + esc(bill.bill_id) +
        (bill.label ? '<br><small>' + esc(bill.label) + '</small>' : '') + '</td><td>' + esc(bill.customer_id) +
        '</td><td>' + esc(bill.due_date) + '</td><td class="num">' + money(bill.amount_cents) +
        '</td><td class="num">' + money(bill.paid_cents) + '</td><td class="num">' + money(bill.outstanding_cents) +
        '</td><td><span class="verdict" data-v="' + esc(bill.status) + '">' + esc(bill.status) + '</span>' +
        '<br><small>' + esc(flags.join(' · ')) + (bill.overdue ? ' · 逾期' : '') + '</small></td></tr>';
    }).join('') : '<tr><td class="empty" colspan="7">暂无账单</td></tr>';
  }
  function allocationText(receipt) {
    if (receipt.status !== 'ACTIVE') {
      return '<small>已冲回；原抵 ' + esc(receipt.allocations.map(function (a) { return a.bill_id; }).join('、') || '无') + ' 已恢复欠款</small>';
    }
    var parts = receipt.allocations.map(function (a) {
      return esc(a.bill_id) + ' ' + money(a.amount_cents) + (a.source === 'CLAIM' ? '(认领)' : '');
    });
    var held = receipt.pending.map(function (p) {
      return '待认领 ' + money(p.amount_cents) + '（' + esc(p.bill_id || '-') + '）';
    });
    return (parts.length ? parts.join('<br>') : '无抵扣') + (held.length ? '<br><small>' + held.join('<br>') + '</small>' : '');
  }
  function renderReceipts(snapshot) {
    $('receipt-count').textContent = snapshot.receipts.length + ' 笔';
    $('receipt-head').innerHTML = '<tr><th>回款</th><th>客户</th><th>到账</th><th>金额</th><th>已抵</th><th>待认领</th><th>状态</th><th>处理</th></tr>';
    $('receipt-body').innerHTML = snapshot.receipts.length ? snapshot.receipts.map(function (receipt) {
      var actions = [];
      if (receipt.status === 'ACTIVE') {
        if (receipt.pending_cents > 0) {
          actions.push('<button type="button" data-claim="' + esc(receipt.receipt_id) + '">认领</button>');
        }
        actions.push('<button type="button" data-reverse="' + esc(receipt.receipt_id) + '">冲回</button>');
      }
      return '<tr><td>' + esc(receipt.receipt_id) + (receipt.bank_ref ? '<br><small>' + esc(receipt.bank_ref) + '</small>' : '') +
        '</td><td>' + esc(receipt.customer_id) + '</td><td>' + esc(receipt.received_at.replace('T', ' ')) +
        '</td><td class="num">' + money(receipt.amount_cents) + '</td><td class="num">' + money(receipt.allocated_cents) +
        '</td><td class="num">' + money(receipt.pending_cents) + '</td><td>' + esc(receipt.status) +
        '</td><td class="row-actions">' + actions.join('') + '</td></tr>' +
        '<tr class="detail"><td colspan="8">' + allocationText(receipt) + '</td></tr>';
    }).join('') : '<tr><td class="empty" colspan="8">暂无回款</td></tr>';
  }
  function fillSelects(snapshot) {
    var customers = Array.from(new Set(snapshot.bills.map(function (bill) { return bill.customer_id; })));
    $('customer-list').innerHTML = customers.map(function (id) {
      return '<option value="' + esc(id) + '"></option>';
    }).join('');
    var designated = $('designated-bill');
    var current = designated.value;
    var customer = ($('receipt-customer') && $('receipt-customer').value || '').trim();
    var options = snapshot.bills.filter(function (bill) {
      return !customer || bill.customer_id === customer;
    });
    designated.innerHTML = '<option value="">不指定</option>' + options.map(function (bill) {
      return '<option value="' + esc(bill.bill_id) + '">' + esc(bill.bill_id) + ' · ' + esc(bill.customer_id) +
        (bill.disputed ? ' · 争议' : (bill.signed ? '' : ' · 未签')) + '</option>';
    }).join('');
    designated.value = current;
    var claimBill = $('claim-bill');
    var claimReceipt = state.claim && snapshot.receipts.find(function (item) {
      return item.receipt_id === state.claim;
    });
    claimBill.innerHTML = billOptions().filter(function (bill) {
      return !claimReceipt || bill.customer_id === claimReceipt.customer_id;
    }).map(function (bill) {
      return '<option value="' + esc(bill.bill_id) + '">' + esc(bill.bill_id) + ' · ' + esc(bill.customer_id) +
        ' · 未结 ' + money(bill.outstanding_cents) + '</option>';
    }).join('');
  }
  function render(snapshot) {
    state.snapshot = snapshot;
    renderKpis(snapshot);
    renderBills(snapshot);
    renderReceipts(snapshot);
    fillSelects(snapshot);
    if (state.claim) {
      var receipt = snapshot.receipts.find(function (item) { return item.receipt_id === state.claim; });
      if (!receipt || receipt.pending_cents <= 0 || receipt.status !== 'ACTIVE') clearClaim();
      else $('claim-hint').textContent = '可认领 ' + money(receipt.pending_cents);
    }
  }
  function clearClaim() {
    state.claim = null;
    $('claim-receipt').value = '';
    $('claim-submit').disabled = true;
    $('claim-clear').disabled = true;
    $('claim-hint').textContent = '选择一笔有待认领金额的回款';
  }
  async function refresh() {
    try { render(await request('/api/settlement')); }
    catch (error) { setNotice(error.message); }
  }
  async function submitReceipt(event) {
    event.preventDefault();
    var form = event.target;
    var data = Object.fromEntries(new FormData(form).entries());
    var body = {customer_id: data.customer_id.trim(), amount: data.amount.trim()};
    if (data.received_at) body.received_at = data.received_at + ':00';
    if (data.bank_ref) body.bank_ref = data.bank_ref.trim();
    if (data.designated_bill_id) {
      body.designated_bill_id = data.designated_bill_id;
      if (data.designated_yuan.trim()) {
        body.designated_cents = Math.round(Number(data.designated_yuan) * 100);
      }
    }
    $('post-receipt').disabled = true;
    try {
      var result = await request('/api/settlement/receipts', {method: 'POST', body: JSON.stringify(body)});
      setNotice(result.replayed ? '同一笔回款已入账，未重复销账' : '');
      form.reset();
      await refresh();
    } catch (error) { setNotice(error.message); }
    finally { $('post-receipt').disabled = false; }
  }
  async function submitBill(event) {
    event.preventDefault();
    var data = Object.fromEntries(new FormData(event.target).entries());
    var body = {
      bill_id: data.bill_id.trim(), customer_id: data.customer_id.trim(),
      amount: data.amount.trim(), due_date: data.due_date, label: (data.label || '').trim(),
      signed: data.signed === 'on', disputed: data.disputed === 'on'
    };
    try {
      render(await request('/api/settlement/bills', {method: 'POST', body: JSON.stringify(body)}));
      setNotice('');
    } catch (error) { setNotice(error.message); }
  }
  async function submitClaim(event) {
    event.preventDefault();
    if (!state.claim) return;
    var data = Object.fromEntries(new FormData(event.target).entries());
    if (!data.bill_id) { setNotice('没有可销账的已签无争议账单'); return; }
    var body = {bill_id: data.bill_id};
    if (data.claim_yuan.trim()) body.amount_cents = Math.round(Number(data.claim_yuan) * 100);
    try {
      var result = await request('/api/settlement/receipts/' + encodeURIComponent(state.claim) + '/claim',
        {method: 'POST', body: JSON.stringify(body)});
      render(result.snapshot);
      event.target.reset();
      clearClaim();
      setNotice('');
    } catch (error) { setNotice(error.message); }
  }
  async function reverse(receiptId) {
    if (!window.confirm('确认冲回 ' + receiptId + '？将恢复它抵掉的欠款。')) return;
    try {
      var result = await request('/api/settlement/receipts/' + encodeURIComponent(receiptId) + '/reverse',
        {method: 'POST', body: '{}'});
      render(result.snapshot);
      setNotice('');
    } catch (error) { setNotice(error.message); }
  }
  function startClaim(receiptId) {
    state.claim = receiptId;
    $('claim-receipt').value = receiptId;
    $('claim-submit').disabled = false;
    $('claim-clear').disabled = false;
    $('claim-hint').textContent = '可认领 ' + money(
      (state.snapshot.receipts.find(function (item) { return item.receipt_id === receiptId; }) || {}).pending_cents);
    if (state.snapshot) fillSelects(state.snapshot);
    $('claim-bill').focus();
  }
  document.addEventListener('DOMContentLoaded', function () {
    if (!$('settlement')) return;
    $('receipt-form').addEventListener('submit', submitReceipt);
    $('receipt-customer').addEventListener('input', function () {
      if (state.snapshot) fillSelects(state.snapshot);
    });
    $('bill-form').addEventListener('submit', submitBill);
    $('claim-form').addEventListener('submit', submitClaim);
    $('claim-clear').addEventListener('click', clearClaim);
    $('receipt-body').addEventListener('click', function (event) {
      var claim = event.target.getAttribute('data-claim');
      var rev = event.target.getAttribute('data-reverse');
      if (claim) startClaim(claim);
      else if (rev) reverse(rev);
    });
    refresh();
  });
  window.refreshSettlement = refresh;
})();
