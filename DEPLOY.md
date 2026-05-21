# 线上部署说明

这个版本可以部署到公网服务器或容器平台。个人使用建议采用“本地控制台 + 公网只读状态页”：交易 API Key 只放在你自己的电脑或私有服务器上，公网页面只展示状态。

## 当前能力

- 个人单实例状态：本机控制台和公网只读页展示同一套策略状态。
- API Key/Secret/Passphrase 只保存在当前服务进程内存，不写入文件。
- `APP_PASSWORD` 本机控制台登录保护。
- 公网只读状态页：不接收 API Key，不允许启动、停止、重置或紧急平仓。
- 风控阈值：单日最大亏损、单轮最大浮亏、最大累计头寸、最大保证金压力。
- 紧急平仓和审计记录。
- Binance 已接入真实 USD-M Futures 行情、杠杆、市价单。
- OKX、Bybit、Bitget、Gate.io、自定义交易所已有 UI 和绑定入口，但真实行情/下单适配器仍待接入。

## Docker 本地验证

```bash
docker build -t gold-martingale .
docker run --rm -p 8765:8765 \
  -e APP_PASSWORD="换成管理密码" \
  -e APP_SECRET="换成随机字符串" \
  gold-martingale
```

打开：

```text
http://127.0.0.1:8765
```

## 部署到一台云服务器

服务器需要安装 Docker，然后在项目目录运行：

```bash
docker build -t gold-martingale .
docker run -d --name gold-martingale --restart unless-stopped -p 8765:8765 \
  -e APP_PASSWORD="换成管理密码" \
  -e APP_SECRET="换成随机字符串" \
  -e BINANCE_API_KEY="可选：个人使用时放这里" \
  -e BINANCE_API_SECRET="可选：个人使用时放这里" \
  gold-martingale
```

再用 Nginx/Caddy/Cloudflare Tunnel 把公网域名反代到：

```text
http://127.0.0.1:8765
```

要求：

- 域名必须启用 HTTPS。
- 不要把服务裸露在没有 HTTPS 的公网地址上。
- 如果客户交易所 API 开了 IP 白名单，白名单要填这台服务器的公网出口 IP。

## Render / Fly.io / Railway

这些平台可直接识别 `Dockerfile`。如果只做公网展示，可以不在 Render 上放交易所 API Key。真实交易机器人建议跑在你的 Mac 或固定 IP 的 VPS 上。

部署时设置：

```text
PORT=8765
HOST=0.0.0.0
APP_PASSWORD=可选；公网只读页不需要交易控制密码
APP_SECRET=换成随机字符串
```

注意：如果平台实例重启，内存里的 API 授权和策略状态会丢失，客户需要重新绑定。要做商业版，需要接数据库和加密密钥托管。

公网访问会自动进入只读状态页，不允许进入交易控制界面。本地 `127.0.0.1` 才能执行控制操作。

不建议用免费实例跑实盘自动交易：实例可能休眠或重启。实盘建议使用固定公网 IP 的 VPS，并在交易所 API 后台设置 IP 白名单。

## 商业版上线前必须补的安全项

- 正式用户体系：注册、找回、设备管理、会话撤销。
- API Key 加密存储或只使用短期内存授权；当前版本选择短期内存授权。
- 更完整的通知系统：Telegram、邮件、短信。
- 真实交易前的交易所适配器验收测试。
- 每个交易所独立的最小下单量、数量精度、合约类型、保证金模式、持仓模式处理。
