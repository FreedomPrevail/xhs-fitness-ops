# XHS Fitness Ops — Agent Contract

The system is split into analysis agents and generation agents. Agents exchange structured JSON; they do not directly mutate unrelated modules.

## Analysis chain
1. `Discovery Agent`: decide which platform posts deserve analysis. It must preserve provenance and a machine-readable selection reason.
2. `Feature Analyst`: explain why one candidate is useful. It extracts topic, audience/intent context, title/hook, content/copy, cover, and transferability. Inferred audience browsing/search timing must include confidence and basis; never present inference as observed platform analytics.
3. `Pattern Agent`: aggregate multiple admitted Golden Samples. It must keep evidence IDs and update pattern weights when `transfer_score` changes.
4. `Account Analyzer`: analyze observed own-account post metrics, audience snapshots, timing and operations. Missing metrics are unavailable, never zero; inferred timing must be labelled.

Official own-account analytics priority: read-only Creator Data Center (`/statistics/account/v2`) first, then creator CLI summaries, then saved/manual fallbacks. Visible account totals inform operations and Topic Agent evidence; only attributable per-note metrics may change a specific topic's Account Score.

## Golden data rule
Golden Pool is not a feature-only store. Every sample keeps canonical title/body, URL/noteId, source reason, metrics, extracted features, scores, and raw source snapshot. Golden Pattern is a derived layer and must never overwrite source copy.
Golden support combines Content, inferred Audience, Copy, observed Cover, Platform Performance and per-dimension own-account Transfer. External audience is inference with confidence; do not call it observed audience.

## Release gate
Topic discovery, generated copy/cover, Reviewer and server-side publish all use the versioned fitness policy gate. BLOCK must stop publishing; rules may explain or suggest a safer factual rewrite but must never evade moderation.

## 小红书运营合规与 2026-08-01 风险预警
这是一条账号安全的最高优先级约束。用户提供的“8 月 1 日起严打”内容是风险线索，不视为已核验的官方规则；任何 Agent 在将其作为平台事实、修改具体阈值或恢复外部访问前，必须附上可访问的官方规则链接、生效日期和核验日期。即使规则尚未核验，下列保守限制仍然生效。

### 账号操作：手动连接、逐项触发
1. 默认 `XHS_OPERATION_MODE=manual_connected`。A1/A2、B1/B3 详情补全和 Golden revalidate 只能在运营者点击相应按钮后运行；不得后台定时或在验证/限流后循环重试。仍不得自动点赞、收藏、关注、评论、私信、加群或发布。
2. 禁止任何模拟真人、规避或对抗风控的做法，包括设备/账号/IP 轮换、虚拟号、共享账号、自动化点击、规避验证，以及为规避审核而拆字、谐音、错别字或语义改写。
3. 收到新的账号预警、限流、验证、禁言或封禁通知时，当前批次立即停止且不得自动重试；保留站内信与原创素材证明，由用户通过官方客户端人工处理和申诉。不得建议“连续发帖恢复权重”等未经平台确认的补救动作。
4. 平台互动与自动发布保持关闭；数据读取、搜索、详情补全和复查均保留明确的用户点击门禁。

### 内容与素材：发布前必须检查
1. AI 参与文案、配图或封面时，发布人须在小红书官方发布界面按当时有效的平台要求选择/添加 AI 相关标识。Agent 只提醒和输出标识建议，不得伪造“已标识”状态。
2. 不得输出或建议绝对化效果、虚假体验、医疗功效、极端减重、身材羞辱、暗示性站外导流、标题党蹭热点或不可验证的横向贬损。现有 `compliance/policy.py` 的 BLOCK 优先级不变。
3. 每篇内容必须保留原创来源、生成记录和素材许可说明；不得搬运、洗稿或复用近似图文。相似度阈值若需使用，必须标明算法、比较范围和“内部风险参考”，不得声称是平台的固定 50% 阈值。
4. 测评、推荐和商业内容应基于可核验事实，清楚说明条件、局限和利益关系；不得用未授权平台、联系方式、二维码或“私信领取/移步主页”等话术引导站外交易或沟通。

### 待核验详细风险清单（用户提供，2026-08-19）
在提供官方原文前，下列内容只作为从严的内部风险检查项，不得向用户声称为平台已确认的固定处罚、固定阈值或唯一恢复办法：
1. **站外引流**：拦截联系方式、二维码、站外交易或沟通引导，以及“加好友”“看主页”“私信领福利”“完整内容不在这”等直接或隐晦话术；不得建议通过简介、评论、包裹卡片或变体文字规避识别。
2. **AI 标识**：若创作中使用 AI 文案或素材，输出发布前人工检查项，提醒按官方发布页当时可用的标识能力完成披露；未确认标识前不得声称笔记合规或已发布。
3. **夸大表述**：将“最管用”“百分百有效”“彻底改善”等绝对化、无法验证或过度承诺表达交给 `compliance/policy.py` 阻断或人工复核。
4. **账号与设备风控**：不得提供、记录或执行虚拟号、账号/设备/网络切换、多账号共网规避、身份不一致或规避平台验证的建议。账号身份与设备要求以平台官方规则和用户自行的合法操作为准。
5. **重复与搬运**：生成前检查来源、素材许可与近似内容；内部相似度仅作风险提示，禁止宣称“超过 50% 必定违规”。多账号内容差异化只能通过真实不同的选题、证据、结构和视觉设计实现，不能靠同义替换或洗稿。
6. **测评与推荐**：仅在可核验、可追溯的条件下呈现成分、价格、体验等比较维度；披露适用条件、局限与利益关系，不贬损竞品、不编造数据或体验。
7. **真实表达**：优先支持用户的真实日常、真实经历和可验证干货；AI 不得虚构亲历、效果数据、用户评价或平台认可。
8. **违规处置**：先保存站内通知和原创证据，停止自动化平台访问，再由用户通过官方客户端和帮助入口人工处理。不得自动删改笔记、自动申诉、反复发送客服关键词，或把“申诉后 24 小时连发三篇”当作权重恢复策略。

### 规则维护
所有新规都要写入版本化合规资料，至少包含：`rule_id`、来源链接、来源类型（官方/待核验）、生效日期、核验日期、适用范围、处理动作（WARN/BLOCK/人工复核）和失效/复核日期。`AGENTS.md` 只记录跨 Agent 的行为边界；可执行的文本审核写入 `compliance/policy.py`，可执行的账号访问开关写入独立的 operation policy，不能只靠提示词约束。

## Generation chain
`Topic Agent -> Content Agent -> Outline Agent -> Cover Agent -> Image/Canva package`. The default hybrid route uses the configured HTTP/Base URL model for Topic/Golden/Content and the locally authenticated Codex CLI for Outline/Cover/Image; every role remains overrideable. Topic, Content, Outline and Cover must consume the newest local Golden Pattern and keep `golden_score`, `transfer_score` and `revalidation_score` distinct. Low or stale revalidation evidence is weak evidence, never a real-time trend claim. No agent may copy long source passages or closely imitate a single source.

Canva Connect upload is a separate operator-triggered handoff. It may rebuild the selected layered PPTX, refresh the operator's OAuth token and create a Canva Design Import job only after the operator clicks “发送到 Canva” or runs the explicit CLI upload command. It must never run during page load, generation, a timer, or small-red-book publishing. Store Client Secret and user tokens only in the operating-system credential vault; never place them or temporary Canva edit URLs in project files, prompts, browser storage, manifests or ZIP archives.

Content must emit 18–36 non-duplicative `knowledge_units` with conditions and safety notes when the evidence supports them. Outline converts those units into 5–7 pages of `modules` such as flow, comparison, action cards, checklist, fact grid, mistake/fix, timeline, formula and FAQ. Content pages should normally contain 5–10 traceable information units and 1–3 modules; do not invent facts to hit a density target. Image generation owns only no-text illustrations. The local renderer owns Chinese typography, cards, arrows, tables and compliance footer, and must remain usable when image generation fails.

## Personal memory and owned writing samples
Personal memory is local-only. Import only material the operator marks `owned` or `authorized`, preserve its material ID and normalized local provenance, and never store account credentials, private messages, identity documents or third-party personal data. Recent topic decisions prevent repetitive angles; source documents are never overwritten by learned preferences.

Items with `kind=writing_sample` and `purpose=voice_and_argumentation_style_only` are voice evidence, not subject-matter evidence. Topic Agent may use their themes and availability when choosing an angle; Content and Outline may learn rhetorical order, evidence explanation, tone and closing style. They must not copy sentences, treat an essay's claims or old figures as current fitness facts, infer personal fitness experience, or call the samples platform trends. Cover/Image agents may use only the derived account tone, never unrelated people, incidents or claims from the essays. Adapt academic long-form traits to short mobile paragraphs while retaining concrete evidence, limitations and humane language.

The text backend is selectable per run: either an OpenAI-compatible Base URL or the locally authenticated Codex CLI. Codex CLI text calls must use structured output schemas, `read-only` sandboxing and ephemeral sessions. Preserve different reasoning depths by role; never use bypass/yolo flags. Image generation remains opt-in and may write only the current local package assets.

## Orchestration order
The six domain agents are the primary contracts. `Orchestrator` is now a thin coordinator over those stable contracts; `Reviewer` is independent from Content Agent and runs after generation. The existing manual dashboard flow remains available so orchestration is additive rather than a risky replacement.

## Official Topic Score commit rule
The only official write path is the successful end of the full pipeline: fitness samples -> Golden Candidate/Feature/Sample/Pattern -> account/platform evidence -> exact 35/30/30/5 calculation. Sample refresh, Golden actions, page load, recommendation read and partial analysis are evidence-only. A failed stage must preserve the prior committed score, version and chart.
