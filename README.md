# XAUUSDT Defensive Martingale Bot

本项目按 `$xau-martingale-risk-manager` 的规则运行：

- 首仓按 U 本位名义金额计算，默认 `500U` 本金对应 `45U` 首仓。
- 首仓和第一次加仓使用同样数量。
- 后续每跌 `1%`，加仓数量翻倍。
- 最大加仓 `8` 次。
- 第 8 次加仓后再跌 `1%`，止损关闭本轮。
- 有加仓时，反弹到加仓部分的保本线后，只减掉加仓部分。
- 只剩首仓时，上涨 `1%` 止盈并结束本轮。

默认是 dry-run，不会真实下单。

## 查看头寸表

```bash
python3 xau_martingale_bot.py table --equity 500 --initial-notional 45 --leverage 35
```

如果不传 `--initial-notional`，程序会按 `45U / 500U` 自动缩放：

```bash
python3 xau_martingale_bot.py table --equity 400
```

## 手动价格模拟

第一次 tick 会开首仓：

```bash
python3 xau_martingale_bot.py tick --price 4000 --step-size 0.001
```

价格跌到下一层会 dry-run 加仓：

```bash
python3 xau_martingale_bot.py tick --price 3960 --step-size 0.001
```

状态保存在 `xau_bot_state.json`。测试时可以手动删除这个文件来重置模拟。

## Binance Testnet

先在本地环境放 key，不要写进聊天或提交到 git：

```bash
export BINANCE_API_KEY="..."
export BINANCE_API_SECRET="..."
```

测试网真实发单需要同时传 `--live --testnet`：

```bash
python3 xau_martingale_bot.py tick --testnet --live
```

## 实盘

实盘需要显式传 `--live`，不传就是 dry-run：

```bash
python3 xau_martingale_bot.py loop --live --interval 15
```

建议先只跑 dry-run 和 testnet，确认交易对存在、数量精度、最小下单量、手续费和标记价格行为都符合预期后，再考虑实盘。

## 本地网页控制台

启动网站：

```bash
python3 web_server.py
```

然后打开：

```text
http://127.0.0.1:8765
```

网页名称是“黄金保守马丁格尔策略”。API key、secret 和部分交易所需要的 passphrase 只发送到本机后端内存，不会写入文件。实盘启动需要：

- 选择交易所并绑定 API
- 勾选 `实盘下单`
- 填入 API key 和 secret
- 在 `实盘确认` 输入 `黄金保守马丁格尔策略`
- 点击 `启动策略`

页面里的 `手动价格 dry-run` 可以不用 API key 测试策略动作。

## 线上部署

项目已提供 Dockerfile，可部署到云服务器、Render、Fly.io、Railway 等平台。详见：

[DEPLOY.md](DEPLOY.md)

公网使用必须启用 HTTPS。客户 API 授权默认只保存在服务进程内存里；如果服务重启，客户需要重新绑定。

当前交易所接入状态：

- Binance：已接入 USD-M Futures 行情、杠杆和市价单。
- OKX、Bybit、Bitget、Gate.io、自定义交易所：网站已支持绑定字段和策略参数，但真实行情/下单需要补对应交易所适配器。

## 安全说明

- 程序只从环境变量读取 API key，不打印、不保存 key。
- 网页版从表单读取 API key，只保存在运行中的后端内存。
- 杠杆只影响保证金占用，不改变真实亏损。
- `MAX_ORDERS_PER_TICK` 默认是 `1`，避免价格跳过多层时一次性连下多笔单。
- 当前实现使用 MARKET 单，实盘前应确认滑点是否能接受。
