# 新币种策略、过滤器与出场组合优化记录（2026-06-28）

## 目标

使用和上一轮不同的币种，至少 15 个，对以下三类参数做组合优化：

- 入场策略参数；
- 两仪四象过滤器参数；
- 出场方式参数。

要求必须包含优化、测试、验证过程。

## 样本

本轮使用 15 个上一轮未作为主样本的新币种：

```text
VELVETUSDT, SLXUSDT, MAGMAUSDT, JTOUSDT, MYXUSDT,
XPLUSDT, SKYAIUSDT, ONDOUSDT, PUNDIXUSDT, PENGUUSDT,
FARTCOINUSDT, JUPUSDT, PUMPUSDT, ARKUSDT, GRASSUSDT
```

其中：

- `VELVETUSDT`、`SLXUSDT`、`MAGMAUSDT` 为当前符合原始流动性门槛且本地之前没有缓存的新币；
- 其余币种来自 Bitget 当前 USDT 永续合约，放宽成交额门槛后拉取历史数据；
- 本轮研究用这些币做稳健性测试，不代表实盘候选池规则被放宽。

数据：

- 周期：60 天 5m K 线；
- 切分：前 70% 训练，后 30% 验证；
- 成本：默认手续费 `0.06%/边`，滑点 `0.05%/边`；
- 压力测试：对前三名组合做 `1.5x` 和 `2.0x` 成本压力。

## 优化空间

### 策略参数

测试了以下方向：

1. 当前参数；
2. `breakout_active`：放宽突破回踩信号；
3. `breakout_quality`：收紧突破回踩质量；
4. `squeeze_quality`：收紧波动压缩策略；
5. 第二轮额外测试“只开突破回踩”和“只开波动压缩”。

### 过滤器参数

测试了：

1. `off`：关闭两仪四象过滤器；
2. `current15`：当前 15m 两仪四象过滤器；
3. `strict15`：更宽容但更明确的 15m 方向过滤；
4. `opposition4h`：4H 反向过滤。

### 出场参数

测试了：

1. `early_half`
   - `0.8R` 保本；
   - `1.5R` 平一半；
   - `2 ATR` 跟踪。

2. `balanced_third`
   - `1.0R` 保本；
   - `1.5R` 平三分之一；
   - `2 ATR` 跟踪。

3. `runner`
   - `1.0R` 保本；
   - `2.0R` 平三分之一；
   - `3 ATR` 跟踪。

4. `fast_cut`
   - `0.6R` 保本；
   - `1.2R` 平一半；
   - `1.8 ATR` 跟踪。

## 测试过程

第一轮测试：

- 12 个代表组合；
- 包含双策略组合、不同过滤器、不同出场方式。

第一轮结论：

- 所有双策略组合验证集平均 R 均为负；
- `volatility_squeeze` 在这 15 个新币验证集上明显拖累整体；
- 需要拆分为单策略测试。

第二轮测试：

- 24 个单策略组合；
- 分别测试“只开突破回踩”和“只开波动压缩”；
- 对前三名做成本压力测试。

## 最优组合

本轮按验证集与稳健评分排序，最优组合为：

```text
只启用 breakout_retest
策略参数：breakout_active
过滤器：off
出场：balanced_third
```

具体参数：

```toml
# breakout_active
volume_multiplier = 1.1
minimum_breakout_score = 0.78
minimum_breakout_body_atr = 0.25
minimum_trend_efficiency = 0.15
breakout_lookback = 20

# filter
liangyi_filter = off

# balanced_third exit
breakeven_at_r = 1.0
first_take_profit_at_r = 1.5
first_take_profit_fraction = 0.333333
trailing_atr_multiple = 2.0
no_progress_bars = 48
minimum_progress_r = 0.15
failed_breakout_confirmation_bars = 1
```

训练集：

| 指标 | 结果 |
|---|---:|
| 币种数 | 15 |
| 交易数 | 78 |
| 正收益币种 | 6 / 15 |
| 平均收益 | -0.28% |
| 平均 R | -0.006R |
| 最大回撤 | 13.75% |

验证集：

| 指标 | 结果 |
|---|---:|
| 币种数 | 15 |
| 交易数 | 40 |
| 正收益币种 | 5 / 15 |
| 平均收益 | +0.09% |
| 平均 R | +0.0066R |
| 最大回撤 | 9.86% |

成本压力：

| 成本 | 平均收益 | 平均 R | 最大回撤 |
|---:|---:|---:|---:|
| 1.5x | -0.10% | -0.008R | 10.00% |
| 2.0x | -0.29% | -0.0226R | 10.14% |

## 重要结论

1. 本轮 15 个新币不适合当前双策略组合。
   - 双策略最优组合验证集平均 R 仍为负；
   - `volatility_squeeze` 在验证集中交易少且不稳定。

2. 本轮最优组合是“只开突破回踩，关闭两仪四象过滤器，使用 balanced_third 出场”。
   - 但优势非常弱；
   - 成本提高后会转负；
   - 训练集也没有显著正期望。

3. 两仪四象过滤器在这轮没有提供稳定增益。
   - `strict15` 过滤后的结果略低于关闭过滤器；
   - `opposition4h` 没有改善验证结果。

4. 不建议把本轮最优组合直接应用到当前模拟盘。
   - 它只是本轮 15 个新币里的相对最优；
   - 绝对收益和抗成本能力不足；
   - 直接落地可能降低当前系统的总体稳健性。

## 本轮执行结论

本轮已经完成：

- 至少 15 个不同币种；
- 策略参数优化；
- 过滤器参数优化；
- 出场参数优化；
- 训练 / 验证切分；
- 成本压力测试；
- 单策略与双策略对照。

最终结论是：本轮样本未找到足够稳健、值得立即替换当前模拟盘配置的新组合。当前模拟盘配置暂不修改。

