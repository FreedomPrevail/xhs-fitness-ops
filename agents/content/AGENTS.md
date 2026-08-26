# Content Agent Contract (for next implementation phase)

## Mission
Write an original Xiaohongshu fitness post for one approved Topic Brief, using Golden Pattern as the primary guide and selected Golden Samples as supporting evidence.

## Inputs
- `topic_brief` from Topic Agent
- target `audience_intent`
- top relevant `golden_patterns`
- 2-5 retrieved `golden_samples` including canonical title/body and features
- persona/compliance config

## How to use Golden Samples
Use source copy to understand angle, pacing, specificity, proof style, and what information readers save/share. Extract reusable moves; do not sentence-match, paraphrase line-by-line, or inherit a single author's distinctive wording.

## Output
Must validate against `schemas/content_agent_output.schema.json`. Output includes chosen pattern IDs and Golden evidence IDs so later performance attribution can update `transfer_score`.

## Quality constraints
- Audience, intent and search keyword must match the Topic Brief.
- Opening hook and body structure must be explainable by cited Golden Pattern fields.
- Do not consume legacy fixed `title_patterns` from topic configuration. Use only the whitelisted Topic Brief plus retrieved Golden Pattern/Sample evidence.
- Manually selected live-search titles are trend evidence only; do not imitate their title sentence patterns.
- This system generates image-text posts. Prefer `content_type=image_text`; legacy `unknown` samples are usable only when canonical body exists. Metadata-only video samples may inform Topic/hook evidence but must not become image-text body-structure evidence.
- Include concrete, actionable fitness information rather than generic motivational copy.
- Emit 18–36 distinct `knowledge_units` spanning principles, steps, parameters, comparisons, mistakes, checks and safety boundaries. Every unit expresses one idea and includes its factual basis status; never pad the count with paraphrases or unsupported numbers.
- Keep source/evidence traceability in metadata, not in public-facing text.
- Record exact topic/audience/content/copy feature usage so feedback can update per-dimension transfer scores.
- Inspect `golden_score`, `transfer_score`, `revalidation_score`, last revalidation time and snapshot count separately. Prefer durable, revalidated structures and mark stale evidence as weak; never describe local evidence as live heat.
- HTTP and Codex CLI are equivalent structured backends. The default hybrid route uses HTTP/Base URL for Content. Codex CLI uses high reasoning depth when selected and must validate against `schemas/content_agent_output.schema.json`.
