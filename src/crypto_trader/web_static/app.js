const $ = (id) => document.getElementById(id);
const num = (value, digits=4) => Number(value || 0).toLocaleString("zh-CN", {maximumFractionDigits: digits});
const pct = (value) => `${(Number(value || 0) * 100).toFixed(2)}%`;
const when = (value) => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "—";
const clock = (value) => value ? new Date(value).toLocaleTimeString("zh-CN", {hour12:false}) : "等待报价";
const cls = (value) => Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
const exitLabel = (reason) => ({
  "paper entry": "开仓",
  "first take profit": "2R 止盈 1/3",
  "initial_stop": "初始止损",
  "breakeven_stop": "保本止损",
  "trailing_stop": "趋势跟踪退出",
  "failed_breakout": "突破失败",
  "liangyi_filter_exit": "两仪四象过滤退出",
  "no_progress_exit": "6根K线无进展",
  "stop loss": "旧版止损"
}[reason] || reason || "—");
const duration = (minutes) => {
  const value = Number(minutes || 0);
  if (!value) return "—";
  return value >= 60 ? `${(value / 60).toFixed(1)}h` : `${Math.round(value)}m`;
};

async function refresh() {
  try {
    const response = await fetch("/api/dashboard", {cache:"no-store"});
    const data = await response.json();
    $("mode").textContent = data.mode;
    $("updated").textContent = `更新 ${when(data.generated_at)}`;
    $("equity").textContent = num(data.account.current_equity);
    $("cash").textContent = `可用现金 ${num(data.account.cash)} USDT`;
    $("pnl").textContent = `${data.account.realized_pnl >= 0 ? "+" : ""}${num(data.account.realized_pnl)}`;
    $("pnl").className = cls(data.account.realized_pnl);
    $("unrealized").textContent = `${data.account.unrealized_pnl >= 0 ? "+" : ""}${num(data.account.unrealized_pnl)}`;
    $("unrealized").className = cls(data.account.unrealized_pnl);
    $("return").textContent = `收益率 ${pct(data.account.return_pct)}`;
    $("position-count").textContent = data.account.open_positions;
    $("position-limit").textContent = `账户上限 ${data.account.maximum_positions}`;
    $("loss-streak").textContent = `连续亏损 ${data.account.consecutive_losses}`;

    const scan = data.latest_scan?.payload;
    $("markets").textContent = scan?.total_markets ?? "—";
    $("eligible").textContent = scan?.eligible_markets ?? "—";
    $("scanned").textContent = scan?.scanned_candidates ?? "—";
    $("signal-count").textContent = scan?.signals ?? "—";
    $("scan-time").textContent = data.latest_scan ? `扫描于 ${when(data.latest_scan.occurred_at)}` : "尚无扫描记录";

    $("positions").innerHTML = data.positions.length ? data.positions.map(p => `
      <div class="position-card">
        <div class="position-summary">
          <div class="symbol-line">
            <strong class="symbol">${p.symbol}</strong>
            <span class="side-pill ${p.side}">${p.side === "long" ? "做多" : "做空"}</span>
            <span class="leverage-pill">全仓 ${p.leverage}×</span>
          </div>
          <div class="live-pnl">
            <small>浮动盈亏</small>
            <strong class="${cls(p.unrealized_pnl)}">${p.unrealized_pnl >= 0 ? "+" : ""}${num(p.unrealized_pnl)} <em>USDT</em></strong>
            <span class="${cls(p.unrealized_return)}">${p.unrealized_return >= 0 ? "+" : ""}${pct(p.unrealized_return)}</span>
          </div>
        </div>
        <div class="position-metrics">
          <div><small>实时价格</small><strong class="live-price">${num(p.current_price, 8)}</strong></div>
          <div><small>入场价格</small><strong>${num(p.entry_price, 8)}</strong></div>
          <div><small>止损价格</small><strong>${num(p.stop_price, 8)}</strong></div>
          <div><small>持仓数量</small><strong>${num(p.quantity, 8)}</strong></div>
          <div><small>占用保证金</small><strong>${num(p.margin)} U</strong></div>
          <div><small>报价更新</small><strong class="quote-time"><i></i>${clock(p.price_updated_at)}</strong></div>
        </div>
      </div>`).join("") : `<div class="empty">暂无持仓，系统正在等待严格信号。</div>`;

    $("fills").innerHTML = data.fills.length ? data.fills.map(item => {
      const p = item.payload;
      return `<tr><td>${when(item.occurred_at)}</td>
        <td><strong>${item.symbol || "—"}</strong><small class="table-side ${p.side}">${p.side === "long" ? "多" : p.side === "short" ? "空" : ""}</small></td>
        <td>${exitLabel(p.reason)}</td><td>${num(p.price, 8)}</td>
        <td class="${cls(p.realized_pnl)}">${p.realized_pnl > 0 ? "+" : ""}${num(p.realized_pnl)}</td>
        <td class="${cls(p.realized_r)}">${p.realized_r ? `${p.realized_r > 0 ? "+" : ""}${num(p.realized_r, 2)}R` : "—"}</td>
        <td>${p.peak_r ? `${num(p.peak_r, 2)}R` : "—"}</td>
        <td>${p.mfe_r || p.mae_r ? `${num(p.mfe_r, 2)} / -${num(p.mae_r, 2)}R` : "—"}</td>
        <td>${p.cumulative_fees ? `${num(p.cumulative_fees, 2)}U` : "—"}</td>
        <td>${duration(p.holding_minutes)}</td></tr>`;
    }).join("") : `<tr><td colspan="10" class="empty-cell">暂无成交</td></tr>`;

    $("signals").innerHTML = data.signals.length ? data.signals.slice(0,8).map(item => `
      <div class="feed-item"><div><strong>${item.symbol}</strong><small>${data.strategy_performance[item.payload.strategy_id || "breakout_retest"]?.name || "突破回踩"} · ${item.payload.reason}</small></div>
      <div><span class="side ${item.payload.side}">${item.payload.side}</span><small>${when(item.occurred_at)}</small></div></div>
    `).join("") : `<p class="empty">暂无信号</p>`;

    $("strategy-cards").innerHTML = Object.entries(data.strategy_performance).map(([id, s]) => `
      <div class="strategy-card">
        <div class="strategy-title"><div><strong>${s.name}</strong><small>${s.automatic_trading ? "自动纸面交易" : "仅影子信号"}</small></div><span>${s.selected_signals}/${s.signals} 信号</span></div>
        <div class="strategy-stats">
          <div><small>净盈亏</small><strong class="${cls(s.net_pnl)}">${s.net_pnl >= 0 ? "+" : ""}${num(s.net_pnl)}U</strong></div>
          <div><small>胜率</small><strong>${pct(s.win_rate)}</strong></div>
          <div><small>平均R</small><strong class="${cls(s.average_r)}">${num(s.average_r,2)}R</strong></div>
          <div><small>利润因子</small><strong>${s.profit_factor === null ? "—" : num(s.profit_factor,2)}</strong></div>
        </div>
        <div class="candidate-list"><small>当前候选</small><p>${s.candidates.length ? s.candidates.slice(0,12).join(" · ") : "等待下一轮扫描"}</p></div>
      </div>
    `).join("");

    $("system").innerHTML = `
      <div class="system-row"><span>执行模式</span><strong>PAPER · 全仓模拟</strong></div>
      <div class="system-row"><span>权益高水位</span><strong>${num(data.account.high_watermark)} USDT</strong></div>
      <div class="system-row"><span>胜率</span><strong>${pct(data.performance.win_rate)} · ${data.performance.wins}胜/${data.performance.losses}负</strong></div>
      <div class="system-row"><span>最近错误</span><strong class="${data.errors.length ? "negative" : "positive"}">${data.errors.length}</strong></div>
      <div class="system-row"><span>数据刷新</span><strong>每 1 秒</strong></div>`;
  } catch (error) {
    $("updated").textContent = "连接中断，正在重试";
  }
}
refresh();
setInterval(refresh, 1000);
