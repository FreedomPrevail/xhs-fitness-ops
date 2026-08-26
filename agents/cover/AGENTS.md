# Cover Agent Contract (next phase)
Design the cover brief from Topic Brief + final draft + relevant Golden cover patterns. Output concept, headline, layout, visual type, and evidence pattern IDs. Do not simply reproduce a Golden cover.
Use only image-text/legacy-compatible samples with actual observed cover features. Do not treat a video's metadata-only cover guess as image-text cover evidence.
Record cover feature usage against the current Golden usage ID. A real visual feature requires locally saved cover bytes and successful vision analysis.

Use Pattern `effective_weight` and each sample's independent golden/transfer/revalidation signals when selecting hierarchy, hook and visual metaphor. Generate three directions that differ in medium, composition and palette rather than color-only variants. A fully clothed, non-identifiable adult mascot is allowed when useful; never request a real-person likeness, minor, body-focused transformation or before/after comparison. The default hybrid route uses Codex CLI at medium reasoning depth for Cover decisions, followed by the opt-in ImageGen worker.
