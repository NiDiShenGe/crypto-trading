const $ = (id) => document.getElementById(id);
const num = (value, digits=4) => Number(value || 0).toLocaleString("zh-CN", {maximumFractionDigits: digits});
const pct = (value) => `${(Number(value || 0) * 100).toFixed(2)}%`;
const when = (value) => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "—";
const cls = (value) => Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";

async function refresh() {
  try {
    const response = await fetch("/api/dashboard", {cache:"no-store"});
    const data = await response.json();
    $("mode").textContent = data.mode;
    $("updated").textContent = `更新 ${when(data.generated_at)}`;
    $("cash").textContent = num(data.account.cash);
    $("pnl").textContent = `${data.account.realized_pnl >= 0 ? "+" : ""}${num(data.account.realized_pnl)}`;
    $("pnl").className = cls(data.account.realized_pnl);
    $("return").textContent = `收益率 ${pct(data.account.return_pct)}`;
    $("position-count").textContent = data.account.open_positions;
    $("position-limit").textContent = `账户上限 ${data.account.maximum_positions}`;
    $("win-rate").textContent = pct(data.performance.win_rate);
    $("win-loss").textContent = `${data.performance.wins} 胜 / ${data.performance.losses} 负`;
    $("loss-streak").textContent = `连续亏损 ${data.account.consecutive_losses}`;

    const scan = data.latest_scan?.payload;
    $("markets").textContent = scan?.total_markets ?? "—";
    $("eligible").textContent = scan?.eligible_markets ?? "—";
    $("scanned").textContent = scan?.scanned_candidates ?? "—";
    $("signal-count").textContent = scan?.signals ?? "—";
    $("scan-time").textContent = data.latest_scan ? `扫描于 ${when(data.latest_scan.occurred_at)}` : "尚无扫描记录";

    $("positions").innerHTML = data.positions.length ? data.positions.map(p => `
      <div class="position-card">
        <div><strong>${p.symbol}</strong><span class="side ${p.side}">${p.side}</span></div>
        <div><small>数量</small><strong>${num(p.quantity, 8)}</strong></div>
        <div><small>入场</small><strong>${num(p.entry_price, 8)}</strong></div>
        <div><small>止损</small><strong>${num(p.stop_price, 8)}</strong></div>
        <div><small>全仓杠杆</small><strong>${p.leverage}×</strong></div>
      </div>`).join("") : `<div class="empty">暂无持仓，系统正在等待严格信号。</div>`;

    $("fills").innerHTML = data.fills.length ? data.fills.map(item => {
      const p = item.payload;
      return `<tr><td>${when(item.occurred_at)}</td><td>${item.symbol || "—"}</td>
        <td class="side ${p.side}">${p.side || "—"}</td><td>${p.reason || "—"}</td>
        <td>${num(p.price, 8)}</td><td>${num(p.quantity, 8)}</td>
        <td class="${cls(p.realized_pnl)}">${num(p.realized_pnl)}</td></tr>`;
    }).join("") : `<tr><td colspan="7" class="empty-cell">暂无成交</td></tr>`;

    $("signals").innerHTML = data.signals.length ? data.signals.slice(0,8).map(item => `
      <div class="feed-item"><div><strong>${item.symbol}</strong><small>${item.payload.reason}</small></div>
      <div><span class="side ${item.payload.side}">${item.payload.side}</span><small>${when(item.occurred_at)}</small></div></div>
    `).join("") : `<p class="empty">暂无信号</p>`;

    $("system").innerHTML = `
      <div class="system-row"><span>执行模式</span><strong>PAPER · 全仓模拟</strong></div>
      <div class="system-row"><span>权益高水位</span><strong>${num(data.account.high_watermark)} USDT</strong></div>
      <div class="system-row"><span>最近错误</span><strong class="${data.errors.length ? "negative" : "positive"}">${data.errors.length}</strong></div>
      <div class="system-row"><span>数据刷新</span><strong>每 10 秒</strong></div>`;
  } catch (error) {
    $("updated").textContent = "连接中断，正在重试";
  }
}
refresh();
setInterval(refresh, 10000);
