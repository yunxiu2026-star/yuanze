# B2B 建站母版

独立、可运行的 B2B 品牌官网 + 询盘 API + GEO 增长工作台。以 2026-11-05 只读导出的正式应用代码为基础整理；原官网的客户内容改成配置驱动模板，后台业务模块和接收内核保留。生产站没有被修改。

包含 EN / AR 两套桌面和手机页面、产品筛选/计数/搜索、语言切换状态保留、系列悬停切图、七阶段滚动展示、采购指南表格、申样胶囊、报价表单、UTM/GCLID 归因、真实回执、成功页 WhatsApp 与指南入口，以及 GEO 询盘跟进、收录监控、CSV 导出、知识库、AEO 草稿导出和改密功能。

## 先启动本地版

需要 Python 3.12 和 Git。Windows PowerShell，在本仓库根目录执行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe scripts/init.py
.venv/Scripts/python.exe scripts/start.py --seed-demo
```

macOS / Linux 使用 `.venv/bin/python` 替代 `.venv/Scripts/python.exe`。

- 官网：<http://127.0.0.1:9320/>，阿语：<http://127.0.0.1:9320/ar/>
- GEO：<http://127.0.0.1:9322/>；初始随机密码见本机 `.local/access.txt`。
- API：<http://127.0.0.1:9321/api/v1/readiness>。仅监听本机。
- 停止：在启动终端按 `Ctrl+C`，父进程会关闭四个子进程。

`init.py` 不覆盖已有凭据。`--seed-demo` 只增量添加虚构示例：2 条测试询盘、2 篇待审核草稿、带 `is_test=true` 的 GA4 示例事件，以及没有授权令牌的假资源号 `000000000`。不会调用 Google 或付费 AI。首次使用后，默认首页询盘列表筛选为客户；切换到“测试”可看到示例。正式项目不要使用 `--seed-demo`。

本地接收层使用真实 SQLite 事务。成功回执是 `201 + RECEIVED + record_id`，不是假成功 UI；每 60 秒从接收库只读同步到 GEO。生产配置使用 PostgreSQL 18，GEO 仍保留 9 个独立 SQLite 数据库。

## 换一个品牌

1. 编辑 `config/site.json`：名称、域名、联系方式、配色、Logo、合作伙伴、申样选项和统计配置。
2. 编辑 `config/catalog.json`：系列、产品、分类、描述、规格、标签、是否进入 Ready stock，以及图片路径。
3. 将新图片放入 `site/static/`，JSON 中使用 `/assets/文件名`；母版图片为原创 SVG 占位图。
4. 编辑 `config/pages.json` 和 `config/guides.json`：首页、流程、能力、FAQ、4 篇采购指南及 HTML 表格。
5. 运行 `python scripts/build_site.py`，重启 `scripts/start.py`。内容会生成到 `dist/`，不会写往其他网站。

默认示例为 3 个系列、6 个产品、4 篇指南，共 44 个 EN/AR 路由。没有把原客户的 488 页内容、照片、证书、品牌墙、出货量或采购方资料复制进来。保留的是可复用的页面类型、业务逻辑和后台模块。添加产品/指南会自动生成详情、语言链接、canonical 和 Sitemap；移除产品后会清理上次构建的旧 HTML。

默认 `demo=true`、`indexing_enabled=false`，HTML、HTTP 头和 robots 均阻止索引。正式换成真实域名、核准内容并设置 `demo=false` 后，才能显式开启索引。详情见 [定制说明](docs/CUSTOMIZATION.md)。

## 文件目录

| 目录 | 作用 |
|---|---|
| `site/` | Jinja2 模板、通用 CSS / JS、示例图形 |
| `config/` | 品牌、产品、指南、页面及精确重定向配置 |
| `backend/` | 正式询盘接收内核、校验/幂等/审计/通知队列、3 份 SQL 迁移 |
| `geo/geo_monitor/` | FastAPI + Jinja2 工作台、询盘/收录/知识库/内容/数据连接等模块 |
| `examples/` | 纯虚构询盘、草稿、GA4 CSV、知识库文件 |
| `scripts/` | 初始化、构建、启动、同步、备份、数据库准备、部署配置生成 |
| `deploy/` | Nginx 模板及按品牌生成的配置 |
| `tests/` | 真实 HTTP / 数据库 / 浏览器 / 配置替换验证 |
| `docs/` | 定制、部署、模块清单及源码来源记录 |
| `reports/` | 本次验收结果；截图可在交付压缩包和本机报告中查看 |
| `.local/` | **私有运行数据**：密码、数据库、授权令牌、上传文件、日志、备份；不进入 Git / 压缩包 |

## 验证与备份

保持本地服务运行，另开终端：

```powershell
.venv/Scripts/python.exe tests/integration.py
.venv/Scripts/python.exe tests/configuration.py
.venv/Scripts/python.exe scripts/backup.py
```

浏览器验证需额外安装：`pip install -r requirements-dev.txt`、`python -m playwright install chromium`，然后执行 `python tests/browser.py`。可用 `B2B_BROWSER` 指定已安装的 Chromium / Chrome。

SQLite 备份采用在线 backup API 并做完整性检查，位于 `.local/backups/`。每个数据库内部一致；多数据库备份不声称跨库同一时点事务。跨库恢复时先停接收/同步，备份原目录，再将快照恢复到全新的数据目录。PostgreSQL 另用 `pg_dump`，见部署说明。

## 部署与外部服务

Docker / Compose、PostgreSQL 18 权限分离、Nginx HTTPS / 精确 301 的步骤见 [部署说明](docs/DEPLOYMENT.md)。本次电脑没有 Docker 和 Nginx，**容器启动、PostgreSQL 迁移和 `nginx -t` 尚未在本机执行**，需要在新项目的隔离环境验收后发布。原生本地版本已实际启动并验证。

Google / Meta 授权、真实 GA4、AI 联网检测、Webhook、SMTP 都保留配置入口，默认没有客户密钥、授权令牌或自动付费任务。Google 收录状态与 HTTP 可访问性分别记录；不会把“HTTP 200”当成“Google 已收录”。AEO 导出只允许已审核草稿，生成 HTML + FAQ Schema 片段，不会直接修改主站文件。

本仓库未推送到任何远程 Git 服务，也不会自动连接或发布至原客户服务器。用 `git remote add origin <你的新仓库地址>` 绑定新项目。源码包内另附 `git-history.bundle`，可用 `git clone git-history.bundle 新目录` 恢复完整 Git 历史；直接解压源码使用时可自行 `git init -b main`。
