# ideal-link-extractor 服务器部署指南

本文档介绍如何使用 Docker 将 ideal-link-extractor 部署到 Linux 服务器。

## 环境要求

- 服务器操作系统：Linux（推荐 Ubuntu 20.04+ / CentOS 7+）
- Docker Engine 20.10+ 与 Docker Compose v2
- 服务器能访问外网（用于拉取镜像和 pip 安装依赖）
- 目标端口 8060 未被占用

## 第一步：上传项目到服务器

将整个项目目录上传到服务器 `/opt/apps/ideal-link-extractor`：

```
scp -r ./ideal-link-extractor-open-source-20260712 root@your-server:/opt/apps/ideal-link-extractor
```

或者在服务器上使用 git 克隆（如果有仓库）。

## 第二步：配置必要文件

进入项目目录后，需要准备以下配置文件的初始版本（镜像构建时不会包含它们）：

```
cd /opt/apps/ideal-link-extractor
```

### 2.1 Kakao Pay 代理配置

```bash
# 将你的代理种子写入 kakao/proxy_seeds.txt
vim kakao/proxy_seeds.txt
```

每行一个代理，格式为 `http://user:pass-country=KR-xxx@host:port`，例如：
```
http://5652965-c7a22eec:3a9089c8-country=KR-93675085@gate.kookeey.info:1000
```

### 2.2 Token 文件（按需配置）

```bash
touch token.txt kakao/token.txt upi/token.txt blik/token.txt pix/token.txt twint/token.txt
```

如果有固定 token，直接写入对应文件。大多数情况下 token 通过 UI 页面设置，文件保持空即可。

### 2.3 前置代理（可选）

如果服务器需要通过本机代理才能访问 Kookeey 等外部代理，可以在 `docker-compose.yml` 中配置前置代理环境变量：

```yaml
environment:
  - IDEAL_PRE_PROXY=socks5h://127.0.0.1:1080
  - KAKAO_PRE_PROXY=socks5h://127.0.0.1:1080
  - UPI_PRE_PROXY=socks5h://127.0.0.1:1080
```

不需要前置代理时保持为空即可。

## 第三步：启动服务

### 使用 docker compose（推荐）

```bash
cd /opt/apps/ideal-link-extractor

# 构建镜像并启动
docker compose up -d --build

# 查看容器状态
docker compose ps

# 查看日志
docker compose logs -f
```

### 或使用原生 docker 命令

```bash
# 构建镜像
docker build -t ideal-link-extractor .

# 启动容器
docker run -d \
  --name ideal-link-extractor \
  --restart unless-stopped \
  -p 8060:8060 \
  -e TZ=Asia/Shanghai \
  -v ./kakao/proxy_seeds.txt:/app/kakao/proxy_seeds.txt \
  -v ./kakao/token.txt:/app/kakao/token.txt \
  -v ./upi/proxy_seeds.txt:/app/upi/proxy_seeds.txt \
  -v ./upi/token.txt:/app/upi/token.txt \
  -v ./blik/token.txt:/app/blik/token.txt:ro \
  -v ./pix/token.txt:/app/pix/token.txt:ro \
  -v ./twint/token.txt:/app/twint/token.txt:ro \
  -v ./logs:/app/logs \
  ideal-link-extractor
```

启动后访问 `http://your-server-ip:8060` 即可打开支付提链控制台。

## 第四步：常用运维操作

```bash
# 重启服务（配置修改后需要）
docker compose restart

# 停止服务
docker compose down

# 查看实时日志
docker compose logs -f

# 查看最近 100 行日志
docker compose logs --tail=100

# 进入容器调试
docker compose exec ideal-link-extractor bash
```

## 第五步：配置反向代理（可选）

如果希望通过域名访问并启用 HTTPS，可以使用 Nginx 反向代理：

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8060;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## 目录结构说明

```
/opt/apps/ideal-link-extractor/
├── Dockerfile              # 镜像构建文件
├── docker-compose.yml      # 服务编排
├── .dockerignore           # 构建排除文件
├── requirements.txt        # Python 依赖
├── ideal_ui.py             # Web 主程序
├── ideal_ui.html           # 前端页面
├── kakao/
│   ├── proxy_seeds.txt     # Kakao Pay 代理池（需配置）
│   ├── token.txt           # Kakao Pay token（可选）
│   └── logs/               # 运行日志
├── upi/
│   ├── proxy_seeds.txt     # UPI 代理池
│   ├── token.txt           # UPI token
│   └── logs/
├── blik/
│   ├── token.txt           # BLIK token
│   └── logs/
├── pix/
│   ├── token.txt           # PIX token
│   └── logs/
├── twint/
│   ├── token.txt           # TWINT token
│   └── logs/
└── logs/                   # 全局日志
```

## 故障排查

### 容器启动后立即退出

```bash
docker compose logs --tail=50
```

常见原因：8000/8060 端口被占用，或依赖安装失败。

### 代理预检超时

容器内访问 Kookeey 等外部代理不通时，检查：
1. 服务器能否直接访问代理地址：`docker compose exec ideal-link-extractor curl -x http://proxy-url ipinfo.io`
2. 如不通，配置前置代理（KAKAO_PRE_PROXY 等环境变量）

### 修改代理种子后不生效

代理种子通过 volume 挂载，修改后重启容器即可：
```bash
docker compose restart
```

### 更新项目代码

```bash
cd /opt/apps/ideal-link-extractor
git pull  # 或重新上传代码
docker compose up -d --build
```

### 代理输入格式

所有业务线路的代理池、手动代理池和前置代理都支持以下裸格式：

- `HOST:PORT:USER:PASS`
- `HOST:PORT@USER:PASS`
- `USER:PASS:HOST:PORT`
- `USER:PASS@HOST:PORT`

程序会在业务流程开始前统一规范为 `scheme://USER:PASS@HOST:PORT`。未写协议时按页面选择或环境变量里的默认协议补齐。