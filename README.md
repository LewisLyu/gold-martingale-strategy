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

网页名称是“黄金保守马丁格尔策略”。个人版采用“本地控制台 + 公网只读状态页”：

- 本机 `127.0.0.1` 可以启动、停止、平仓、调整参数。
- 公网域名只展示状态，不接收 API Key，不允许任何交易控制。
- API key、secret 推荐存本机 `.env` 或系统环境变量。

可以复制 `.env.example` 为 `.env`，然后把真实密钥只放在你电脑本地：

```bash
cp .env.example .env
```

`.env` 示例：

```bash
APP_PASSWORD=换成一个足够长的本地管理密码
APP_SECRET=换成一段随机字符串
BINANCE_API_KEY=你的key
BINANCE_API_SECRET=你的secret
```

实盘启动需要：

- 在本机打开 `http://127.0.0.1:8765`
- 使用 `.env` 或系统环境变量中的 API
- 勾选 `实盘下单`
- 在 `实盘确认` 输入 `黄金保守马丁格尔策略`
- 点击 `启动策略`

页面里的 `手动价格 dry-run` 可以不用 API key 测试策略动作。

控制台已包含：

- `APP_PASSWORD` 本机控制台登录保护。
- API Secret 支持从本机 `.env` / 环境变量读取，不需要提交到网页。
- 公网访问自动进入只读状态页，不能调用交易控制接口。
- 单日最大亏损、单轮最大浮亏、最大累计头寸、最大保证金压力。
- 触发风控后可自动平仓并停止。
- 紧急平仓按钮。
- 最近审计记录，记录启动、停止、绑定、策略动作，但不记录 Secret。

## 线上部署

项目已提供 Dockerfile，可部署到云服务器、Render、Fly.io、Railway 等平台。详见：

[DEPLOY.md](DEPLOY.md)

公网使用必须启用 HTTPS。客户 API 授权默认只保存在服务进程内存里；如果服务重启，客户需要重新绑定。

当前交易所接入状态：

- Binance：已接入 USD-M Futures 行情、杠杆和市价单。
- OKX、Bybit、Bitget、Gate.io、自定义交易所：网站已支持绑定字段和策略参数，但真实行情/下单需要补对应交易所适配器。

## 安全说明

- 程序启动时会自动读取项目根目录 `.env`，也支持系统环境变量；不会打印 key。
- 网页版仍支持本地表单临时绑定，但个人实盘更推荐 `.env`。
- 公网域名只读，不允许绑定 API、启动、停止或紧急平仓。
- 交易所 API 必须关闭提现权限。
- `APP_PASSWORD` 只保护本机控制台；公网默认只有只读状态页。
- 强烈建议在交易所 API 后台设置 IP 白名单，只允许你的服务器固定 IP 调用。
- 杠杆只影响保证金占用，不改变真实亏损。
- `MAX_ORDERS_PER_TICK` 默认是 `1`，避免价格跳过多层时一次性连下多笔单。
- 当前实现使用 MARKET 单，实盘前应确认滑点是否能接受。
