# XHS Fitness Ops

一个本地优先、人工触发的小红书健身内容运营工作台。它把账号数据、外部样本、Golden Pattern、LLM 选题与写稿、三套封面方向、5–7 页图文排版以及可编辑 Canva PPTX 串成一条可审查的生产链路。

> 本仓库不是无人值守发布机器人。平台读取、Golden revalidation 和内容生成都需要运营者主动触发；点赞、收藏、关注、评论、私信和自动正式发布保持关闭。遇到登录验证、限流或账号安全提示时应停止当前批次，并在官方客户端人工处理。

## 能做什么

- 读取本人创作者中心可见数据，并区分账号总览与逐篇指标。
- 搜索外部平台样本，经过人工 Golden 门禁后建立 Feature、Golden Sample 和 Golden Pattern。
- 用户点击时重新检查 Golden Sample 热度，保留 `golden_score`、`transfer_score` 和 `revalidation_score` 三个独立信号。
- 用固定的 35/30/30/5 公式融合账号、平台、Golden 和新颖度证据，只有完整链路成功后才提交正式 Topic Score。
- 通过 OpenAI-compatible Base URL 或本机 Codex CLI 完成 Topic、Content、Outline 和 Cover 决策。
- 输出 18–36 个知识单元、三套差异化封面方向和 5–7 页模块化信息图。
- 图片模型只生成无字插画；本地渲染器负责中文、卡片、箭头、页码和合规提示。
- 输出 JPG 预览、原始无字素材和分层可编辑 PPTX，可人工上传普通 Canva。
- 可选使用 Canva Connect API 自动导入，但它不是运行主链路的前提。
- 用本地个人知识库学习语气和论证方式，不把写作样本当作健身事实或实时热点。

## 安全边界

默认运行模式为：

```text
XHS_OPERATION_MODE=manual_connected
```

允许的连接操作包括运营者主动点击的数据读取、搜索、详情补全和 Golden revalidation。禁止的平台操作由 `compliance/operation_policy.py` 阻断。发布前还必须由运营者检查事实、素材许可、AI 标识要求和平台当时有效的官方规则。

`AGENTS.md` 中的部分平台风险项来自运营者提供的风险线索，不等于已经核验的官方固定阈值或处罚规则。对外引用前必须补充官方来源、生效日期和核验日期。

## 工作流

```text
A1/A2 本人账号数据 + B1 外部样本
  → 人工选择 Golden Candidate
  → B3 Feature / Golden Sample / Golden Pattern
  → 用户点击 revalidation + Pattern 重建
  → C 融合并提交正式 Topic Score
  → Topic Agent adopt / hold / skip
  → Content Agent + 18–36 个知识单元
  → Outline / Cover Agent
  → 三套封面 + 5–7 页信息图
  → JPG + 分层可编辑 PPTX
  → Canva 人工修改
  → 官方客户端人工审核与发布
```

更详细的模块关系见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 环境要求

### 本地看板与排版必需

- macOS 或 Windows
- Python 3.10 或更高版本
- 首次安装 Python 依赖时需要联网
- 现代浏览器

### 小红书实时连接功能需要

- Node.js 20 或更高版本，推荐在所有电脑上固定同一主版本
- OpenCLI 本地程序
- Chrome/Chromium 中启用 OpenCLI Browser Bridge
- Chrome 对应 Profile 已登录：
  - `www.xiaohongshu.com`
  - `creator.xiaohongshu.com`

OpenCLI 没有安装时，看板、本地写稿、排版、测试和手动 Canva 上传仍然可用；平台实时采集不可用。

### LLM 与图片生成至少选择一种路线

1. OpenAI-compatible Base URL：适合 Topic、Golden 和 Content 文本任务。
2. 本机 Codex CLI：可处理结构化文本角色；登录和网络可用时还能执行图片阶段。
3. 混合路线：Base URL 负责 Topic/Golden/Content，Codex CLI 负责 Outline/Cover/Image。

如果只配置文本 Base URL，仍能生成本地信息图页面；未生成插画的槽位会使用本地兼容视觉。

## 安装与启动

### macOS

克隆或下载仓库后进入项目目录：

```bash
bash setup_environment.sh
bash start_dashboard.sh
```

也可以双击：

```text
start_dashboard.command
```

看板地址：

```text
http://127.0.0.1:5000
```

如果需要小红书连接功能，再检查：

```bash
node --version
command -v opencli
opencli --version
opencli doctor
opencli list
opencli profile list
opencli xiaohongshu --help
```

使用 NVM 时，全局 npm 包跟随 Node 版本。切换 Node 后找不到 OpenCLI，需要在当前 Node 版本重新安装或把正确版本设为 default。

### Windows

双击：

```text
start_dashboard.bat
```

或在项目目录运行：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

## 配置模型

复制 `.env.example` 中需要的变量到你自己的终端环境。不要创建含真实密钥并会被 Git 追踪的配置文件。

### Base URL 文本路线

```bash
export XHS_LLM_PROVIDER="http"
export XHS_LLM_BASE_URL="https://provider.example/v1"
export XHS_LLM_API_KEY="your-key"
export XHS_LLM_MODEL="your-model"
```

看板也可以临时填写这些信息。API Key 只保存在当前浏览器标签的 `sessionStorage`，关闭标签后需要重新填写；不要在共享电脑上使用真实密钥。

### 统一 Codex CLI

```bash
codex login status
export XHS_LLM_PROVIDER="codex_cli"
```

默认推理深度：Topic/Golden/Content 为 high，Outline/Cover 为 medium，Image 为 low。各角色可分别覆盖：

```text
XHS_TOPIC_PROVIDER
XHS_GOLDEN_PROVIDER
XHS_CONTENT_PROVIDER
XHS_OUTLINE_PROVIDER
XHS_COVER_PROVIDER
XHS_IMAGE_PROVIDER
```

### 混合路线

```bash
export XHS_LLM_PROVIDER="http"
export XHS_LLM_BASE_URL="https://provider.example/v1"
export XHS_LLM_API_KEY="your-key"
export XHS_LLM_MODEL="your-text-model"
export XHS_OUTLINE_PROVIDER="codex_cli"
export XHS_COVER_PROVIDER="codex_cli"
export XHS_IMAGE_PROVIDER="codex_cli"
```

## 首次使用顺序

1. 修改 `config/persona.yaml`：账号名称、定位、受众、语气和内容比例。
2. 如不做健康保障内容，保持 `insurance_soft: 0.00` 和 `config/insurance.yaml` 中 `enabled: false`。
3. 启动看板，确认 `/api/operation/status` 显示 `manual_connected`。
4. 需要连接数据时，先运行 `opencli doctor`，再在相同 Chrome Profile 登录两个小红书域名。
5. 点击 A1/A2 获取本人账号证据。
6. 点击 B1 获取外部样本，人工选择至少一个 Golden Candidate。
7. 点击 B3，等待 Feature、Golden Sample 和 Golden Pattern 都建立完成。
8. 需要观察增长趋势时，再主动点击 Golden revalidation。
9. 完整证据通过后进入 C 融合并提交 Topic Score。
10. 配置文本/图片后端，生成文案、三套封面和图文包。
11. 人工检查后把可编辑 PPTX 上传 Canva。

公开种子 `config/topic_weights.example.json` 的 `score_commit.status` 是 `uncommitted`。首次读取时会复制到已被 Git 忽略的 `data/topic_weights.json`；之后的账号评分和 Pattern ID 不会修改公开种子。在没有真实账号、平台和 Golden 证据前，系统拒绝伪造正式推荐分，这是预期行为。

## Golden Engine 说明

- Golden Candidate：待补全、待分析的候选帖子。
- Golden Feature：从候选帖子提取的结构化特征。
- Golden Sample：通过准入门禁并保留来源证据的样本。
- Golden Pattern：多个有效样本聚合出的可迁移模式。
- Golden Pool：保留正文、URL、指标、特征、分数和原始来源的样本层，不只是 Feature 列表。
- Revalidation：只有用户点击时才读取最新可见指标，计算热度变化并更新 Pattern 有效权重。
- Transfer feedback：发布后有可归因指标时，用来判断某个模式是否适合自己的账号。

Revalidation 不应覆盖 `golden_score` 或 `transfer_score`；三个字段保持独立，Pattern 层再组合使用。

## 个人语气知识库

只导入自己拥有或已获授权的材料：

```bash
.venv/bin/python -m personal_ops.import_writing_samples "/path/to/owned-article.docx"
```

规范化文本写入本地 `data/personal_knowledge/`，该目录被 Git 忽略。写作样本默认只用于：

- 语气与句式节奏
- 论证顺序
- 证据解释方式
- 对限制和个体差异的表达

它们不能自动成为健身事实、个人健身经历或平台趋势证据。

## 生成内容与图文包

完整命令行链路：

```bash
bash run_daily.sh --generate-images --max-image-assets 6
```

只从现有草稿生成：

```bash
.venv/bin/python content/carousel/generate_carousel.py \
  --draft content/drafts/<draft>.json \
  --generate-images \
  --max-image-assets 6
```

生成目录：

```text
content/canva_packages/<post_id>/
├── page_01.jpg ...
├── source_assets/
├── manifest.json
├── editable_text_layers.json
├── <post_id>_Canva可编辑.pptx
└── CANVA_IMPORT.md
```

## Canva

### 推荐默认：人工上传

国内版或国际版 Canva 都可以先尝试人工导入：

1. 打开 Canva。
2. 选择“导入文件”。
3. 上传 `<post_id>_Canva可编辑.pptx`。
4. 检查页数、中文字体、换行、图片裁切和图层可编辑性。
5. 人工调整并导出。

不要只上传成品 JPG，否则整页会变成不可编辑的扁平图片。

### 可选：Canva Connect API

国际版 Canva Connect API 已提供可选实现，但不配置也不会影响主流程：

```bash
.venv/bin/python -m canva_connect configure --client-id "<client-id>"
.venv/bin/python -m canva_connect login
.venv/bin/python -m canva_connect status
.venv/bin/python -m canva_connect upload --post-id <post_id> --open
```

详情见 [docs/CANVA_CONNECT_API.md](docs/CANVA_CONNECT_API.md)。Client Secret 和 OAuth Token 只允许进入操作系统凭证库，不能进入仓库。

## 测试

运行全部测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

启动冒烟检查：

```bash
.venv/bin/python app.py
```

浏览器打开 `http://127.0.0.1:5000`，确认看板能够加载。仓库内的 GitHub Actions 会在 Python 3.11 和 3.13 上运行同一组测试。

## 常见问题

### `No such file or directory: opencli`

看板仍可启动，但实时采集不可用。确认 OpenCLI 安装在当前 Node/NVM 版本，并运行 `opencli doctor`。

### B3 提示 Golden Feature/Sample/Pattern 未完整建立

常见原因是没有人工选 Golden、候选详情补全失败、LLM 后端不可用，或准入后有效样本不足。依次检查 B1 人工选择、OpenCLI 登录、模型配置和 B3 任务错误详情。

### Codex CLI 已登录但请求超时

`codex login status` 只证明本机存在登录状态，不证明网络调用一定成功。先运行一个最小 `codex exec` 测试；文本阶段可临时切换 Base URL，图片阶段保留本地无图排版降级。

### Playwright 浏览器不可用

HTML 渲染失败时项目会回退 Pillow。若操作系统不受当前 Chromium 构建支持，不要反复安装不兼容浏览器；优先使用兼容 Python/Playwright 组合或本地 Pillow 输出。

### Canva 导入后字体或位置变化

优先使用系统和 Canva 都有的字体，减少超长中文行；上传后必须人工检查。PPTX 是可编辑交接格式，不保证 Canva 对每个字体和布局实现像素级一致。

## 数据与隐私

以下内容只属于本地运行时数据，默认全部被 `.gitignore` 排除：

- `data/*.db`
- `data/raw/`
- `data/personal_knowledge/`
- `data/daily_runs/`
- `content/drafts/`
- `content/covers/`
- `content/post_pages/`
- `content/canva_packages/`
- `.venv/`、编辑器配置、缓存和日志

公开仓库前按照 [docs/GITHUB_RELEASE.md](docs/GITHUB_RELEASE.md) 执行检查。不要把账号后台快照、个人文章、草稿、图片、API Key、OAuth Token、Cookie、浏览器 Profile 或临时 Canva 编辑链接提交到 Git。

## 贡献与许可证

贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。

本发布副本暂未附带开源许可证，因为许可证决定其他人能否复制、修改和分发代码。公开发布前，仓库所有者必须明确选择 MIT、Apache-2.0、GPL、专有授权或保持默认版权；不要在不了解授权后果时随意添加许可证。
