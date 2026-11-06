# 配置与扩展

## 配置契约

- `site.json` 的 `name`、`domain`、`public_origin`、`dashboard_origin` 同时驱动官网链接、SEO、API Origin 白名单、GEO 站点域名、回调及通知直达链接。主域名必须是裸域名，`public_origin` 必须为匹配的 `https://域名`。
- `languages` 当前支持 `en` / `ar`。添加其他语言需要补充内容、路由和模板翻译，不能只添加代码后声称已完成十语种本地化。
- `brand_terms` / `business_keywords` 用于 GEO 品牌识别与业务词筛选；不含业务数据或访问令牌。
- `theme` 使用合法 CSS 颜色。`logo` / `favicon` 指向 `/assets/` 资源；前端默认使用品牌名称字标。GEO 的 `brand-logo.svg` / `brand-icon.svg` 可替换为自己的通用 SVG。
- `partners` / `suppliers` 默认空列表。只有拿到公开展示授权、资料核对后，才填入真实品牌。可传入名称字符串；品牌图片墙扩展位置在 `site/templates/page.html`。
- `catalog.collections[].id`、`products[].id/category/collection` 使用小写英文、数字、连字符。`id` 生成产品 URL；`category` 配合 hash 筛选；`collection` 必须引用已存在系列。
- `products[].title/summary`、系列名称/描述和页面字段使用 `{ "en": "...", "ar": "..." }`。Badges、规格、指南表格为示例英文采购字段；可按相同字典结构扩展翻译。
- `products[].image` 是产品展示图。建议同一模块统一比例，保留背景与内容安全区。素材由品牌提供；没有客户原图或下载外站图片。
- `products[].stock=true` 的产品出现在 Ready stock。库存标记只是展示开关，不是 ERP 可售数量；正式供货状态必须接自己的库存源。
- `guides[].id` 生成详情 URL，`columns/rows` 生成 HTML 表格，`faq` 同步生成可见问答与 FAQPage，申样自动带入指南标题。
- `redirects.json` 是精确路径映射，保留 query；不带未知路径的通配重定向。改变产品路径时在这里显式配置合并目标。

## 表单与数据链路

```text
官网 / EN / AR
  → POST /api/v1/sample-requests | product-brief | rfqs
  → 服务端字段 / 同意 / Origin / 幂等 / 限流校验
  → 接收库 + 审计 + 通知 outbox 同事务提交
  → 201 RECEIVED / record_id
  → 成功页 + 去重 generate_lead + WhatsApp / 指南
  → 60 秒 worker 只读增量读取接收库 → GEO leads.db
  → Brief / Buyer guide / Ad Source 徽章、筛选、跟进、可选通知
```

首次带广告参数的着陆路径及 UTM/GCLID 使用 sessionStorage 保存。提交时写入结构化 `source_context` 和不超出字数限制的 `[Ad Source: ...]` 行。来源信息被标记为客户端提供的线索，不视为已经核实的广告归因。姓名、邮箱等不加入转化事件。

`sample-requests` 支持紧凑弹窗与完整表单两种字段契约；`product-brief` 是报价入口；`rfqs` 保留完整 RFQ API。不可随意去掉 consent、Origin 或幂等检查。产品系列与 RFQ 品类分别可由 `PRODUCT_BRIEF_SERIES` / `RFQ_CATEGORIES` 配置，启动脚本默认从系列生成。

默认 GA4/GTM 都为空。选择一种接入方式，配置 `analytics.enabled=true` 及对应 ID；浏览器明确同意后才加载外部统计脚本。`dataLayer.generate_lead` 是转化钩子，服务端确认接收后每个回执只发一次；GTM 内仍需按自己的 Ads/GA4 目标配置去重，不能再重复绑定多个成功事件。

## GEO 可复用模块

| 入口 | 保留能力 | 首次新项目状态 |
|---|---|---|
| `/leads` | 询盘导入、跟进、标签筛选、WhatsApp、导出 | 仅示例或新提交数据 |
| `/indexing` | Sitemap 增量同步、HTTP/robots/canonical、人工/Google 证据 | 真实索引未知；示例站为 noindex |
| `/inquiries` | GA4 事件快照导入/同步、CSV 导出 | 示例事件明确标记测试；正式需资源与授权 |
| `/optimize` | 关键词与标题草稿、EN/AR FAQ、审核后 HTML/JSON 导出 | 示例草稿待审核，不自动发布 |
| `/knowledge` | 资料上传、提取、分类、版本、审核/归档 | 空库，附示例 Markdown |
| `/ai` | OpenAI 联网提及/引用证据、费用预览、结果导出 | 未配置密钥，付费执行关闭 |
| `/analytics`、`/search-console` | Google 报告与搜索表现 | 未授权，不制造数据 |
| `/social`、`/social/meta` | 社媒账号、UTM 链接与 Meta 连接 | 无客户账号、无授权 |
| `/google`、`/google/imports` | OAuth 资源设置、CSV/XLSX 导入 | 无 OAuth 凭据；示例资源须替换 |
| `/settings/password` | 验证旧密码、原子更新、旧会话撤销 | 每项目新随机密码，无 12 位下限 |

后台首页及其导航主要为中文运营界面，保留原工作台流程；EN/AR 是对外官网和指南。没有将后台伪装成全部英文/阿语。

默认启动只开 growth-v2 的已交付页面。完整报告流水线和各引擎代码仍在 `geo_monitor/` 内；模拟 engine 标明 mock，不代表真实 AI 推荐。新项目开启外部付费前须选择有效模型并重新核对供应商价格与预算配置，仓库保留的历史规划参数不保证是当日价格。

## 数据边界

全新 `.local/` 内生成自己的凭据与数据库，绝不导入原客户数据库。旧环境升级、覆盖配置前必须做在线备份，不能删除数据库来“解决启动问题”。`init.py` 再次执行不会重置已有 runtime；后台改密后，以持久化的密码 verifier 为准，初始化文件里的旧 bootstrap 密码不再有效。

前端采用 Editorial 留白、深绿/米白/金色变量、静态 HTML 和少量原生 JS。首页模块顺序可在模板调整。性能辅助包括图片尺寸预留、懒加载、rAF 滚动更新、触屏行为分流及 reduced-motion。没有引入客户专属原料商、证书、药企或保密数字。
