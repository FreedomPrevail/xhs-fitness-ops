# Canva-editable carousel workflow

Use this workflow when the operator asks for a cover, carousel, Canva handoff, editable text, or a PPTX.

## Required deliverables

Create one package under `content/canva_packages/<post_id>/` containing:

- `page_01.jpg` through the final JPG as review and publishing previews;
- three topic-specific cover preview JPGs;
- `source_assets/` with accepted no-text ImageGen assets;
- `manifest.json` and `editable_text_layers.json`;
- `<post_id>_Canva可编辑.pptx` as the editable source;
- `CANVA_IMPORT.md` with the one-file Canva import instructions.

The deck must contain exactly one selected cover followed by 4–6 content pages. Keep the other two cover directions as separate preview choices; do not insert all three into the publishing sequence.

## ImageGen handoff

Invoke `$imagegen` only when new raster artwork is required. Generate one project-bound asset per cover direction or content-page visual task. Every prompt must include the intended frame, subject scale, safe margin, style lock, and the constraint `no text, letters, numbers, pseudo-writing, logo or watermark`.

Keep Chinese text, arrows, diagrams made of simple shapes, page numbers and compliance labels outside the generated bitmap. Images are replaceable Canva objects, not page backgrounds with baked-in copy.

## Editable PPTX contract

The portable dashboard/CLI builder runs automatically after the Canva package JSON and source assets exist:

```bash
python -c "from content.carousel.pptx_builder import ensure_editable_pptx; ensure_editable_pptx('content/canva_packages/<post_id>')"
```

When the Codex artifact runtime is available and a higher-fidelity deck is requested, the agent may instead run:

```bash
"$RUNTIME_NODE" skills/xhs-personal-ops/scripts/build_canva_carousel.mjs \
  --package-dir "content/canva_packages/<post_id>" \
  --output "content/canva_packages/<post_id>/<post_id>_Canva可编辑.pptx" \
  --preview-dir "content/canva_packages/<post_id>/pptx_preview"
```

`RUNTIME_NODE` and `RUNTIME_NODE_MODULES` must come from the Codex workspace dependency loader. Make `@oai/artifact-tool` resolvable for the command without installing or modifying global packages.

Every visible item must remain independently editable where practical:

- title, subtitle, badges, module labels, item labels and item descriptions: text boxes;
- cards, dividers, number markers and decorative surfaces: native shapes;
- ImageGen outputs: individual image objects using `contain` when full limbs or full equipment must remain visible;
- no full-page flattened screenshot underneath the editable objects.

## Text fitting

Choose layout and shorten copy before shrinking text. The builder applies a first fit pass, but the agent must still inspect renders.

- cover title: 54–82 px, at most 2 lines;
- page title: 42–62 px, at most 2 lines;
- subtitle: 24–30 px, normally 1 line;
- module title: 25–32 px;
- item label: 21–27 px;
- body: 18–23 px.

If body copy still overflows at 18 px, return to Content/Outline and reduce or split the unit. Do not trust Canva to auto-fit arbitrary Chinese copy.

## QA and Canva handoff

Render every slide and run overflow/overlap checks. Inspect full-size pages for clipped text, unexpected wrapping, cut-off bodies, tiny subjects, duplicate copy and missing layers. Rebuild until clean.

Manual upload of `<post_id>_Canva可编辑.pptx` is the default handoff and requires no API credentials. If the operator has explicitly configured and authorized the international Canva Connect integration, run:

```bash
python -m canva_connect upload --post-id <post_id> --open
```

The command rebuilds the currently selected cover into the layered PPTX, refreshes OAuth when necessary, creates and polls the Design Import job, and returns the temporary `edit_url`. The dashboard “发送到 Canva” button uses the same implementation. Never persist credentials, tokens or temporary edit URLs in the package. Importing never authorizes automatic publishing.
