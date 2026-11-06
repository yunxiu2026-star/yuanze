# 新项目部署与维护

这套配置只针对新项目，不包含原客户 SSH 地址、证书、域名映射、生产路径或发布指针。先在自己的隔离环境验证；不会自动执行上线。

## Docker 本地启动

要求 Docker Compose 支持长格式 `env_file.required=false`。先完成 README 的 `scripts/init.py`，其私有 `.local/runtime.json` 必须可被容器 UID 10001 只读访问，且不能放进镜像。Windows / Docker Desktop 可通过只读 bind mount 使用；Linux 请用专用组/ACL 授予服务 UID 读取权限，并限制其他用户读取。

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=40 api geo sync
```

端口 9320 / 9321 / 9322 只绑定 127.0.0.1。运行前停止同端口原生实例。数据卷是 `api-state` 与 `geo-state`，同步容器对接收库只有只读挂载；静态站容器不挂载业务库。示例不默认灌入 Docker 实例，数据为空也是正常状态。

Docker 内与宿主机 Python 的 `.local/data/` 是两套不同数据位置。需要演示数据可在自己的临时实例执行 `docker compose exec geo python scripts/prepare_data.py --seed-demo`；不要在正式项目执行。停容器用 `docker compose down`，不要加 `-v`，避免删除数据卷。

## 正式 PostgreSQL + HTTPS

1. 修改 `config/site.json` 为新品牌的真实 HTTPS 域名及后台域名，审核公司资料、产品、政策与参数。关闭 `demo` 后再决定是否开启索引；构建并验收。
2. 从 `.env.example` 复制成 `.env.production`，填写**新生成**且不相同的 `POSTGRES_PASSWORD` / `APP_DB_PASSWORD`。不要提交这个文件。
3. 运行初始化，保管 `.local/runtime.json`。正式模式会要求安全 Cookie、准确 Origin / OAuth 回调、独立数据库名/Schema 与明确的公开接收开关。
4. 检查 `172.30.73.0/24` 是否与本机已有网络冲突；如调整网段，同时调整 `TRUSTED_PROXY_IPS` 为实际 Nginx → API 的受信任网关地址。该 IP 必须是你的可信代理，不能填写任意来源网段。若使用远程数据库，配置证书与 `verify-full`；示例 disable 仅限私有本机 Docker PostgreSQL 链路。
5. 构建镜像、先启动 PostgreSQL、初始化新库，再启动其余服务：

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml build
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml up -d postgres
# 等 pg_isready 健康后；不会删除已有数据，迁移校验和冲突时停止
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml run --rm --no-deps api python scripts/provision_postgres.py
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml up -d
```

应用角色没有超级用户、建库、建角色、Schema CREATE、记录 DELETE、迁移账本 UPDATE 权限。迁移角色无登录权限，仅由数据库管理员 `SET ROLE` 执行已有 3 份 SQL；迁移校验和和 PostgreSQL 18 检查保留。生产 API 用 Gunicorn，GEO 用 Uvicorn，同步 worker 每 60 秒运行并持有互斥锁。

6. 宿主机放置本项目构建产物（示例 `/srv/b2b/current/`），生成 Nginx 配置：

```bash
python scripts/build_site.py
python scripts/render_deploy.py
# 在新服务器安装 TLS 证书后，将 deploy/generated/site.conf 引入 nginx http {}
nginx -t
# 仅在新环境审核、测试通过后，由操作员执行
systemctl reload nginx
```

Nginx 静态读取 `dist/`，公开只代理 3 个 POST 询盘入口到 127.0.0.1:9321；内部管理 API 不对公网放行。客户端地址由 Nginx 覆盖传给后端，禁止信任任意传入 X-Forwarded-For；Nginx 增加每 IP 接收限流。目录斜杠使用 Nginx 自身行为，精确历史路径 301 从 redirects.json 生成并保留 query。

7. 验收 `/`、`/products/`、`/ready-stock/`、`/resources/`、`/get-a-quote/`、`/ar/`、Sitemap；验证 API readiness、真实测试询盘、成功页去重事件、GEO 登录/标签/导出/Sitemap/改密，以及数据库卷和同步日志。GEO `/healthz` 是进程健康，`/readyz` 另外用于就绪检查。

本次宿主机未安装 Docker、PostgreSQL 或 Nginx，因此这里的容器/迁移/配置只能提供源码与静态检查结果，不能当成已完成目标服务器验收。

## 通知与数据连接

- `.env.production` 的 `WEBSITE_NOTIFY_WEBHOOK_URL` 支持通用 JSON / 企业微信 / 飞书 / 钉钉，空值静默跳过；真实新询盘入库与提醒入队在同一事务，提交后异步发送，失败退避重试。测试夹具不发通知。
- `WEBSITE_NOTIFY_SMTP_*` 可替代或并行通知。已有 API 邮件通知 worker 仍保留在 `backend/notification_worker.py`，如启用请另配它要求的 SMTP 环境，不要与 GEO 对同一人重复提醒；不配置就不启动该 worker。主接收库 QUEUED 不是已发送证明。
- 在 GEO `/google` 按页面说明添加自己的 OAuth client 与资源；回调固定为自己的 `https://后台域名/google/callback`。Meta 同样在自己的应用登记回调。私有 JSON 文件和授权令牌只保存在数据卷私有目录。
- `WEB_AI_PAID_ENABLED` 默认 false。只有明确设置为 true 并由管理员在页面预览问题与费用、确认执行，才进入联网检测流程；不自动定时付费。多引擎 CLI 也需单独指定引擎、模型和密钥。

## 备份、升级与回退

- SQLite：`python scripts/backup.py`。为避免备份包含自身，备份输出不能位于数据源目录中；Docker 请另挂一个 `/backup` 卷并输出 `--output /backup/时间戳`。备份容器必须挂载需备份的 API / GEO 数据卷；仅挂载 GEO 卷的容器不能备份 API 库。备份会另存 runtime 配置与存在时的私有环境文件，必须按敏感资料保护。
- PostgreSQL：`pg_dump -Fc -h <host> -U <backup-role> -d b2b_production -f <private-backup.dump>`；使用私有 pgpass 或密钥管理，不能把密码写入 Git 或公开命令日志。
- 保管数据库、上传文件、runtime secret 和密码 verifier；数据库快照本身不能恢复丢失的加密密钥。
- 发布新镜像前保存旧镜像 ID、配置及数据快照。切换代码不删除卷。SQL 迁移只向前；若需要恢复数据库，应先停服务，将备份恢复到新的数据库/卷并核验，再切换服务，不能直接覆盖正在使用的文件。

当前仓库没有绑定远程仓库或自动部署密钥。新增 CI/CD 时使用项目专属凭据和隔离环境。
