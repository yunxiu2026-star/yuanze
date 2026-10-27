# 云岫建站模版

以 [yuanzebeauty.com](https://yuanzebeauty.com) 为第一个实例的企业官网模版。**每个客户站点只是一份配置文件 + 一个素材文件夹**，页面结构、样式和交互由模版统一提供，构建出纯静态 HTML，可以部署到任何静态托管（GitHub Pages、Cloudflare Pages、Netlify、阿里云 OSS、宝塔等）。

- 零依赖：只需要 Node.js 18 以上，不用 `npm install`
- 多站点：`sites/` 下一个文件夹就是一个站
- 换品牌只改配置：名称、文案、颜色、字体、联系方式、导航、页面区块全部在 `site.config.json`

## 目录结构

```
sites/
  yuanze/                 元泽官网（第一个实例，可作为同行业客户的起点）
    site.config.json
    assets/               该站的 Logo、图片、favicon
  _starter/               空白起步模版，所有待填内容标注为【请填写：…】
src/
  layout.mjs              页面骨架：<head>/SEO、页眉导航、页脚
  sections/               页面区块，每种 type 一个文件
  assets/site.css         公共样式（颜色/字体/圆角全部用 CSS 变量）
  assets/site.js          移动端菜单、暂停动效
scripts/
  build.mjs               构建 sites/<名>/ → dist/<名>/
  serve.mjs               本地预览（刷新即重新构建）
  new-site.mjs            新建站点
```

## 新建一个客户站点

```bash
# 1. 从空白模版复制（或 --from yuanze 基于元泽站改写，适合同行业客户）
npm run new -- acme
npm run new -- acme --from yuanze

# 2. 编辑 sites/acme/site.config.json，把 Logo、图片放进 sites/acme/assets/

# 3. 本地预览 http://localhost:4321
npm run dev -- acme

# 4. 构建并检查是否还有【请填写】未替换，有则报错列出
npm run build -- acme --check
```

`dist/acme/` 就是完整的网站，直接上传到托管服务即可。如果配置了 `site.domain`，会自动生成 `CNAME`；配置了 `site.baseUrl` 会生成 `sitemap.xml` 和 `robots.txt`。

构建所有站点：`npm run build -- --all`（跳过 `_` 开头的模版目录）。

## 配置说明（site.config.json）

| 字段 | 作用 |
| --- | --- |
| `site` | 域名、网址、语言、SEO 标题/描述、分享图、favicon、语言切换列表 |
| `brand` | 品牌名、公司全称、标语、Logo（留空则显示文字 Logo）、版权署名 |
| `theme.colors` | 主色 `primary`、主色上的文字色、点缀色 `accent`、正文色、次要文字色、背景、浅底、边框 |
| `theme.fonts` | 标题字体、正文字体、Google Fonts 链接（国内站建议留空，用系统字体） |
| `contact` | 邮箱、电话、WhatsApp、微信、地址、营业时间；`*Label` 字段可改显示名 |
| `social` | 社交媒体链接 |
| `nav` / `navCta` | 顶部导航（支持一级下拉 `children`）和右上角按钮 |
| `footer` | 页脚链接分组、法律链接、底部说明 |
| `analytics.gaId` | Google Analytics ID，留空不加载 |
| `pages` | 页面列表；每页 `path` + 可选 `title`/`description` + `sections` 区块数组 |

### 可用区块（sections 的 type）

每个区块都可以加 `id`（用于导航锚点）、`eyebrow`（小标题）、`title`、`intro`、`background`（`"alt"` 浅底 / `"dark"` 主色底）。

| type | 用途 | 主要字段 |
| --- | --- | --- |
| `hero` | 首屏 | `title` `subtitle` `image` `buttons[]` `badges[]` |
| `facts` | 卖点卡片 / 数字统计 / 流程路径 | `items[{value,label,text,href?,linkLabel?,unit?}]`；`variant: "path"` 为深色底金线串联的流程（适合起订 → 打样 → 生产 → 品控），`"stats"` 为大数字条 |
| `categories` | 产品系列与品类 | `groups[{title,subtitle,image,items[]}]` |
| `split` | 图文分栏 | `text` `bullets[]` `image` `reverse` `buttons[]` |
| `logos` | 客户/供应商 Logo 滚动墙 | `items[{name,image}]`，无图时显示名称 |
| `markets` | 市场与资质 | `items[{name,tags[]}]` `image` |
| `gallery` | 图片卡片墙 | `items[{title,text,image}]` `note` |
| `steps` | 流程步骤（自动编号） | `items[{title,text,image}]` |
| `cards` | 通用卡片（服务模式等） | `items[{eyebrow,title,text,href}]` |
| `faq` | 常见问题折叠 | `items[{q,a}]` |
| `cta` | 收尾号召条 | `title` `text` `buttons[]` |
| `contact` | 联系方式 + 询盘表单 | `form.action`（表单服务地址，留空则用邮件发送）`form.fields[]` |
| `richtext` | 隐私政策等纯文字页 | `html` |

区块可以任意增删、调整顺序、重复使用。图片字段留空时会显示带说明文字的占位块，方便先搭结构后补图。

### 询盘表单

`form.action` 留空时，表单通过访客的邮件客户端发到 `contact.email`。正式上线建议接入表单服务（如 Formspree、Getform，或自己的接口），把地址填入 `form.action`。

## 扩展模版

- **新增区块类型**：在 `src/sections/` 写一个 `export default function (section, site) { return html }`，在 `src/sections/index.mjs` 登记。所有配置文本输出前用 `esc()` 转义。
- **改全局样式**：改 `src/assets/site.css`，所有站点同时生效；只想改某个站，用该站的 `theme`。
- **多语言**：每种语言建一个站点目录（如 `yuanze-es`），在各自 `site.languages` 里互相链接，部署到子目录或子域名。

## 元泽站（sites/yuanze）待补内容

模版里的元泽配置根据线上页面结构整理，以下内容需要从原站素材补齐：

- Logo、首屏图、各区块配图、资质徽章图片、客户/供应商 Logo（放入 `sites/yuanze/assets/` 并填写路径）
- 品牌主色、点缀色（目前为暂定的墨绿 + 金色，需要按原站确认）
- LinkedIn / Instagram / Facebook 主页链接（填入 `social`）
- 完整的客户品牌列表（线上约 36 个，配置中列了 6 个）和供应商列表（线上约 24 个）
- 其余 9 种语言版本
- 隐私政策、使用条款正文
