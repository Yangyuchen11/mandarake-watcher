# Mandarake 【ドール】 专区商品监控爬虫与行情看板

专为日本中古特化连锁名店 **Mandarake (まんだらけ)** (`https://order.mandarake.co.jp/order/?lang=ja`) 打造的针对大分类 **【ドール】(Doll)** 及其全线下属二级、三级子分类的商品情报批量爬取、行情归档、智能阅览与云端推送监控系统。

工具专注于：**高可靠爬取数据 + 实时通知推送（Discord / 邮件）+ 商品智能阅览与历史行情中心**。想买的商品通过提醒或阅览中心一键直达官网购买。

---

## ✨ 核心特性与设计亮点

### 1. 深度聚焦【ドール】全品类体系 (Category Hierarchy)
- **大分类限定**：专精针对 Mandarake 大分类 `020114` (ドール)，精准收录其下属所有层级分类商品，绝无杂项干扰。
- **全子分类覆盖**：
  - `02011401`: 書籍・雑誌
  - `02011402`: ひめかずら(ドールウィッグ) *(含 LL/L/M/MS/S 尺寸、长短发及各类发色细分)*
  - `02011403`: 大型キャストドール(スーパードルフィーなど) *(含 60cm SD / 40cm MSD / 30cm 幼SD / 30cm以下)*
  - `02011404`: 大型ソフビドール(ドルフィードリームなど) *(含 60cm DD / 40cm MDD 等)*
  - `02011405`: 1/6～1/12ドール(～30cm) *(含 ネオブライス / ミディブライス / プチブライス / リカちゃん / ジェニー / バービー / プーリップ / momokoDOLL / ピュアニーモ / リビングデッドドールズ / ベッツィー / ワンダーフロッグ / その他)*
  - `02011406`: 創作人形
  - `02011429`: その他ドール
  - `02011431`: ドール衣装 *(含 60cm / 40~50cm / 30cm / 27cm / 22cm / 1/12 等尺寸)*
  - `02011432`: 中古ドールウィッグ *(含 SD/DD / 幼SD / MSD 尺寸)*
  - `02011433`: ドールアイ
  - `02011434`: ドール用小物・家具
- **按分类批量获取**：支持按分类依次深度爬取该分类下所有商品，最大单页 `dispCount=240` 极大提速，支持自动连续翻页直至最后一页。

### 2. 丰富完备的多维检索与在库筛选 (Product Browser)
- **整卡点击直达官网**：商品卡片整张可点击，悬浮微光动效，点击任意区域直接在新标签页中打开 Mandarake 官网详情页。
- **多状态筛选**：
  - 🟢 **仅在库 (In Stock)**：快速定位当前所有真实在售且可购买的娃类好物。
  - 🔴 **仅售罄 (Sold Out)**：用于历史成交价与稀有娃品行情参考。
  - ⚪ **全部商品 (All)**：全量回溯。
- **多关键词 AND 联合搜索**：输入多个关键词以空格隔开（例如 `DearSD はな` 或 `Blythe OF`），自动进行联合筛选。
- **常用标签预设**：内置 MDD / DDP / DD / SDGr / 幼SD / Blythe 等快捷标签，支持自由自定义增删。
- **排除词过滤**：支持 `難あり`、`ジャンク` 等瑕疵或不需要的词汇自动排除。
- **价格区间检索**：自由指定最低价与最高价。
- **多向排序**：支持最新更新、ID降序、价格高低等多向排序。

### 3. 定点上新监控与远程即时提醒 (Drop Monitor & Push Notifications)
- **扫描频率与弹性重试机制**：
  - **自定义扫描周期**：可在控制台自由设置每隔多少秒（如 `10s`、`30s`、`60s`）对目标商品执行一次扫描。
  - **失败重试参数**：可设定遭遇网络抖动或官网繁忙状态码 (429/503) 时的**重试等待间隔**（如 `5s`）与**最大重试次数**，最大程度防止漏报与假死。
- **灵活的关键词监控规则清单 (Watch Rules)**：
  - 支持多词联合匹配：**AND 模式**（如 `幼SD` + `はな` 同时包含）与 **OR 模式**（如 `Blythe` 或 `ブライス` 满足其一）。
  - **排除词过滤**：如自动排除 `難あり`、`ジャンク` 等瑕疵品。
  - **最高限价过滤**：超出预设最高金额的商品自动过滤不报。
- **远程推送渠道**：
  - 🤖 **Discord Webhook**：命中新商品即刻富文本卡片推送到 Discord 频道，手机/电脑秒级弹窗响铃，点击链接直达商品页。
  - 📧 **SMTP 邮件通知**：支持 QQ 邮箱、163、Gmail 等，上新邮件直达手机。

### 4. 永久持久化与云原生支持 (Data Persistence & Docker)
- 所有爬取到的商品档案、分类进度、监控规则与通知配置统一持久化保存在 `data/` 目录中。
- Docker 容器与 Linux 服务器通过挂载卷 `./data:/app/data` 绑定，容器重置、销毁或镜像升级数据绝对不丢失。
- **多渠道即时推送 (Multi-Channel Notifiers)**：
  - 📧 **邮箱邮件提醒 (SMTP)**：内置响应式暗黑风 HTML 邮件模板，将包含商品高清图、含税日元价格、归属店铺、分类全路径及官网直达链接的精美图文邮件实时投递至 QQ、163、Gmail 等邮箱。
  - 👾 **Discord Webhook**：实时推送精美 Embed 嵌入式卡片。
  - 💻 **Windows 原生桌面弹窗 (Toast)**：右下角系统级横幅气泡通知，点击直接在浏览器唤起商品详情页。
  - 🔊 **多音阶清脆提示音**：发现新商品时播放专属提示音。

### 6. 现代化 Web 视窗控制看板与实时终端 (Web & SSE Terminal)
- **玻璃拟物深色主题**：极具科技感的 Dark Glassmorphism 界面。
- **商品卡片瀑布流**：高清缩略图、含税价格明细、在库指示灯、发货门店标签（如 SAHRA、中野店等）。
- **实时日志与进度流**：基于 Server-Sent Events (SSE) 实现后台爬虫进度条与运行日志零刷新流式推送。

---

## 🚀 快速启动

### 方式一：双击启动 (推荐)
在项目根目录中，直接双击运行：
```text
run.bat
```
会自动启动本地服务并弹出浏览器访问控制台 (`http://127.0.0.1:5005`)。

---

### 方式二：命令行启动

1. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```

2. **启动 Web 控制看板**：
   ```bash
   python main.py
   ```

3. **单次即时上新检测**：
   ```bash
   python main.py --check
   ```

4. **终端全量分类爬取**：
   ```bash
   python main.py --crawl
   ```

5. **纯终端后台监控模式**：
   ```bash
   python main.py --cli
   ```

---

## 📁 项目目录结构

```text
mandarake/
├── core/
│   ├── categories.py       # 【ドール】全分类树体系管理与解析
│   ├── scraper.py          # Mandarake 列表页与详情页网络抓取与结构解析
│   ├── archive_db.py       # SQLite 数据库管理模块 (WAL 模式，商品表/订单表/进度表)
│   ├── archiver.py         # 后台分类抓取引擎 (多分类分发, 进度流, 暂停/继续/停止)
│   ├── monitor.py          # 定时/定点上新与在库状态监控引擎 (规则过滤, 去重)
│   ├── scheduler.py        # 定时任务调度器 (平缓巡检与定点高频爆发模式)
│   └── terminal.py         # 实时运行日志拦截器 (环形内存缓冲, SSE 广播)
├── hooks/
│   └── auto_order.py       # 登录保持、秒级加购、结算推进与 Dry-Run 安全下单引擎
├── notifiers/
│   ├── base.py             # 通知器基类接口
│   ├── sound.py            # Windows 原生提示音 (winsound 多音阶)
│   ├── desktop.py          # Windows 桌面 Toast 原生弹窗 (点击直达商品页)
│   ├── discord.py          # Discord Webhook 富文本 Embed 推送
│   └── manager.py          # 统一通知调度中心
├── web/
│   ├── app.py              # Flask 后端 REST / SSE API 服务
│   ├── templates/
│   │   └── index.html      # 现代暗黑拟物管理看板主页面
│   └── static/
│       ├── css/
│       │   └── style.css   # 响应式玻璃态样式库
│       └── js/
│           └── app.js      # 前端数据交互、商品网格渲染、分类切换与一键下单逻辑
├── data/
│   ├── doll_categories.json# 【ドール】完整分类代码与名称映射树
│   ├── archive.db          # SQLite 本地归档数据库 (自动生成)
│   ├── session.json        # 登录凭据持久化缓存 (自动生成)
│   └── seen_ids.json       # 已扫描商品去重缓存 (自动生成)
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 服务编排文件
├── .dockerignore           # Docker 忽略文件
├── config.example.json     # 配置文件示例 (初次运行自动生成 config.json)
├── config.json             # 基础配置文件 (敏感配置已加入 .gitignore)
├── requirements.txt        # 依赖包列表
├── run.bat                 # Windows 一键启动脚本
├── main.py                 # 程序主入口
└── README.md               # 本系统详细说明文档
```

---

## 🐳 Linux 云服务器 / Docker 部署指南

### 1. 使用 Dekploy 部署（推荐）

本项目已专门针对 **Dekploy**（基于 Docker + Traefik 的自托管 PaaS）进行了容器与反向代理深度适配：

#### 方案 A：以 Application 方式部署（最便捷，推荐）
1. 打开 Dekploy 控制面板，进入对应 Project，点击 **Create Service -> Application**；
2. **General 配置**：
   - **Provider / Source**: `Git`
   - **Repository URL**: `https://github.com/Yangyuchen11/mandarake-watcher.git`
   - **Branch**: `main`
   - **Build Type**: `Dockerfile`
3. **Network / Port 配置**：
   - **Port**: `5005`
4. **Storage / Persistent Volumes (数据持久化)**：
   - 添加挂载卷：
     - **Container Path (容器路径)**: `/app/data`
     - **Volume Name (卷名)**: `mandarake-data`（或任意自定义名称）
     > 💡 **说明**：系统采用智能配置引擎，所有数据（SQLite 归档库 `archive.db`、去重索引 `seen_ids.json`、登录凭据 `session.json` 以及运行配置 `config.json`）均集中保存在 `/app/data` 中，仅需挂载此单个卷即可保证版本更新、重新部署时不丢失任何数据。
5. **Domains (域名与 HTTPS)**：
   - 在 Domains 中添加您的子域名（如 `mndrk.yourdomain.com`），Dekploy 会通过 Traefik 自动申请并配置 Let's Encrypt SSL 证书；
   - 系统 API 已内置 `X-Accel-Buffering: no` 与 `no-cache` 响应头，Traefik 会直接透传实时 SSE 日志流。
6. 点击 **Deploy** 部署即可！

#### 方案 B：以 Docker Compose 方式部署
1. 在 Dekploy 中点击 **Create Service -> Docker Compose**；
2. 选择 Git 仓库 `https://github.com/Yangyuchen11/mandarake-watcher.git`，直接读取仓库根目录的 `docker-compose.yml`；
3. 点击 **Deploy** 一键构建启动。

---

### 2. 使用原生 Docker Compose 启动

```bash
# 1. 克隆代码仓库
git clone https://github.com/Yangyuchen11/mandarake-watcher.git
cd mandarake-watcher

# 2. 构建并启动后台容器
docker compose up -d --build

# 3. 查看运行日志
docker compose logs -f
```

启动完成后，在浏览器访问 `http://<服务器公网IP>:5005` 即可打开 Web 管理看板。

---

### 3. 使用原生 Docker 命令运行

```bash
# 构建镜像
docker build -t mandarake-watcher:latest .

# 运行容器 (挂载数据卷以持久化数据库与配置)
docker run -d \
  --name mandarake-watcher \
  --restart unless-stopped \
  -p 5005:5005 \
  -v $(pwd)/data:/app/data \
  -e TZ=Asia/Tokyo \
  mandarake-watcher:latest
```

---

## 🛡️ 安全与合规说明
1. **默认开启 Dry-Run 仿真测试模式**：在您确认帐号与支付设置无误前，系统坚决不会向官网提交实际扣款购买，保证您的资金安全。
2. **防封禁请求节流**：内置请求延迟与随机抖动，避免向 Mandarake 服务器发送畸形高频请求触发 429 访问风控。
3. **敏感凭证脱敏保护**：`config.json`、`data/archive.db`、`data/session.json` 均已加入 `.gitignore`，防止任何敏感 Cookie 或通知 Webhook 外泄。
