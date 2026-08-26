# Voice memory and account direction

## Collect

Accept only operator-owned or authorized samples. Use `writing_sample` for articles/captions and `voice_sample` for transcripts or dictated notes. Preserve full normalized text locally, a stable material ID, rights status and purpose. Do not store credentials, private conversations or third-party personal data.

Classify each sample as one or more of:

- `voice_only`: sentence rhythm, transitions, attitude and explanation style;
- `personal_experience`: a real first-person event the operator explicitly confirms may be used;
- `domain_reference`: a fitness/weight-loss fact source with provenance and date;
- `visual_preference`: preferred cover/page examples, not textual facts.

Default articles and transcripts to `voice_only`. Never promote them to personal experience or factual evidence by inference.

## Distill

Maintain a compact derived profile rather than placing every full document into every prompt. Separate:

- stable voice anchors: how the operator judges, explains evidence, shows empathy and states limits;
- adaptation rules: how long-form language becomes short mobile paragraphs;
- forbidden inferences: experiences, facts or trends the samples do not prove;
- variation axes: hook, structure, page type, level of formality and visual metaphor.

Update `config/persona.yaml` for stable account identity and `data/personal_knowledge/style_profile.json` for the derived voice profile. Keep normalized sources immutable.

## Apply

- Topic Agent: stay primarily within practical fitness tips and rational weight-loss guides; use recent decisions to avoid repeating an angle.
- Content Agent: learn voice anchors from bounded excerpts, then write original platform-native copy. Prefer fact → meaning → action → limitation.
- Outline Agent: convert the same reasoning into 5–7 concise pages and vary the page structure to fit the topic.
- Cover Agent: use the account direction and derived tone only. Do not place unrelated essay people, incidents or claims into fitness visuals.
- Reviewer: flag copied phrases, unsupported first-person claims, excessive academic density, repetitive structures and style drift.

## Learn

Treat explicit operator feedback such as “像我/不像我”, preferred rewrites and approved final drafts as voice feedback. Keep platform performance feedback separate: it may change topic, structure or cover preference, but it must not redefine the operator's voice automatically. Require repeated evidence before changing a stable voice anchor.
