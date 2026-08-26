"""Deterministic density, structure and image-generation checks for carousel packages."""
from __future__ import annotations


def _units(page: dict) -> int:
    total=0
    for module in page.get("modules") or []:
        total+=len(module.get("items") or [])
        total+=sum(len(column.get("items") or []) for column in (module.get("columns") or []))
    return total


def review(outline: dict, visual_spec: dict, image_generation: dict | None=None) -> dict:
    issues=[]; warnings=[]; pages=outline.get("pages",[])
    if not 5<=len(pages)<=7:
        issues.append("页数必须为 5–7 页")
    if not pages or pages[0].get("type")!="cover":
        issues.append("第一页必须为封面")
    if pages and pages[-1].get("type") not in ("checklist","summary","cta"):
        issues.append("最后一页应为总结、清单或行动引导")
    sparse=[]; overdense=[]; module_types=[]
    for page in pages:
        no=page.get("page_no")
        if len(page.get("title",""))>32:
            issues.append(f"第{no}页标题过长")
        if no==1:
            continue
        modules=page.get("modules") or []
        if not modules:
            issues.append(f"第{no}页没有信息图模块")
        if len(modules)>4:
            issues.append(f"第{no}页模块超过4个")
        count=_units(page)
        if count<4: sparse.append(no)
        if count>12: overdense.append(no)
        module_types.extend(str(module.get("type") or "") for module in modules)
        for module in modules:
            for item in module.get("items") or []:
                if len(str(item.get("detail") or ""))>46:
                    issues.append(f"第{no}页存在过长的信息单元")
                    break
    if sparse:
        warnings.append("这些内容页信息单元少于4个："+",".join(map(str,sparse)))
    if overdense:
        warnings.append("这些内容页信息单元超过12个，需重点检查手机可读性："+",".join(map(str,overdense)))
    if len(set(module_types))<3 and len(pages)>=5:
        warnings.append("模块类型变化不足，建议在流程、对比、动作卡、网格和清单之间轮换")
    if len(visual_spec.get("pages",[]))!=len(pages):
        issues.append("视觉规格与内容页数不一致")
    image=image_generation or {}
    if image.get("enabled") and not image.get("generated"):
        warnings.append("Codex CLI 未生成素材，已使用本地图标和粉彩信息图组件；可查看 image_generation.results 后重试。")
    if image.get("failed"):
        warnings.append(f"有 {image['failed']} 个图片任务失败；对应页面已用本地视觉组件降级。")
    if image.get("deferred"):
        issues.append(f"有 {image['deferred']} 个必需图片任务因素材上限未生成")
    return {"status":"PASS" if not issues else "REVISE","issues":issues,"warnings":warnings,
            "checks":{"page_count":len(pages),"template_count":len({p.get('template_type') for p in visual_spec.get('pages',[])}),
                      "module_type_count":len(set(module_types)),"information_units":sum(_units(page) for page in pages),
                      "generated_assets":image.get("generated",0)}}
