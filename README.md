# Bitget 山寨币量化交易系统 2.0

这是一个基于 Bitget U 本位永续合约真实公开行情运行的本地量化交易系统。

当前版本定位为 **2.0 纸面交易系统**：系统使用 Bitget 实盘行情扫描、生成信号、模拟开仓、模拟平仓、计算手续费/滑点/杠杆/权益变化，但不会向 Bitget 提交真实订单。

> 重要提醒：本项目不承诺盈利。历史回测、参数优化和纸面交易结果都不能代表未来实盘表现。当前版本仍处于真实行情纸面观察阶段，不建议直接开启实盘。

## 2.0 核心变化

- 主策略从多策略混合收敛为 **两套主策略**：
  - `breakout_retest`：2H 放量突破策略；
  - `volatility_squeeze`：4H 趋势 + 1H 缩量回调延续策略。
- `trend_pullback` 独立策略已关闭，不再作为单独开仓策略使用。
- 新增/强化 **4H 两仪四象过滤器**，用于过滤主策略假信号。
- 新增两仪四象提前退出：
  - 只有当 **4H 两仪四象连续 2 根确认反向**；
  - 且当前持仓最高浮盈还没有达到 `1R`；
  - 才会提前退出。
- 当前纸面资金重置为 `1000 USDT`。
- 持仓价格和浮动盈亏支持实时刷新。
- Web 控制台和邮件通知继续保留。
- 已生成 TradingView 策略脚本，便于单币图表观察和辅助验证。

## 当前运行状态

- 交易市场：Bitget USDT-FUTURES / U 本位永续合约
- 交易模式：纸面交易 `paper`
- 保证金模式：全仓 `crossed`
- 行情来源：Bitget 实盘公开行情
- 初始模拟权益：`1000 USDT`
- 方向：支持做多和做空
- 扫描节奏：每根 5 分钟 K 线收盘约 3 秒后触发一次系统扫描
- 实盘下单：当前代码层面禁止
- Web 控制台：<http://127.0.0.1:8000>
- 通知方式：QQ 邮箱 SMTP

必须保持以下安全开关：

```env
EXECUTION_MODE=paper
LIVE_TRADING_ENABLED=false
```

## 当前策略结构

### 1. 突破回踩 `breakout_retest`

定位：捕捉高波动币种的 2H 放量趋势突破。

当前主要逻辑：

- 从高波动候选池里选取前 `8` 个市场；
- 使用 **2H K 线**判断 Donchian 区间突破；
- 要求成交量放大；
- 要求 EMA10/EMA30 趋势同向；
- 要求突破实体足够大；
- 要求收盘位置靠近突破方向；
- 要求 ATR/价格达到最低波动率门槛；
- 支持做多和做空；
- 入场前经过 4H 两仪四象过滤器确认方向。

关键周期：

- 入场信号周期：`2H`
- 两仪四象过滤周期：`4H`

### 2. 波动压缩延续 `volatility_squeeze`

定位：捕捉 4H 趋势中的 1H 缩量回调后重新启动。

当前启用的是趋势延续分支：

```toml
squeeze_use_trend_continuation = true
```

当前主要逻辑：

- 从高波动候选池里选取前 `20` 个市场；
- 使用 **4H EMA10/EMA30**判断主趋势；
- 要求 4H 趋势效率达到门槛；
- 等待 **1H 回调**；
- 回调阶段要求波动区间收缩；
- 回调阶段要求成交量不高于前序阶段；
- 1H 重新放量突破最近回调结构后入场；
- 支持做多和做空；
- 入场前经过 4H 两仪四象过滤器确认方向。

关键周期：

- 入场确认周期：`1H`
- 主趋势周期：`4H`
- 两仪四象过滤周期：`4H`

## 两仪四象过滤器

两仪四象目前不作为独立开仓策略，而是作为两个主策略的质量过滤器。

当前参数：

```toml
adaptive_timeframe_minutes = 240
efficiency_period = 20       # N1
efficiency_range = 20        # N2
momentum_ema_period = 20
minimum_signal_score = 0.55
```

也就是：

> 4H 两仪四象过滤器，N1=20，N2=20。

过滤逻辑概括：

- 用市场效率系数判断趋势纯度；
- 根据效率动态调整动量统计周期；
- 用成交量加权价格动量判断多空方向；
- 当主策略方向与两仪四象方向明显冲突时，过滤掉该信号。

提前退出逻辑：

- 如果持仓尚未达到 `1R`；
- 且 4H 两仪四象连续 `2` 根 K 线确认反向；
- 系统会触发 `liangyi_filter_exit` 提前平仓。

## 候选币范围

基础交易池必须同时满足：

- Bitget U 本位永续合约；
- 上线至少 `30` 天；
- 24 小时成交额大于 `10,000,000 USDT`；
- 买卖价差小于 `0.3%`；
- 排除稳定币；
- 排除 RWA；
- 排除成交异常市场。

注意：系统不是直接无差别扫描所有币，而是先建立高波动候选池，再由不同策略在自己的候选范围内排序和筛选。

## 风控参数

当前主要风控：

```toml
risk_per_trade = 0.05
maximum_symbol_risk = 0.05
daily_loss_limit = 0.25
maximum_drawdown = 0.50
test_maximum_positions = 3
production_maximum_positions = 5
minimum_leverage = 2
maximum_leverage = 125
use_exchange_max_leverage = true
enable_consecutive_loss_limit = false
reentry_cooldown_bars = 1
```

解释：

- 单笔风险：账户权益的 `5%`
- 单币风险上限：账户权益的 `5%`
- 单日亏损上限：`25%`
- 最大回撤：`50%`
- 测试期最多同时持仓：`3`
- 正式期最多同时持仓：`5`
- 杠杆优先使用交易所允许的最高杠杆；
- 但真实下单数量仍由止损距离和单笔风险预算决定；
- 连续亏损冷却当前关闭；
- 同币平仓后至少等待 1 根 5 分钟 K 线才允许重新入场。

## 出场逻辑

两个主策略当前使用相同的 2.0 出场框架：

- 初始止损：
  - 使用 ATR 和结构位计算；
  - 以更保守的一侧作为止损。
- 保本：
  - 达到 `0.8R` 后，止损移动到开仓价附近。
- 第一档止盈：
  - 达到 `1.5R` 后，平掉 `50%` 仓位。
- 移动止损：
  - 达到保本条件后，剩余仓位使用 ATR 移动止损。
- 突破失败退出：
  - 如果价格重新收回关键失效位，触发 `failed_breakout`。
- 无进展退出：
  - 如果长时间没有达到最低进展要求，触发 `no_progress_exit`。
- 两仪反向提前退出：
  - 如果持仓未达到 `1R`，且 4H 两仪四象连续 2 根反向，触发 `liangyi_filter_exit`。

## 安装与配置

需要 Python 3.11 或更高版本。

CMD：

```cmd
copy .env.example .env
notepad .env
```

PowerShell：

```powershell
Copy-Item .env.example .env
notepad .env
```

纸面交易不需要 Bitget API Key。即使 `.env` 中填写了 API 信息，只要保持：

```env
EXECUTION_MODE=paper
LIVE_TRADING_ENABLED=false
```

系统就不会向交易所提交真实订单。

## 启动系统

建议分别打开两个 CMD 窗口。

第一个窗口启动交易扫描和纸面撮合：

```cmd
run.cmd
```

第二个窗口启动 Web 控制台：

```cmd
web.cmd
```

然后访问：

```text
http://127.0.0.1:8000
```

如果 PowerShell 阻止脚本执行，可以使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

## 邮件通知

当前通知渠道为 QQ 邮箱 SMTP。

`.env` 示例：

```env
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=你的QQ号@qq.com
SMTP_PASSWORD=QQ邮箱授权码
ALERT_EMAIL_FROM=你的QQ号@qq.com
ALERT_EMAIL_TO=收件邮箱@qq.com
```

说明：

- `SMTP_PASSWORD` 填写 QQ 邮箱授权码，不是 QQ 登录密码；
- 发信邮箱和收信邮箱可以相同；
- 但自发自收有时会被 QQ 邮箱折叠到“已发送”、延迟展示或拦截；
- 邮件一般在开仓、分批止盈、平仓等关键事件触发，不会每次扫描都发送。

测试邮件：

```cmd
run.cmd test-email
```

## 常用命令

只扫描和记录信号，不执行纸面开仓：

```cmd
run.cmd scan
```

回测单个币种：

```cmd
run.cmd backtest BTCUSDT 90
```

回测指定策略：

```cmd
run.cmd backtest BTCUSDT 180 breakout_retest
run.cmd backtest BTCUSDT 180 volatility_squeeze
```

下载并缓存历史数据：

```cmd
run.cmd fetch-history 60 BTCUSDT ETHUSDT SOLUSDT DOGEUSDT
```

优化指定策略：

```cmd
run.cmd optimize volatility_squeeze 60 BTCUSDT ETHUSDT SOLUSDT DOGEUSDT
```

执行多币种验证：

```cmd
run.cmd validate-strategies 180 BTCUSDT ETHUSDT SOLUSDT DOGEUSDT
```

## Web 控制台

本地控制台地址：

```text
http://127.0.0.1:8000
```

主要展示：

- 账户权益；
- 纸面现金；
- 持仓；
- 实时价格；
- 浮动盈亏；
- 最近成交；
- 最近信号；
- 最近错误；
- 策略表现；
- 扫描状态。

第一版 Web 控制台只建议本机访问，不建议暴露到公网。

## TradingView 脚本

项目已经提供两个 TradingView 策略脚本：

- `tradingview/breakout_retest_strategy.pine`
- `tradingview/volatility_squeeze_continuation_strategy.pine`

用途：

- 在单个币种图表上观察策略信号；
- 辅助肉眼验证趋势、突破、回调和两仪过滤；
- 辅助比较不同币种的结构表现。

注意：

- TradingView 脚本无法完整复刻 Bitget 全市场候选币扫描；
- 也无法完整复刻系统级风控、持仓上限、接口异常保护和本地撮合状态；
- 它更适合作为策略观察工具，而不是替代本地交易系统。

## 数据文件

- `data/trader.sqlite3`：纸面账户状态、成交、信号、扫描和错误日志；
- `data/history/`：历史 K 线与资金费率缓存；
- `data/optimization/`：参数优化和策略验证结果。

不要提交以下内容：

- `.env`
- Bitget API Key
- Bitget API Secret
- Bitget API Passphrase
- QQ 邮箱授权码
- 任何其他私密凭据

## 当前验证结论

截至 2.0 版本：

- 系统仍处于纸面交易观察阶段；
- 当前更重视信号质量、出场质量和风险可控，而不是盲目提高开仓频率；
- 两仪四象目前更适合作为过滤器，而不是独立始终在场策略；
- 新增的两仪提前退出逻辑用于降低未达到 1R 前的错误方向持仓；
- 后续重点应继续围绕：
  - 过滤器参数验证；
  - 出场规则稳定性；
  - 不同币种池表现；
  - 手续费和滑点压力测试；
  - 真实行情纸面交易复盘。
