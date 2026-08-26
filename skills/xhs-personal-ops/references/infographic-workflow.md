# Modular infographic workflow

## Content contract

Generate 18–36 distinct `knowledge_units` before page planning. Keep one fact, action, comparison, parameter or safety boundary per unit. Preserve conditions and basis status. Do not split one sentence into paraphrases to inflate density, and do not use Golden samples as factual fitness evidence.

## Outline contract

Produce 5–7 pages. Keep page 1 as the cover. On each content page, group traceable knowledge units into 1–3 modules and normally show 5–10 information units. Use the module that matches the relationship:

- `step_flow`: ordered sequence;
- `comparison`: two conditional choices;
- `action_cards`: two or three actions with cue/value;
- `fact_grid`: independent facts or parameters;
- `mistake_fix`: mistake and safer alternative;
- `timeline`: chronological sequence;
- `formula`: components forming one result;
- `faq`: question and concise answer;
- `checklist` / `summary`: review and next action.

Keep 25% breathing room. Reduce modules when the evidence is insufficient; never invent a number or instruction to meet a visual target.

## Image contract

Generate only no-text assets. Prefer a consistent warm pastel editorial style for content pages. When a person helps explain an action, use a fully clothed, non-identifiable adult mascot; do not imitate a real person, depict minors, emphasize body measurements, or create before/after imagery. Request separated vignette cells so the local renderer can crop one asset across module cards.

When Codex is the visual provider, invoke `$imagegen` explicitly. Treat every cover direction and every content-page illustration as a distinct asset request; do not use one generated montage as the final carousel. Save accepted assets into the current post package before rendering. Preserve a minimum 12% safe margin, full limbs and enough negative space for independent Chinese text boxes.

## Rendering and Canva

Let `content/carousel/render_post.py` own the finished JPG previews. It must produce a complete page even when ImageGen is disabled or fails. Export JPG files, `source_assets/`, `manifest.json`, and `editable_text_layers.json`.

For Canva-editable delivery, also create `<post_id>_Canva可编辑.pptx` with separate native text boxes, shapes and image objects. Use `scripts/build_canva_carousel.mjs`; the PPTX is the editable source and the JPG files are visual previews only. Do not place a rendered JPG behind editable text, because that duplicates baked-in copy. Import the PPTX into Canva as one design rather than uploading every page asset separately.

Use deterministic text-fit rules before PPTX export: shorten copy or switch module layout first; only then reduce size within the allowed tier. Keep cover title at 54 px or larger, page titles at 42 px or larger, module titles at 25 px or larger, and body copy at 18 px or larger on the 1080 x 1440 canvas. No text box may rely on Canva to repair overflow automatically.

## Review

Check page count, title length, module count, total information units, template variety, duplicate units, text overflow, safety conditions and image-generation failures. Render every editable PPTX page and inspect it at full size; fix unintended overlap and clipping before delivery. Treat sparse pages as a content/outline issue, not a reason to enlarge decorative art or fabricate details.
