# 楼层报修工单系统

> 扫码报修 · 派单流转 · 完工验收 · SLA 计时 —— 一套可以直接跑在内网里的轻量工单系统。

**Python 3 标准库 + SQLite + 原生前端，零第三方依赖，无需 pip / npm / 外网。** 适用于企业办公楼的楼层设施报修：员工扫楼层二维码提单，行政派单，维修师傅接单并回填完工报告，报修人验收确认，全程留痕、可统计、可导出，并可选接入企业微信推送。

---

## 目录

- [功能特性](#功能特性)
- [角色与流程](#角色与流程)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [目录结构](#目录结构)
- [API 概览](#api-概览)
- [运维与自检](#运维与自检)
- [安全须知](#安全须知)
- [文档与许可](#文档与许可)

---

## 功能特性

| 能力 | 说明 |
| --- | --- |
| 零依赖部署 | 后端只用 Python 标准库（`http.server` + `sqlite3`），前端原生 JS/CSS，开箱即跑 |
| 扫码报修 | 按楼层生成二维码海报，扫码直达对应楼层；支持 1–6 张现场照片，前端自动压缩 |
| 四种角色 | 普通员工、处理人（师傅）、行政派单员、超级管理员，按角色隔离数据与操作权限 |
| 工单池抢单 | 处理人可从「待受理」工单池自主抢单；接单前对提单人姓名/部门/电话打码 |
| 完工报告 | 提交完工时必填「处理过程 + 处理结果」，可附最多 3 张完工照片，独立区块展示并进入 CSV 导出 |
| 全流程留痕 | 每次状态变更写入时间轴事件，支持备注、后台代操作与重新打开 |
| SLA 计时 | 按紧急度自动计算响应/完成时限，超时标记；待验收 48 小时无异议自动完成 |
| 消息通知 | 站内消息 + 可选的企业微信应用消息（新单、派单、完工、完成），支持通讯录同步与 OAuth 免登 |
| 管理后台 | 多条件筛选、超时筛选、统计看板、人员管理、系统配置、二维码海报、CSV 导出（24 列） |
| 移动体验 | 响应式页面、PWA manifest、favicon / apple-touch-icon / 桌面图标，可添加到手机主屏 |
| 运维配套 | systemd 一键安装、在线一致性备份（保留 30 份）、全链路自检与完工报告回归测试 |

当前版本 **V1.1**：新增完工报告、旧库字段自动迁移（历史数据不丢失）、网站图标与 PWA 支持。

---

## 角色与流程

| 角色 | 主要操作 |
| --- | --- |
| 普通员工 | 扫码提单、查看自己的工单进度、确认验收、接收通知 |
| 处理人 | 查看工单池、抢单/接单、开始处理、提交完工报告 |
| 行政派单员 | 派单、跟踪超时、代操作、导出工单 |
| 超级管理员 | 人员与角色管理、配置项、企微集成、二维码海报、统计与全部后台能力 |

```mermaid
flowchart LR
    A[员工扫码报修] --> B[待受理]
    B -->|行政派单 / 处理人抢单| C[已派单]
    C --> D[处理中]
    D -->|提交完工报告| E[待验收]
    E -->|报修人确认 / 48h 自动| F[已完成]
    E -->|驳回重开| D
    F --> G[已关闭]
```

**SLA 默认规则**（可在后台配置中调整）：

| 紧急度 | 响应时限 | 完成时限 | 典型场景 |
| --- | ---: | ---: | --- |
| 紧急 | 15 分钟 | 240 分钟 | 影响正常办公 / 存在安全隐患 |
| 一般 | 120 分钟 | 1440 分钟 | 需要处理但不影响工作 |
| 不急 | 240 分钟 | 4320 分钟 | 可安排时间处理 |

工单号格式 `WO + 日期 + 4 位序号`，例如 `WO20261116-0007`。会话默认有效期 30 天。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.9+，`http.server.ThreadingHTTPServer`，手写路由 |
| 存储 | SQLite（`data/tickets.db`）+ 本地文件（`data/uploads/`） |
| 前端 | 原生 HTML / CSS / JavaScript，无构建步骤、无框架 |
| 二维码 | 随项目自带的 `qrcode.min.js`（纯前端生成，不联网） |
| 集成 | 企业微信应用消息 / 通讯录 / OAuth（可选，仅标准库 `urllib`） |
| 部署 | Windows 双击启动脚本；Linux systemd 服务 |

---

## 快速开始

> ⚠️ 仓库中**不包含** `config.json`（内含企业微信密钥与单位信息）。首次运行请先复制示例配置：
>
> ```bash
> cp config.example.json config.json      # Windows: copy config.example.json config.json
> ```
>
> 然后按实际情况修改 `company`、`floors`、`wecom` 等字段。

### Windows（本地试用）

1. 安装 Python 3.9+ 并加入 PATH，无需安装任何依赖。
2. 复制配置文件（`copy config.example.json config.json`）。
3. 双击 `启动工单系统.bat`，或直接运行 `python server.py`。
4. 浏览器访问：
   - 员工端：<http://127.0.0.1:8080/?f=2F>
   - 管理端：<http://127.0.0.1:8080/admin>
5. 初始管理员 `admin / 123456`，**首次登录后请立即修改密码**。

关闭最小化的「WorkOrder Server」窗口即停止服务。端口占用时脚本会自动检测并只打开浏览器。

### Linux（Ubuntu 24.04 内网部署）

```bash
# 1. 放置项目并复制配置
sudo mkdir -p /opt/workorder
sudo cp -r . /opt/workorder/
cd /opt/workorder && sudo cp config.example.json config.json && sudo nano config.json

# 2. 一键安装为 systemd 服务（会设置时区、放行端口、开机自启）
sudo bash deploy/install.sh

# 3. 常用运维
systemctl status workorder
journalctl -u workorder -f
python3 deploy/smoke_test.py http://127.0.0.1:8080
```

安装脚本会输出员工端/管理端地址。二维码请把地址换成服务器实际内网 IP，并在管理端「二维码海报」重新生成 2F–6F 的码后打印张贴。企微推送需要服务器能访问 `qyapi.weixin.qq.com`；不配置企微也完全不影响主流程。

---

## 配置说明

`config.json` 主要字段：

| 字段 | 作用 |
| --- | --- |
| `server.host` / `port` / `base_url` | 监听地址（默认 `0.0.0.0:8080`）与对外基址 |
| `company.*` | 单位名称、系统标题、标语，展示在页面头部与 PWA 名称 |
| `floors` | 楼层列表，决定二维码海报与提单可选楼层 |
| `categories[]` | 报修类型：名称、图标字、颜色、默认紧急度、是否启用 |
| `urgency[]` | 紧急度档位与 `response_min` / `resolve_min` SLA 时限 |
| `sla.auto_close_hours` | 待验收自动完成的小时数（默认 48） |
| `wecom.*` | 企业微信 `corp_id` / `agent_id` / `corp_secret`、通讯录同步、OAuth 免登开关 |
| `auth.*` | 管理员账号、默认密码、会话天数 |
| `ticket_prefix` | 工单号前缀 |

**企业微信是可选能力**：把 `wecom.enabled` 设为 `false`、密钥留空即可离线运行；也可在管理端「系统配置」里在线填写并测试推送。密钥只应保存在服务器本地的 `config.json`，不要写进版本库或部署包外发。

---

## 目录结构

```
.
├── server.py                  # 后端全部逻辑：路由、鉴权、工单状态机、SLA、企微、CSV 导出
├── config.example.json        # 脱敏配置模板（复制为 config.json 后使用）
├── 启动工单系统.bat            # Windows 一键启动
├── static/
│   ├── index.html / app.js    # 员工端：报修、我的工单、详情、通知
│   ├── admin.html / admin.js  # 管理后台：工单、统计、人员、配置、二维码
│   ├── app.css / common.js    # 样式与公共逻辑
│   ├── qrcode.min.js          # 纯前端二维码库
│   ├── logo.png / favicon.ico / icons/ / manifest.webmanifest
├── deploy/
│   ├── install.sh             # Ubuntu systemd 一键部署
│   ├── smoke_test.py          # 部署后全链路自检（29 项）
│   ├── test_finish_report.py  # 完工报告回归测试（隔离临时库，22 项）
│   ├── backup.py              # 在线备份数据库与照片，保留 30 份
│   └── gen_icons.py           # 纯标准库生成 favicon / PWA 图标
├── 本地测试二维码/
│   └── 生成二维码.js           # Node 零依赖生成本地测试用楼层二维码
├── data/                      # 运行时生成（已在 .gitignore 中排除）
└── README.md
```

---

## API 概览

所有接口位于 `/api/`，登录后通过 `X-Token` 或 `Authorization: Bearer` 传递会话令牌。

| 分组 | 代表性接口 |
| --- | --- |
| 配置与认证 | `GET /api/config/public`、`POST /api/auth/login`、`POST /api/auth/logout`、`POST /api/auth/password`、`POST /api/auth/bind`、`GET /api/me` |
| 工单 | `POST /api/tickets`、`GET /api/tickets/mine`、`GET /api/tickets/assigned`、`GET /api/pool`、`GET /api/tickets/{id}`、`GET /api/tickets/{id}/events` |
| 状态流转 | `POST /api/tickets/{id}/claim\|start\|finish\|confirm\|note\|remark` |
| 通知 | `GET /api/notifications`、`POST /api/notifications/read` |
| 管理端 | `GET /api/admin/tickets`、`GET /api/admin/tickets/export`（CSV）、`GET /api/admin/stats`、`/api/admin/users`、`POST /api/admin/tickets/{id}/assign\|close\|reopen`、`GET/PUT /api/admin/config` |
| 企业微信 | `GET /api/wecom/auth_url`、`POST /api/auth/wecom`、`POST /api/admin/wecom/sync`、`POST /api/admin/wecom/test` |
| 静态资源 | `/`、`/admin`、`/static/*`、`/uploads/*` |

---

## 运维与自检

```bash
# 全链路自检：登录、提单、派单、完工报告、验收、CSV、图标资源等 29 项
python3 deploy/smoke_test.py http://127.0.0.1:8080

# 完工报告回归测试：使用临时库 + 随机端口，不触碰真实数据、不占用 8080
python3 deploy/test_finish_report.py

# 在线一致性备份（建议 crontab 每天 02:00），保留最近 30 份
python3 deploy/backup.py

# 忘记管理员密码（Linux 需先停服务）
python3 server.py --reset-admin 你的新密码

# 更换 logo.png 后重新生成全套图标
python3 deploy/gen_icons.py
```

数据库位于 `data/tickets.db`，现场照片位于 `data/uploads/YYYYMMDD/`。备份脚本使用 SQLite 在线备份 API，无需停服；升级旧库时 `init_db()` 会自动补齐完工报告字段，历史数据保留。

---

## 安全须知

- 本仓库为**公开仓库**。`config.json`（企业微信密钥）、`data/`（真实工单与照片）、备份、内网部署包、使用说明书等均已被 `.gitignore` 排除，切勿手工 `git add -f` 提交。
- 初始管理员密码 `123456` 仅用于首次登录，**上线前必须修改**，并按需收紧 `server.host`、防火墙来源与端口。
- 系统面向内网使用，请勿直接暴露到公网；如确需外网访问，应前置反向代理、HTTPS 与访问控制。
- 源码中的示例内网地址与单位名称仅作占位，部署时请按实际环境替换；若密钥曾随文件外发，请及时在企业微信后台轮换。
- 前端页面中的品牌文案与 Logo 属于部署方素材，二次分发前请自行确认授权。

---

## 文档与许可

- 仓库内文档：`README.md`（本文件）、`config.example.json` 注释化的配置项。
- 完整《工单系统使用说明书》面向各角色编写（含状态机、SLA、通知规则、FAQ 与附录），作为内部交付文档单独提供，未随本仓库发布。

本项目为内部业务系统，未附加开源许可证；如需二次分发或商用复用，请先与维护者确认。
