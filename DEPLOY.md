# 线上部署说明

这个版本可以部署到公网服务器或容器平台。线上运行时必须使用 HTTPS，因为客户会在页面填写交易所 API Key。

## 当前能力

- 多客户会话隔离：每个浏览器 session 使用独立策略状态和独立 API 内存。
- API Key/Secret/Passphrase 只保存在当前服务进程内存，不写入文件。
- Binance 已接入真实 USD-M Futures 行情、杠杆、市价单。
- OKX、Bybit、Bitget、Gate.io、自定义交易所已有 UI 和绑定入口，但真实行情/下单适配器仍待接入。

## Docker 本地验证

```bash
docker build -t gold-martingale .
docker run --rm -p 8765:8765 gold-martingale
```

打开：

```text
http://127.0.0.1:8765
```

## 部署到一台云服务器

服务器需要安装 Docker，然后在项目目录运行：

```bash
docker build -t gold-martingale .
docker run -d --name gold-martingale --restart unless-stopped -p 8765:8765 gold-martingale
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

这些平台可直接识别 `Dockerfile`。部署时设置：

```text
PORT=8765
HOST=0.0.0.0
```

注意：如果平台实例重启，内存里的 API 授权和策略状态会丢失，客户需要重新绑定。要做商业版，需要接数据库和加密密钥托管。

## 商业版上线前必须补的安全项

- 用户登录系统，不能只靠浏览器 session。
- API Key 加密存储或只使用短期内存授权。
- 操作日志、风控限额、异常熔断。
- 真实交易前的交易所适配器验收测试。
- 每个交易所独立的最小下单量、数量精度、合约类型、保证金模式、持仓模式处理。
