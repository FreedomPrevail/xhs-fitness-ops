---
name: xhs-personal-ops
description: Build and run a personal Xiaohongshu fitness and weight-loss account system with operator-triggered analytics, Golden revalidation, personal voice memory, LLM topic decisions, no-text ImageGen visuals, and editable 5–7 page Canva carousel decks with three differentiated cover directions.
---

# Personal Xiaohongshu Operations

Read the project root `AGENTS.md` and `config/persona.yaml` before acting. Treat the root as the directory two levels above this Skill.

Keep two outputs stable across runs: a recognizable personal voice and a practical fitness/weight-loss account direction. Read `references/voice-memory.md` whenever importing new voice samples, changing the persona, or preparing prompts for Topic, Content, Outline or Cover agents.
Read `references/infographic-workflow.md` whenever generating or changing covers, content pages, image prompts, local rendering or Canva handoff.
Read `references/canva-editable-workflow.md` whenever the requested deliverable includes a cover, carousel, editable Canva handoff, PPTX, image replacement, or typography/layout QA.
Read `references/model-routing.md` when configuring DeepSeek/Base URL or unified Codex CLI routing.

## Preserve the manual-connected boundary

Use `XHS_OPERATION_MODE=manual_connected`. Run A1/A2, B1/B3 detail collection and Golden revalidation only after the operator clicks the corresponding dashboard control. Never schedule collection, loop through authentication/rate-limit errors, interact, or publish. Stop the current batch on login, verification, rate-limit or security warnings and leave resolution to the operator. Require manual review and official-client publishing.

## Run the daily workflow

1. Run A1 to read the whole visible Creator Center, then A2 to organize own-account and per-post metrics when the operator requests a refresh.
2. Run B1 to collect external samples and pause for the operator to choose at least one Human Golden. Run B3 only after that gate to hydrate details, extract Features, admit Golden Samples and rebuild Patterns.
3. Revalidate due Golden Samples only after the operator clicks the revalidation control. Fetch current likes/collects/comments, append a heat snapshot, recompute `revalidation_score`, and rebuild Patterns. Preserve `golden_score`, `transfer_score`, and `revalidation_score` as separate fields.
4. For operator-owned `.docx`/`.pages` writing samples, run `python -m personal_ops.import_writing_samples ...`; keep normalized text under `data/personal_knowledge`, store no source absolute paths, and mark it `voice_and_argumentation_style_only`.
5. Call `agents.topic.agent.decide_daily`. Let the LLM select only among canonical Topic Score candidates and require Pattern/Golden evidence IDs plus a freshness label.
6. On `adopt`, run Content Agent and the carousel generator with the same current Golden Pattern and revalidation evidence. Content emits 18–36 non-duplicative knowledge units. Outline groups them into flow, comparison, action-card, grid, mistake/fix, timeline, formula, FAQ, checklist or summary modules. Produce 5–7 pages and exactly three topic-specific cover directions.
7. If a cover or carousel needs new raster artwork, explicitly use `$imagegen`. Generate each cover direction and each page illustration as a separate no-text project asset. Do not ask ImageGen to render final Chinese copy, cards, arrows, page numbers, or compliance text.
8. Render finished JPG previews, then build one editable PPTX in which text, shapes and images remain separate Canva-editable objects. The normal project pipeline uses `content.carousel.pptx_builder`; `scripts/build_canva_carousel.mjs` remains the higher-fidelity Codex artifact-runtime option. Never flatten the editable deck back into page screenshots.
9. Render and inspect every PPTX page. Fix overflow, accidental overlap, clipped images, tiny people, unreadable type and stale placeholder copy before delivery. Save the package under `content/canva_packages/<post_id>/`.
10. Upload to Canva only when the operator explicitly requests it. Use `python -m canva_connect upload --post-id <post_id>` or the dashboard button, return the edit link, and stop before platform publishing. Never expose or persist Canva secrets, OAuth tokens, or temporary edit URLs in the package.

Run from the project root:

```bash
XHS_LLM_PROVIDER="http" XHS_LLM_BASE_URL="..." XHS_LLM_API_KEY="..." XHS_LLM_MODEL="..." \
  bash run_daily.sh --generate-images
```

This Base URL mode defaults to the hybrid route: Topic/Golden/Content use HTTP; Outline/Cover/Image use the local Codex CLI. Override a role with `XHS_TOPIC_PROVIDER`, `XHS_CONTENT_PROVIDER`, `XHS_OUTLINE_PROVIDER`, `XHS_COVER_PROVIDER` or `XHS_IMAGE_PROVIDER`. Set `XHS_CODEX_MODEL` only when a non-default Codex model is required.

Never store API keys in the Skill, project config, daily-run JSON, or Canva manifest.

Alternatively, use the locally authenticated Codex CLI for Topic, Golden, Content, Outline and Cover agents as well as image generation:

```bash
XHS_LLM_PROVIDER="codex_cli" \
  bash run_daily.sh --generate-images
```

Keep the HTTP/Base URL backend available. Resolve providers by role and never pass an HTTP model ID to Codex CLI. With Codex CLI, use structured `codex exec` output schemas and the default per-agent reasoning map: Topic/Golden/Content high, Outline/Cover medium, image generation low. Never use `--yolo` or grant the text agents write access.

The account Skill orchestrates image work; it does not replace the image skill. In Codex CLI, invoke `$xhs-personal-ops` for the account workflow and let it invoke `$imagegen` only for raster assets. If the logged-in CLI cannot access ImageGen, stop the image stage and keep the no-image renderer usable; do not silently switch to an API-key path.

## Interpret evidence

Use deterministic code for metrics and growth. Use the LLM only to interpret supplied evidence and choose an angle. Content, outline and cover prompts must receive current Pattern effective weights and each selected sample's separate golden/transfer/revalidation signals. Mark missing or old data as `unknown` or `stale`; never call cached evidence real-time. Avoid repeating the same angle within the persona's memory window unless new evidence materially changes the decision.

Treat owned writing samples as voice evidence only. Content may learn their thesis-first order, concrete evidence, comparison, humane tone and reflective closing; Outline may adapt that rhythm to short mobile pages. Never copy source sentences, reuse essay claims as fitness facts, infer personal fitness experience, or treat old figures as current trends. Use `config/persona.yaml` and `data/personal_knowledge/style_profile.json` as the derived style layer; preserve the normalized source text for traceability.

Keep voice identity and variation separate. Preserve stable anchors such as evidence-first reasoning, concrete detail, restrained empathy and limitations. Vary hook, page type, sentence rhythm, visual metaphor and format among tutorial, checklist, misconception, comparison, FAQ and training-log structures. Do not manufacture diversity through synonym replacement or unrelated trend chasing.

## Deliver

Report the selected topic, decision reason, evidence freshness, cited Pattern/Golden IDs, knowledge-unit count, module/template mix, compliance warnings, three cover concepts, page count, JPG preview paths, editable PPTX path, Canva import status, and that manual publishing is required.
