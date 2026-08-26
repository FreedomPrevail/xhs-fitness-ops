#!/usr/bin/env node

import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const runtimeNodeModules = process.env.RUNTIME_NODE_MODULES;
if (!runtimeNodeModules) {
  throw new Error("RUNTIME_NODE_MODULES is required; obtain it from the Codex workspace dependency loader");
}
const require = createRequire(import.meta.url);
const artifactEntry = require.resolve("@oai/artifact-tool", { paths: [runtimeNodeModules] });
const { Presentation, PresentationFile } = await import(pathToFileURL(artifactEntry).href);

const WIDTH = 1080;
const HEIGHT = 1440;
const COLORS = {
  bg: "#FFF9EE",
  paper: "#FFFDF8",
  ink: "#513520",
  brown: "#8B5B3E",
  peach: "#F6C7AE",
  mint: "#CFE8D1",
  blue: "#CDE8F2",
  yellow: "#F8E4A3",
  coral: "#E98267",
  muted: "#A77D61",
  line: "#D9BEA9",
};
const PASTELS = [COLORS.peach, COLORS.mint, COLORS.blue, COLORS.yellow, "#E6D7F2", "#F4D7DD"];

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) result[key] = true;
    else {
      result[key] = value;
      index += 1;
    }
  }
  return result;
}

function clean(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function titleSize(value, cover = false) {
  const length = [...clean(value)].length;
  if (cover) {
    if (length <= 9) return 82;
    if (length <= 14) return 70;
    if (length <= 20) return 60;
    return 54;
  }
  if (length <= 10) return 62;
  if (length <= 16) return 54;
  if (length <= 22) return 47;
  return 42;
}

function bodySize(value, base = 22) {
  const length = [...clean(value)].length;
  if (length <= 22) return base;
  if (length <= 38) return Math.max(20, base - 1);
  return 18;
}

function addShape(slide, name, position, fill, options = {}) {
  const geometry = options.geometry || "roundRect";
  return slide.shapes.add({
    geometry,
    name,
    position,
    fill,
    line: options.line || { style: "solid", fill: COLORS.line, width: options.lineWidth ?? 2 },
    ...(["rect", "textbox", "roundRect"].includes(geometry)
      ? { borderRadius: options.borderRadius ?? "rounded-2xl" }
      : {}),
    ...(options.shadow ? { shadow: options.shadow } : {}),
  });
}

function addText(slide, name, value, position, options = {}) {
  const box = slide.shapes.add({
    geometry: "textbox",
    name,
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  box.text = clean(value);
  box.text.style = {
    fontSize: options.fontSize || 22,
    bold: Boolean(options.bold),
    color: options.color || COLORS.ink,
    alignment: options.alignment || "left",
    verticalAlignment: options.verticalAlignment || "top",
    autoFit: options.autoFit || "shrinkText",
    wrap: "square",
    lineSpacing: options.lineSpacing || 1.08,
    typeface: options.typeface || "Arial",
    insets: options.insets || { top: 3, right: 4, bottom: 3, left: 4 },
  };
  return box;
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

function contentTypeFor(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  if (extension === ".jpg" || extension === ".jpeg") return "image/jpeg";
  if (extension === ".webp") return "image/webp";
  return "image/png";
}

async function addImage(slide, filePath, position, name, fit = "contain") {
  try {
    const bytes = await fs.readFile(filePath);
    const blob = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return slide.images.add({
      blob,
      contentType: contentTypeFor(filePath),
      alt: name,
      fit,
      position,
      geometry: "roundRect",
      borderRadius: "rounded-2xl",
      name,
    });
  } catch {
    return null;
  }
}

async function readImageSource(filePath) {
  if (!filePath) return null;
  try {
    const bytes = await fs.readFile(filePath);
    return {
      blob: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
      contentType: contentTypeFor(filePath),
    };
  } catch {
    return null;
  }
}

function cropForCell(index) {
  const normalized = Math.max(0, Math.min(3, index));
  const column = normalized % 2;
  const row = Math.floor(normalized / 2);
  return {
    left: column === 0 ? 0 : 0.5,
    top: row === 0 ? 0 : 0.5,
    right: column === 0 ? 0.5 : 0,
    bottom: row === 0 ? 0.5 : 0,
  };
}

function addBackground(slide, variant = 0) {
  slide.background.fill = COLORS.bg;
  addShape(slide, "decor-top-left", { left: -120, top: -110, width: 400, height: 330 }, PASTELS[(variant + 2) % PASTELS.length], {
    geometry: "ellipse",
    line: { style: "solid", fill: "none", width: 0 },
  });
  addShape(slide, "decor-top-right", { left: 875, top: -95, width: 300, height: 270 }, PASTELS[(variant + 1) % PASTELS.length], {
    geometry: "ellipse",
    line: { style: "solid", fill: "none", width: 0 },
  });
  addShape(slide, "page-border", { left: 22, top: 22, width: WIDTH - 44, height: HEIGHT - 44 }, "none", {
    geometry: "roundRect",
    line: { style: "solid", fill: "#C89E7E", width: 3 },
    borderRadius: 38,
  });
}

function addBadge(slide, value, top = 112) {
  const width = Math.min(390, Math.max(170, 78 + [...clean(value)].length * 31));
  const badge = addShape(slide, "page-badge", { left: 56, top, width, height: 54 }, COLORS.yellow, {
    line: { style: "solid", fill: COLORS.brown, width: 2 },
    borderRadius: 26,
  });
  badge.text = clean(value || "实用指南");
  badge.text.style = {
    fontSize: 25,
    bold: true,
    color: COLORS.ink,
    alignment: "center",
    verticalAlignment: "middle",
    autoFit: "shrinkText",
    typeface: "Arial",
    insets: { top: 2, right: 8, bottom: 2, left: 8 },
  };
}

function addFooter(slide, pageNo) {
  addShape(slide, "footer-rule", { left: 58, top: 1358, width: 964, height: 1 }, COLORS.line, {
    geometry: "rect",
    line: { style: "solid", fill: "none", width: 0 },
    borderRadius: 0,
  });
  addText(slide, "compliance-footer", "AI 辅助创作 · 发布前请核对训练条件与身体反馈", { left: 58, top: 1372, width: 770, height: 34 }, {
    fontSize: 19,
    color: COLORS.brown,
    verticalAlignment: "middle",
  });
  const number = addShape(slide, "page-number", { left: 916, top: 1366, width: 106, height: 46 }, COLORS.brown, {
    line: { style: "solid", fill: "none", width: 0 },
    borderRadius: 23,
  });
  number.text = String(pageNo).padStart(2, "0");
  number.text.style = {
    fontSize: 21,
    bold: true,
    color: COLORS.paper,
    alignment: "center",
    verticalAlignment: "middle",
    typeface: "Arial",
  };
}

function moduleRows(module) {
  const columns = Array.isArray(module.columns) ? module.columns.filter(Boolean).slice(0, 2) : [];
  if (columns.length === 2 && ["comparison", "mistake_fix"].includes(module.type)) {
    return columns.map((column, index) => ({
      label: clean(column.title || `方案${index + 1}`),
      detail: (column.items || []).map(clean).filter(Boolean).join("\n"),
      value: "",
      tag: index === 0 ? "先判断" : "再选择",
    }));
  }
  return (Array.isArray(module.items) ? module.items : []).filter(Boolean).slice(0, 6);
}

function addModule(slide, module, position, moduleIndex, imageSource = null) {
  const fill = COLORS.paper;
  addShape(slide, `module-${moduleIndex + 1}`, position, fill, {
    line: { style: "solid", fill: COLORS.line, width: 2 },
    shadow: "shadow-sm",
  });
  const pillWidth = Math.min(position.width - 44, Math.max(220, 72 + [...clean(module.title || "本页重点")].length * 30));
  const pill = addShape(slide, `module-${moduleIndex + 1}-title`, {
    left: position.left + 20,
    top: position.top + 16,
    width: pillWidth,
    height: 50,
  }, PASTELS[moduleIndex % PASTELS.length], {
    line: { style: "solid", fill: COLORS.brown, width: 1.5 },
    borderRadius: 25,
  });
  pill.text = clean(module.title || "本页重点");
  pill.text.style = {
    fontSize: 27,
    bold: true,
    color: COLORS.ink,
    alignment: "center",
    verticalAlignment: "middle",
    autoFit: "shrinkText",
    typeface: "Arial",
    insets: { top: 2, right: 8, bottom: 2, left: 8 },
  };

  const rows = moduleRows(module);
  if (!rows.length) {
    addText(slide, `module-${moduleIndex + 1}-empty`, clean(module.note || "保留这一页的核心行动"), {
      left: position.left + 28,
      top: position.top + 92,
      width: position.width - 56,
      height: position.height - 118,
    }, { fontSize: 23, color: COLORS.brown, verticalAlignment: "middle", alignment: "center" });
    return;
  }

  const columns = rows.length === 1 ? 1 : 2;
  const lines = Math.ceil(rows.length / columns);
  const gap = 14;
  const innerLeft = position.left + 20;
  const innerTop = position.top + 82;
  const innerWidth = position.width - 40;
  const innerHeight = position.height - 102;
  const cardWidth = (innerWidth - gap * (columns - 1)) / columns;
  const cardHeight = (innerHeight - gap * (lines - 1)) / lines;

  rows.forEach((row, index) => {
    const column = index % columns;
    const line = Math.floor(index / columns);
    const left = innerLeft + column * (cardWidth + gap);
    const top = innerTop + line * (cardHeight + gap);
    addShape(slide, `module-${moduleIndex + 1}-item-${index + 1}`, {
      left,
      top,
      width: cardWidth,
      height: cardHeight,
    }, "#FFFFFF", {
      line: { style: "solid", fill: "#E2CDBB", width: 1.5 },
      borderRadius: 20,
    });
    const useImage = Boolean(imageSource && index < 4 && cardHeight >= 130);
    const markerSize = cardHeight < 130 ? 38 : 46;
    const visualWidth = useImage ? Math.min(142, Math.max(94, cardWidth * 0.31)) : markerSize;
    if (useImage) {
      slide.images.add({
        ...imageSource,
        alt: `module ${moduleIndex + 1} item ${index + 1} illustration`,
        fit: "cover",
        crop: cropForCell(index),
        position: {
          left: left + 12,
          top: top + 12,
          width: visualWidth,
          height: Math.max(70, cardHeight - 24),
        },
        geometry: "roundRect",
        borderRadius: 16,
      });
    } else {
      const marker = addShape(slide, `module-${moduleIndex + 1}-marker-${index + 1}`, {
        left: left + 14,
        top: top + 14,
        width: markerSize,
        height: markerSize,
      }, PASTELS[(moduleIndex + index) % PASTELS.length], {
        geometry: "ellipse",
        line: { style: "solid", fill: COLORS.brown, width: 1.5 },
      });
      marker.text = String(index + 1);
      marker.text.style = {
        fontSize: cardHeight < 130 ? 17 : 20,
        bold: true,
        color: COLORS.ink,
        alignment: "center",
        verticalAlignment: "middle",
        typeface: "Arial",
      };
    }
    const textLeft = left + visualWidth + 24;
    const compact = cardHeight < 140;
    const labelTop = top + (compact ? 8 : 13);
    const labelHeight = compact ? 38 : 48;
    const detailTop = top + (compact ? 47 : 70);
    addText(slide, `module-${moduleIndex + 1}-label-${index + 1}`, clean(row.label || row.tag || `要点${index + 1}`), {
      left: textLeft,
      top: labelTop,
      width: cardWidth - visualWidth - 38,
      height: labelHeight,
    }, { fontSize: compact ? 21 : 25, bold: true, verticalAlignment: "middle" });
    const secondary = [clean(row.value), clean(row.detail || row.cue), clean(row.tag)].filter(Boolean).join("\n");
    addText(slide, `module-${moduleIndex + 1}-detail-${index + 1}`, secondary || "按身体反馈逐步执行", {
      left: textLeft,
      top: detailTop,
      width: cardWidth - visualWidth - 38,
      height: Math.max(28, cardHeight - (compact ? 55 : 88)),
    }, { fontSize: compact ? 18 : bodySize(secondary, 22), color: COLORS.brown, lineSpacing: compact ? 1.0 : 1.12 });
  });
}

async function assetForPage(packageDir, manifest, pageNo) {
  const rows = Array.isArray(manifest.source_assets) ? manifest.source_assets.filter((row) => Number(row.page_no) === Number(pageNo)) : [];
  if (!rows.length) return "";
  let selected = rows[0];
  if (pageNo === 1) {
    const index = Number(manifest.selected_cover_index || 0) + 1;
    selected = rows.find((row) => String(row.path || "").includes(`_cover_${String(index).padStart(2, "0")}`)) || rows[0];
  }
  const candidate = path.resolve(packageDir, String(selected.path || ""));
  try {
    await fs.access(candidate);
    return candidate;
  } catch {
    return "";
  }
}

async function buildCover(presentation, page, packageDir, manifest) {
  const slide = presentation.slides.add();
  addBackground(slide, 0);
  addText(slide, "cover-kicker", "FITNESS · GUIDE", { left: 58, top: 52, width: 420, height: 42 }, {
    fontSize: 22,
    bold: true,
    color: COLORS.brown,
  });
  addBadge(slide, page.badge || "新手指南", 118);
  addText(slide, "cover-title", page.title || "健身指南", { left: 56, top: 196, width: 968, height: 190 }, {
    fontSize: titleSize(page.title, true),
    bold: true,
    lineSpacing: 0.96,
  });
  addText(slide, "cover-subtitle", page.subtitle || "保存下来照着做", { left: 60, top: 382, width: 900, height: 62 }, {
    fontSize: 30,
    bold: true,
    color: COLORS.coral,
    verticalAlignment: "middle",
  });
  const artFrame = { left: 68, top: 476, width: 944, height: 780 };
  addShape(slide, "cover-art-frame", artFrame, "#FFFFFF", {
    line: { style: "solid", fill: COLORS.line, width: 2 },
    shadow: "shadow-md",
  });
  const artPath = await assetForPage(packageDir, manifest, 1);
  if (artPath) {
    await addImage(slide, artPath, { left: 86, top: 494, width: 908, height: 744 }, "cover-image", "contain");
  } else {
    addText(slide, "cover-art-placeholder", "可替换封面插画\n图片保持无字，中文标题独立编辑", {
      left: 150,
      top: 740,
      width: 780,
      height: 150,
    }, { fontSize: 28, color: COLORS.muted, alignment: "center", verticalAlignment: "middle" });
  }
  addFooter(slide, 1);
}

async function buildContentPage(presentation, page, packageDir, manifest, variant) {
  const slide = presentation.slides.add();
  addBackground(slide, variant);
  addText(slide, "page-kicker", "FITNESS · GUIDE", { left: 56, top: 48, width: 400, height: 38 }, {
    fontSize: 21,
    bold: true,
    color: COLORS.brown,
  });
  addBadge(slide, page.badge || "实用指南", 110);
  addText(slide, "page-title", page.title || "本页重点", { left: 56, top: 178, width: 968, height: 124 }, {
    fontSize: titleSize(page.title, false),
    bold: true,
    lineSpacing: 0.98,
  });
  addText(slide, "page-subtitle", page.subtitle || "", { left: 58, top: 298, width: 940, height: 52 }, {
    fontSize: 27,
    bold: true,
    color: COLORS.coral,
    verticalAlignment: "middle",
  });

  const artPath = await assetForPage(packageDir, manifest, page.page_no);
  const imageSource = await readImageSource(artPath);
  const modulesTop = 378;

  const modules = Array.isArray(page.modules) ? page.modules.filter(Boolean).slice(0, 3) : [];
  const availableBottom = 1338;
  const gap = 18;
  const moduleCount = Math.max(1, modules.length);
  const effectiveModules = modules.length ? modules : [{ title: "本页行动", note: "完成后根据身体反馈调整", items: [] }];
  const availableHeight = availableBottom - modulesTop - gap * (moduleCount - 1);
  const minimum = moduleCount >= 3 ? 220 : moduleCount === 2 ? 280 : availableHeight;
  const weights = effectiveModules.map((module) => Math.max(1, moduleRows(module).length));
  const weightTotal = weights.reduce((sum, value) => sum + value, 0);
  const flexible = Math.max(0, availableHeight - minimum * moduleCount);
  const heights = effectiveModules.map((_, index) => minimum + flexible * weights[index] / weightTotal);
  let cursorTop = modulesTop;
  effectiveModules.forEach((module, index) => {
    addModule(slide, module, {
      left: 58,
      top: cursorTop,
      width: 964,
      height: heights[index],
    }, index, imageSource);
    cursorTop += heights[index] + gap;
  });
  addFooter(slide, page.page_no);
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args["package-dir"]) throw new Error("--package-dir is required");
  const packageDir = path.resolve(String(args["package-dir"]));
  const manifestPath = path.join(packageDir, "manifest.json");
  const layersPath = path.join(packageDir, "editable_text_layers.json");
  const manifest = await readJson(manifestPath);
  const layers = await readJson(layersPath);
  const pages = Array.isArray(layers.pages) ? layers.pages : [];
  if (pages.length < 2) throw new Error("editable_text_layers.json must contain a cover and at least one content page");

  const postId = clean(manifest.post_id || path.basename(packageDir));
  const output = path.resolve(String(args.output || path.join(packageDir, `${postId}_Canva可编辑.pptx`)));
  const previewDir = args["preview-dir"] ? path.resolve(String(args["preview-dir"])) : "";
  await fs.mkdir(path.dirname(output), { recursive: true });
  if (previewDir) await fs.mkdir(previewDir, { recursive: true });

  const presentation = Presentation.create({ slideSize: { width: WIDTH, height: HEIGHT } });
  await buildCover(presentation, pages[0], packageDir, manifest);
  for (let index = 1; index < pages.length; index += 1) {
    await buildContentPage(presentation, pages[index], packageDir, manifest, index);
  }

  if (previewDir) {
    for (const [index, slide] of presentation.slides.items.entries()) {
      const stem = `slide-${String(index + 1).padStart(2, "0")}`;
      await writeBlob(path.join(previewDir, `${stem}.png`), await presentation.export({ slide, format: "png", scale: 1 }));
      const layout = await slide.export({ format: "layout" });
      await fs.writeFile(path.join(previewDir, `${stem}.layout.json`), await layout.text());
    }
    await writeBlob(path.join(previewDir, "montage.webp"), await presentation.export({ format: "webp", montage: true, scale: 1 }));
  }

  const inspection = await presentation.inspect({ kind: "slide,textbox,shape,image", maxChars: 50000 });
  await fs.writeFile(`${output}.inspect.ndjson`, inspection.ndjson, "utf8");
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(output);

  if (path.dirname(output) === packageDir) {
    manifest.editable_canva_deck = path.basename(output);
    manifest.editable_layer_types = ["text", "shape", "image"];
    manifest.canva_import_mode = "editable_pptx";
    await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
    const importGuide = `# Canva 可编辑导入步骤

1. 打开 Canva，在首页选择“上传/导入文件”。
2. 上传 \`${path.basename(output)}\`，等待 Canva 转换成 ${pages.length} 页设计。
3. 标题、正文、页码、卡片、色块和图片均为独立对象，可直接改字、移动、缩放、裁剪或替换。
4. 如果 Canva 替换了中文字体，框选文字后统一改为可用的中文字体，并检查是否发生换行。
5. 修改完成后导出 1080 × 1440 PNG；发布前重新检查文字裁切、训练条件、AI 标识和合规项。

## 其他素材

- \`page_01.jpg\` 至最后一页：发布预览图，不作为可编辑底图。
- \`cover_variant_01.jpg\` 至 \`cover_variant_03.jpg\`：三套封面方向预览。
- \`source_assets/\`：无字原始插画，可在 Canva 中替换现有图片对象。
- \`editable_text_layers.json\`：结构化文字备份。
- \`manifest.json\`：页面、模块、素材和可编辑文件映射。

导入后仍需人工审核与发布，不执行自动发布。
`;
    await fs.writeFile(path.join(packageDir, "CANVA_IMPORT.md"), importGuide, "utf8");
  }
  process.stdout.write(`${JSON.stringify({ ok: true, output, pages: pages.length, preview_dir: previewDir })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});
