# Codex CLI → Canva 图文帖工作流

这条链路支持两种运行方式：统一 Codex CLI，或 DeepSeek/Base URL 负责 Topic/Content、Codex CLI 负责 Outline/Cover/Image。所有 Agent 读取相同的 Golden Pattern/revalidation 证据；本地模块化信息图渲染器负责中文排版，普通 Canva 只做最终人工编辑。

## 运行前

- 本机已完成 `codex login`；可用 `codex login status` 检查。
- 使用已登录的 Codex CLI 时，项目中不需要另填 OpenAI API Key、Recraft 账号或 Canva Pro。
- 需要 Python 的 Pillow、python-pptx 和 keyring 依赖（项目 `requirements.txt` 已列出）。

## 完整每日生成

```bash
export XHS_LLM_PROVIDER="codex_cli"
bash run_daily.sh --generate-images --max-image-assets 6
```

文本调用通过 `codex exec --output-schema` 返回结构化 JSON，并以只读沙箱运行。默认推理深度：Topic/Golden/Content 高，Outline/Cover 中；图片 worker 低。

推荐混合路由：

```bash
export XHS_LLM_PROVIDER="http"
export XHS_LLM_BASE_URL="你的兼容接口"
export XHS_LLM_API_KEY="你的密钥"
export XHS_LLM_MODEL="你的文本模型"
export XHS_CODEX_MODEL=""  # 留空使用本机 Codex 默认模型
bash run_daily.sh --generate-images --max-image-assets 6
```

HTTP 模式默认将 Topic/Golden/Content 路由到 HTTP，将 Outline/Cover/Image 路由到 Codex CLI。需要统一后端时可分别设置 `XHS_OUTLINE_PROVIDER=http`、`XHS_COVER_PROVIDER=http`，或把 `XHS_LLM_PROVIDER=codex_cli`。

## 仅从现有草稿生成图文

在项目根目录执行：

```bash
python content/carousel/generate_carousel.py \
  --draft content/drafts/<你的草稿>.json \
  --generate-images \
  --max-image-assets 6 \
  --cover-direction 0
```

- `--cover-direction` 可选 `0`、`1`、`2`，分别对应训练桌面静物、训练工具箱、轻运动空间。
- `--max-image-assets 6` 优先生成三套封面，再生成重要内容页。其余页面仍由模板生成信息卡，避免无意义地重复出图。
- 不传 `--generate-images` 时，所有页仍可生成，但使用本地占位视觉，不会调用 Codex CLI。

## 看板接口

向 `POST /api/carousel` 提交内容。该接口是用户主动触发的生成动作，默认会调用 Codex CLI：

```json
{
  "topic_id": "home_workout",
  "content": {"title": "标题", "body": "正文"},
  "generate_images": true,
  "max_image_assets": 6,
  "cover_direction_index": 0
}
```

接口返回 `task_id`；用既有的 `GET /api/task/<task_id>` 获取结果。结果中的 `image_generation` 会列出每张图片的状态和失败原因，`review` 不会因为单张图片失败而丢失整个帖子。

## Canva 交接

生成结果在：

```text
content/canva_packages/<post_id>/
├── page_01.jpg ... page_06.jpg       # 已排好的成品页
├── source_assets/                    # 可在 Canva 替换的无字原始素材
├── manifest.json                     # 每页模块、模板、图片状态和三套封面方向
├── editable_text_layers.json         # 结构化中文层备份
├── <post_id>_Canva可编辑.pptx        # 原生文字/形状/图片对象
└── CANVA_IMPORT.md                   # API 与人工兜底导入步骤
```

默认做法是把 `<post_id>_Canva可编辑.pptx` 人工上传 Canva；这不需要 Canva Pro、Developer integration 或 API 凭证。上传后检查中文字体、换行、裁切和图层。

如果运营者选择配置国际版 Canva Connect API，也可以在看板选择最终封面后点击“发送到 Canva”。CLI 等价命令：

```bash
.venv/bin/python -m canva_connect upload --post-id <post_id> --open
```

系统会重新构建分层 PPTX、自动刷新 OAuth 令牌、调用 Design Import API 并返回编辑链接。没有配置 Canva Connect 时直接人工上传同一个 PPTX。不要把中文文字交回给图片模型生成。

## 边界与失败处理

- 图片提示词要求无文字；允许使用全身着装完整、不可识别真人身份的成年引导角色，禁止真人仿冒、未成年人、身体改造对比、医疗或夸大训练效果承诺。
- Codex CLI 图像生成通常需要数分钟；`--max-image-assets` 控制本次额度，任务优先级是三套封面，再到内容页插画条。未生成图片的页面仍由本地信息图组件完成。
- 若 CLI 未登录、不可用或图片任务失败，流程会保留文案、版式和 Canva 包，并在 `image_generation.results` 中说明原因。重新生成只需再次触发对应草稿。
