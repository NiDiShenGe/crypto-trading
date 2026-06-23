# Bitget 山寨币量化交易系统

基于 Bitget U 本位永续合约真实公开行情运行的本地量化交易系统。目前只执行纸面交易：信号、手续费、滑点、杠杆、持仓和盈亏均在本地模拟，不会向 Bitget 提交真实订单。

> 本项目不承诺盈利。历史回测和纸面交易结果都不能代表未来实盘表现。

## 当前运行状态

- 初始模拟资金：1000 USDT
- 行情来源：Bitget 实盘公开行情
- 交易模式：全仓纸面交易
- 方向：支持做多与做空
- 扫描频率：每根 5 分钟 K 线收盘约 3 秒后扫描
- 持仓报价：WebSocket 实时更新价格和浮动盈亏
- 三个策略均已开启自动纸面交易
- 实盘交易被程序硬性禁止
- 本地 Web 控制台：<http://127.0.0.1:8000>

安全开关必须保持：

```env
EXECUTION_MODE=paper
LIVE_TRADING_ENABLED=false
```

## 三套策略

### 1. 趋势突破

- 从高波动候选币中选取前 8 个市场；
- 使用 2 小时 Donchian 区间、成交量和 EMA 趋势识别突破；
- 支持向上突破做多和向下突破做空；
- 低质量或与市场状态冲突的信号会被过滤。

### 2. 趋势回调延续

- 从最多 60 个市场中按趋势强度选取前 20 个；
- 使用 4 小时 EMA10/30 判断主趋势；
- 等待 1 小时级别回调、缩量和重新放量启动；
- 支持多空双向交易。

### 3. 波动压缩延续

- 使用独立候选评分，不再沿用旧版 15 分钟直接突破模型；
- 要求 4 小时趋势明确；
- 1 小时回调区间不超过前序阶段的 1.2 倍；
- 回调平均成交量不得高于前序阶段；
- 放量恢复趋势后入场，支持多空双向交易。

当同一币种同时出现多个策略信号时，系统按信号评分和策略优先级选择一个信号，不会重复持仓。

## 候选币范围

基础交易池必须同时满足：

- Bitget USDT 本位永续合约；
- 上线至少 30 天；
- 24 小时成交额至少 1000 万 USDT；
- 买卖价差不超过 0.3%；
- 排除稳定币、RWA 和异常市场。

各策略会在基础池上使用自己的候选排序。趋势策略在专属候选池中的历史表现明显优于无差别扫描，因此不要把三个策略简单改成扫描所有币种。

## 风控与出场

- 单笔风险：账户权益的 5%；
- 单币累计风险上限：5%；
- 单日亏损上限：25%；
- 最大回撤：50%；
- 1000 USDT 当前最多同时持有 5 个币；
- 杠杆优先使用交易所允许的最大杠杆，但仓位数量仍由止损距离和单笔风险预算决定；
- 连续亏损冷却当前关闭；
- 同币平仓后至少等待一根 5 分钟 K 线才允许重新入场；
- 禁止同币重复开仓、马丁格尔和亏损补仓。

出场由策略独立参数管理，通常包括：

- 初始结构/ATR 止损；
- 达到指定 R 倍数后移动到保本；
- 2R 分批止盈；
- 剩余仓位使用移动止损；
- 突破失败连续收盘确认；
- 长时间没有达到最低进展时提前退出。

## 安装与配置

需要 Python 3.11 或更高版本。

在 CMD 中创建环境文件：

```cmd
copy .env.example .env
notepad .env
```

在 PowerShell 中：

```powershell
Copy-Item .env.example .env
notepad .env
```

纸面交易不需要 Bitget API Key。将来即使填写了密钥，当前版本仍只允许 `EXECUTION_MODE=paper`。

## 启动系统

交易扫描程序和 Web 控制台应分别在两个 CMD 窗口中运行。

第一个窗口：

```cmd
run.cmd
```

第二个窗口：

```cmd
web.cmd
```

然后访问：

```text
http://127.0.0.1:8000
```

如果 PowerShell 阻止脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

## 邮件通知

QQ 邮箱需要开启 SMTP 服务并生成授权码。`SMTP_PASSWORD` 填写的是授权码，不是 QQ 登录密码。

```env
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=你的QQ号@qq.com
SMTP_PASSWORD=QQ邮箱授权码
ALERT_EMAIL_FROM=你的QQ号@qq.com
ALERT_EMAIL_TO=另一个收件邮箱
```

邮件在纸面开仓、分批止盈和最终平仓成交时发送，不会每次扫描都发送。发件邮箱和收件邮箱可以相同，但 QQ 可能把自发自收邮件折叠到“已发送”、延迟展示或拦截；建议使用另一个收件邮箱。

测试邮件：

```cmd
run.cmd test-email
```

如果没有收到，请检查垃圾箱、已发送、邮箱规则和 QQ SMTP 授权码。

## 常用命令

只扫描和记录信号，不执行纸面开仓：

```cmd
run.cmd scan
```

回测单个币及指定策略：

```cmd
run.cmd backtest BTCUSDT 90
run.cmd backtest BTCUSDT 180 trend_pullback
```

下载并缓存历史数据：

```cmd
run.cmd fetch-history 60 ENAUSDT BICOUSDT WLDUSDT SUIUSDT
```

优化指定策略：

```cmd
run.cmd optimize trend_pullback 60 ENAUSDT BICOUSDT WLDUSDT SUIUSDT
```

执行滚动、逐币剔除和成本压力验证：

```cmd
run.cmd validate-strategies 180 HYPEUSDT SYNUSDT ALLOUSDT UBUSDT TNSRUSDT LABUSDT DEXEUSDT TAOUSDT LAYERUSDT BEATUSDT TRUMPUSDT RESOLVUSDT
```

详细验证结果见 [docs/STRATEGY_VALIDATION.md](docs/STRATEGY_VALIDATION.md)。

## 数据文件

- `data/trader.sqlite3`：纸面账户状态、成交、信号、扫描和错误日志；
- `data/history/`：历史 K 线与资金费率缓存；
- `data/optimization/`：参数优化和策略验证结果。

不要提交 `.env`、API 密钥、QQ 邮箱授权码或其他私密凭据。

## 当前验证结论

- 趋势突破和趋势回调在多币种 180 天验证中达到正期望；
- 新版波动压缩延续在 48 个不同币种、116 笔标准成本交易中平均约 `+0.214R`；
- 波动压缩策略在双倍手续费和滑点下约为 `+0.155R`，尚未达到更严格的 `+0.2R` 门槛；
- 三个策略现阶段应继续使用真实行情纸面交易观察，不应据此直接开启实盘。
