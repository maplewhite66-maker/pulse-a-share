# PULSE 4.0 资金与策略工作台

本地升级保留原有市场总览、交易复盘、模拟账户与地量 TOP 20。资金动态、资金画像、风格回测、四维融合均有侧栏入口，个股打开工作台原有走势页。工作台原有当日行业/概念热力图也接入新视图，地域与历史周期保留原视图。

## 接口与数据来源

| 接口 | 数据与边界 |
| --- | --- |
| `GET /api/sectors/live-flow?kind=industry\|concept[&board=BKxxxx]` | 复用东方财富板块及成分榜；轮询 5 秒，受共享限流、网络与上游刷新约束。每行核验真实源时间、当日交易窗口与 120 秒时效；没有可信时标的快照只可参考。SQLite 保存每分钟真实观测与最近有效快照。选中板块可读净流入前 10 只已校验成分。 |
| `GET /api/stocks/{code}/capital-profile` | 官方交易公开信息规范化档案、腾讯五档委比和东方财富历史资金序列。席位标签描述营业部或交易单元，不确认自然人、算法或资金所有者。模型评分是可解释的规则吻合度。 |
| `GET /api/strategy/hot-money-sim?codes=sh600000,...` | 485 个完整交易日的规则回放，按胜率降序显示。可优先返回最多 20 个模拟持仓标的的已验证盘口报价。持仓和成交只在当前浏览器，和既有模拟账户、真实账本使用不同存储。 |
| `GET /api/strategy/pulse-v4` | 行业已核验净流入前五、时效报价和资金风险证据、尾盘进攻轨与当日地量防守轨、仓位和价格的硬约束。 |

板块 Treemap 以榜内成交额作面积、涨跌定底色、资金方向定边框。分时图跟踪正净额前五和负净额前三，超过三分钟的断点不用于斜率判定。「资金加速流入」为当前净额斜率大于等于每分钟 1000 万元且达到前一段正向斜率的 1.8 倍。「高位资金背离」要求最新板块涨幅至少 2%、涨幅未回落且资金斜率不高于每分钟 −1000 万元。净额条形图展示可观察的方向对比，公开净额不支持逐笔跨板块溯源。

交易公开信息是指定披露条件下的营业部或交易单元数据。专用、关注营业部、拉萨营业部等名称标签与账户身份分开处理。机构、活跃营业部、集中低波动、双向脉冲四类使用多项证据加权。分数不足 50、少于两项证据或领先不足 10 分显示为证据不足；没有当日披露时，只有当日可核对的盘口或其他特征才允许生成当日形态推断。现行披露口径参考[上交所交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)。

## 授权快照输入

在具备数据使用及页面展示权的环境设置 `PULSE_ADVANCED_DATA_DIR=/data/pulse-v4`。生产数据由授权提供方或自己的合规任务生成，先完整写临时文件，再原子重命名。所有金额均为人民币元，涨跌、换手率为百分点，时点必须为带时区 ISO 时间。文件都是 JSON 对象，统一带 `schema_version: 1`，`source` 只填写可公开显示的来源名称。此模块不上传快照，不自动订阅付费接口。

| 文件 | 数据协议 |
| --- | --- |
| `capital/sh600000.json` | `sessions` 为递增交易日数组（至多 90）；`reports` 为 `{day,published_at,scope:"daily",reason,turnover,buy:[{seat,buy,sell}],sell:[...]}` 数组。买卖各最多取前五。重复和多日合并披露不作日榜累加。可选 `flows:[{day,known_at,dark_net,northbound_net}]`；缺失项应为 null。 |
| `capital/sh600000.json` 的 `features` | `observed_at` 加上由资料支持的布尔特征：`connect_streak`、`steady_trend`、`moderate_turnover`、`breakout_pulse`、`follow_through`、`chip_concentration`、`shrinking_volume`、`fixed_size_cluster`、`shareholder_decline`、`matched_bursts`、`sharp_reversal`、`local_branch_overlap`。仅在该观察仍有效时使用。 |
| `backtest.json` | 24 MB 以内。递增的 485–540 个市场交易日 `sessions`，及 `stocks:[{code,bars:[...]}]`。每行日线至少要有 `day,known_at,open,close,adj_factor,amount,limit_up,limit_down,risk_warning,suspended,features`。可加 `low,volume,pct` 供计算回踩、缩量和弱转强。未来披露和未完成日线不参与。结果按提供方覆盖的实际股票池计算，接入档案仍需说明抽样范围、退市股及缺失处理。 |
| `live/YYYY-MM-DD.json` | `day,as_of,items:[...]`，每行包含 `code,name,observed_at,board,price,amount,pct,previous_turnover,limit_up,limit_down,risk_warning,suspended,features`。实时模拟需有 `bid,ask,bid_lots,ask_lots`，V4 进攻轨另需 `vwap`。已持仓标的应持续供价，包括超出初始入场价格上限的标的。 |
| `sectors/YYYY-MM-DD.json` | 可选授权板块源。包含 `day,as_of` 和 `industry/concept:{total,items:[...]}`。每类须为全量、无重复且时标有效，条目字段为 `code,name,market_time,main_net,super_net,large_net,amount,pct,leader,leader_code`。授权快照不可用时回退东方财富和最近有效缓存。 |
| `sector-members/BKxxxx.json` | 可选授权成分净额。包含 `board,as_of,items:[{code,name,price,pct,main_net,market_time}]`，成分净额只展示可校验的正净额前十。 |

回放和实时筛选共用五套明示条件：周期龙头（主线、启动期、首板/二板、分歧确认且非退潮），容量核心（成交额严格大于 15 亿元、机构和活跃席位合力），催化反包（已披露催化、反包与弱转强），缩量首阴（主线、缩量、回踩收复 MA5），机构合力趋势（机构净买延续、多头、回踩收复 MA10/20、活跃资金支持）。历史 OHLCV 充足时在后端计算相应的量价形态。情绪周期、主线归属、披露与逐笔特征仍由授权数据管道按时点规范化。公开昵称系策略参数组的描述，未还原个人交易。

风格特征使用以下字段，缺失的必要项不会被视为满足：

- 共用：`high_position`。情绪模型还用 `sector_leader,cycle_launch,early_limit_board,disagreement_confirmed,cycle_retreat`；容量模型用 `sector_leader,institution_active`；催化模型用 `catalyst_disclosed,engulfing,weak_to_strong`。
- 首阴模型用 `sector_leader,first_bearish_pullback,shrinking_volume,ma5_reclaimed`；趋势模型用 `institution_buy_streak,trend_up,reclaimed_ma10_or_ma20,active_flow_support`。
- V4 另要求 `capital_verified=true`，并具有明确的 `fast_reversal_risk=false,distribution_risk=false`。机构活跃合力或筹码集中并缩量加分。进攻轨要求 `first_bearish_pullback,shrinking_volume,vwap_reclaimed`，且价在 VWAP 到其上方 0.6% 内。防守轨只读同日地量 TOP 20 中 120 日分位不超过 10% 的标的。

## 回放及风控

- 回放以 T 日盘后已知信息选股，T+1 开盘入场。容量风格以 T+2 开盘作早盘代理，其他按 T+3 或 T+5 收盘计划退出；缩量首阴可按跌破信号 MA5 后下一可交易时点退出。无法按时退出时延至首个可用收盘，尚无法退出单独计数。日线无法验证盘口先后、瞬时冲高或盘中做 T。每个风格独立回放，单笔模型占用 20% 资金（回放采用连续份额），计入费用与 0.10% 单边滑点；单笔、次日开盘溢价和年化的计量各自独立。
- 浏览器演练使用可核对的买一/卖一、100 股整手和相应深度，计算费用和滑点；买入不超过 20%，不能同日卖出。盘口缺失、价格过期、涨停附近买入或跌停附近卖出均暂停。没有实盘订单或账户写入。
- V4 主板非 ST 且价格低于 50 元，前一日换手大于 35% 排除，14:45 前涨幅大于 4% 排除，单票最多 30%，与地量防守重叠的标的继续执行 20%。进攻轨 S1 为 +3.0%、参考止损 −2.5%；防守轨 S1 为 +2.3%、参考止损 −1.8%。以上为模型观察位。

## 发布验收

先运行现有 CI 的 Python 编译、后端测试、Node 22 前端测试及 TypeScript/Vite 构建，再以真实授权样本复核披露时标、数据源许可、至少一个交易日板块更新间隔和数据过期行为。另分别查验浏览器本机模拟隔离与授权档案只读端点。持续采集可单独配置 `PULSE_SECTOR_FLOW=1`，按当前服务和上游限额运行。没有授权点时档案时，回测与风格和融合信号显示等待状态。生产发布继续沿用既有服务、持久卷、回滚和 CI 验收条件。
