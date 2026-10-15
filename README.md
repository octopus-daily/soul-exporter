# soul-exporter

> Soul App（网页版 `home.soulapp.cn`）**瞬间与媒体一键导出工具**。
>
> **为什么会有这个项目**：截至 2026-10，全网**没有可用的 Soul 内容导出工具**。
> GitHub 上 37 个 `soulapp` 相关仓库，全是 2017–2021 年的仿站、数据分析、旧版破解，没有一个能导出当前版本的数据。
> 这个仓库从零开始：**逆向网页版接口 → 导出全量瞬间 → 批量下载图片视频 → 整理成可浏览的归档**。

## 特性

- **一键导出**：浏览器控制台脚本，登录态下直接拉全量瞬间（含正文、图片、视频、互动数、话题标签）
- **无签名门槛**：Soul 网页版接口是**同源 + 纯 cookie 鉴权 + 无签名**，不需要逆向签名算法
- **批量下载**：图片/视频直链批量落盘，**断点续传**（中断了直接重跑）
- **自动整理**：按年份归档、生成缩略图、生成**离线可浏览的 HTML 页面**
- **不碰凭据**：脚本在你自己的浏览器里跑，**不读取、不上传任何 cookie**

## 实测成果（一次真实运行的规模）

| 项 | 数量 |
|---|---|
| 瞬间 | 485 条 |
| 视频 | 44 条 |
| 媒体直链 | 1,784 个 |
| 下载文件 | 1,087 个 / **1.2 GB** |
| 缩略图 | 1,058 个 / 24 MB |

## 快速开始

### 第 1 步：导出 JSON

1. 浏览器登录 `https://home.soulapp.cn/`，进**「我的」**页面；
2. **F12 → Console**；
3. 把 `tools/soul_export.js` 全文粘贴进去，回车；
4. 跑完自动下载 `soul_export.json`。

脚本顶部有开关：

| 开关 | 默认 | 作用 |
|---|---|---|
| `WITH_DETAIL` | `false` | 逐条再拉详情（字段更全，485 条约多花 6~7 分钟） |
| `WITH_VIDEO` | `true` | 额外导出「视频」标签页 |
| `SLEEP_MS` | `800` | 每页间隔，**别调太小**（有被限频的现实风险） |

### 第 2 步：批量下载媒体

```bash
python tools/soul_media_download.py --json soul_export.json --out soul-media
```

- `--workers 4` 并发数 ｜ `--dry-run` 只看要下什么 ｜ `--estimate` 预估体积
- **断点续传**：已下好的自动跳过
- ⚠️ m3u8（HLS 流）本脚本不处理，遇到会提示，需 ffmpeg 另行下载

### 第 3 步：整理成归档

```bash
python tools/soul_organize.py --root .            # 按年份归档 + 缩略图 + 离线浏览页
python tools/soul_verify_organize.py --root .     # 核验
python tools/soul_analyze.py --root .             # 盘点数据分布
```

产物结构：

```
soul/
├── soul_export.json          # 原始导出数据
├── 瞬间/<年>/<序号_日期_标题>/
│   ├── 正文.md
│   └── *.jpg / *.mp4
├── _缩略图/                   # 浏览页用的小图
├── soul浏览.html             # 离线可浏览（双击即开，含搜索）
└── soul索引.md               # Markdown 索引
```

## 接口逆向说明

完整的方法论、接口清单、鉴权细节、分页规则见 **[docs/接口逆向说明.md](docs/接口逆向说明.md)**。

**方法论摘录**（可复用到任何 SPA 平台）：

1. **确认形态**：先判断有没有本地数据库可扒 —— Soul 网页版是 Vite 打包的 Vue 3 SPA，数据全在服务端；
2. **从打包产物挖接口**：拉 `index-*.js` 分片，grep `/api/` `/pc/` 关键词，得到完整接口清单；
3. **无凭据探测**：不带 cookie 请求接口 —— 返回 `20001 权限验证失败` 说明**接口存在、只差登录**；404 说明路径不对；
4. **照搬前端逻辑**：分页参数直接从打包代码里读（Soul 用 `lastIdEcpt` + `firstStartTime` 游标，不是页码）。

### 关键接口

| 接口 | 作用 |
|---|---|
| `GET /pc/profile/content/posts` | **瞬间列表（导出主力）** |
| `GET /pc/profile/content/tabs` | 内容分类标签 |
| `GET /pc/profile/content/byType` | 按类型取（如视频） |
| `GET /pc/v3/post/detail` | 单条详情 |
| `GET /pc/profile/info` / `metrics` | 资料 / 计数 |

### 鉴权与请求头

- **纯 cookie**，无 `Authorization`、无签名参数（`credentials: 'include'` 即可）
- 必须带的固定头：`os: PC`、`app-id: 10000010`、`app-version/version: 1.0.0`、`channel-id: official`、`device-id: <localStorage.soul_device_id>`
- **返回码约定**：`10001` = 成功 ｜ `20001` = 登录失效 ｜ `10002` = 服务端临时异常

## 已知边界与风险

| 项 | 说明 |
|---|---|
| **只导自己的数据** | 换 `targetUserIdEcpt` 拉别人内容既可能被风控，也涉及他人隐私，**不做** |
| **不含私聊** | 聊天记录涉及对话另一方隐私，**本项目明确不做**，文档里也不列相关接口 |
| **接口会变** | 前端 bundle 带哈希，Soul 一发版可能全变；失效时重拉首页 HTML，找新的 `/assets/index-*.js` 重新提路径 |
| **礼物/消费记录** | 网页版可能不提供，需手机端侧 |
| **易盾验证码** | 只用于登录环节，本方案不涉及登录，无影响 |

## 目录

```
soul-exporter/
├── README.md
├── LICENSE
├── requirements.txt
├── docs/
│   └── 接口逆向说明.md      # 接口清单 + 鉴权 + 分页规则 + 方法学
└── tools/
    ├── soul_export.js            # 浏览器控制台导出脚本（第 1 步）
    ├── soul_media_download.py    # 媒体批量下载（第 2 步）
    ├── soul_organize.py          # 整理归档 + 生成浏览页（第 3 步）
    ├── soul_verify_organize.py   # 核验
    └── soul_analyze.py           # 数据盘点
```

## License

MIT。**仅供个人备份自己的数据使用。**
