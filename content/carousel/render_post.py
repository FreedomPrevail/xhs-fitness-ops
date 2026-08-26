"""Component-based local 1080×1440 infographic renderer.

Generated assets remain text-free.  This module owns Chinese typography,
information hierarchy, arrows, cards, comparison columns and safety footers.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

W,H=1080,1440
BG="#FFF9EE"; INK="#513520"; BROWN="#8B5B3E"; PEACH="#F6C7AE"
MINT="#CFE8D1"; BLUE="#CDE8F2"; YELLOW="#F8E4A3"; CORAL="#E98267"; WHITE="#FFFDF8"
PASTELS=[PEACH,MINT,BLUE,YELLOW,"#E6D7F2","#F4D7DD"]


def _font(size: int, bold: bool=False):
    candidates=(
      ("/System/Library/Fonts/PingFang.ttc",8 if bold else 2),  # PingFang SC Semibold / Regular
      ("/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",0),
      ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",0),
    )
    for path,index in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path,size=size,index=index)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int=2) -> list[str]:
    value=" ".join(str(text or "").split())
    if not value:
        return []
    lines=[]; current=""
    for char in value:
        candidate=current+char
        if current and draw.textlength(candidate,font=font)>max_width:
            lines.append(current); current=char
            if len(lines)>=max_lines:
                break
        else:
            current=candidate
    if len(lines)<max_lines and current:
        lines.append(current)
    if len("".join(lines))<len(value) and lines:
        last=lines[-1]
        while last and draw.textlength(last+"…",font=font)>max_width:
            last=last[:-1]
        lines[-1]=last+"…"
    return lines


def _draw_text(draw, xy, text, font, fill=INK, max_width=900, max_lines=2, spacing=5, anchor=None):
    lines=_wrap(draw,text,font,max_width,max_lines)
    value="\n".join(lines)
    draw.multiline_text(xy,value,font=font,fill=fill,spacing=spacing,anchor=anchor)
    return len(lines)


def _rounded_mask(size: tuple[int,int], radius: int=28) -> Image.Image:
    mask=Image.new("L",size,0); md=ImageDraw.Draw(mask)
    md.rounded_rectangle((0,0,size[0]-1,size[1]-1),radius=radius,fill=255)
    return mask


def _paste_crop(canvas: Image.Image, source: Image.Image | None, box: tuple[int,int,int,int],
                *, segment: int=0, total: int=1, radius: int=28, opacity: float=1.0,
                contain: bool=False, focus_fill: bool=False,
                vertical_range: tuple[float,float] | None=None,
                grid_columns: int=0) -> bool:
    if source is None:
        return False
    total=max(1,total); segment=max(0,min(segment,total-1))
    sw,sh=source.size
    if grid_columns>0:
        columns=max(1,min(grid_columns,total)); rows=max(1,(total+columns-1)//columns)
        col=segment%columns; row=segment//columns
        left=round(sw*col/columns); right=round(sw*(col+1)/columns)
        top=round(sh*row/rows); bottom=round(sh*(row+1)/rows)
        crop=source.crop((left,top,max(left+1,right),max(top+1,bottom))).convert("RGB")
    else:
        left=round(sw*segment/total); right=round(sw*(segment+1)/total)
        crop=source.crop((left,0,max(left+1,right),sh)).convert("RGB")
    if vertical_range:
        top_ratio=max(0.0,min(1.0,float(vertical_range[0])))
        bottom_ratio=max(top_ratio+.01,min(1.0,float(vertical_range[1])))
        crop=crop.crop((0,round(crop.height*top_ratio),crop.width,round(crop.height*bottom_ratio)))
    size=(box[2]-box[0],box[3]-box[1])
    if contain or focus_fill:
        background=Image.new("RGB",crop.size,crop.getpixel((max(0,min(4,crop.width-1)),max(0,min(4,crop.height-1)))))
        difference=ImageChops.difference(crop,background).convert("L").point(lambda value: 255 if value>28 else 0)
        bounds=difference.getbbox()
        if bounds:
            margin=max(8,round(min(crop.size)*.04))
            crop=crop.crop((max(0,bounds[0]-margin),max(0,bounds[1]-margin),
                            min(crop.width,bounds[2]+margin),min(crop.height,bounds[3]+margin)))
    if focus_fill:
        fitted=ImageOps.fit(crop,size,centering=(.5,.46))
    elif contain:
        fitted=Image.new("RGB",size,WHITE)
        contained=ImageOps.contain(crop,size)
        fitted.paste(contained,((size[0]-contained.width)//2,(size[1]-contained.height)//2))
    else:
        fitted=ImageOps.fit(crop,size)
    if opacity<1:
        wash=Image.new("RGB",fitted.size,WHITE)
        fitted=Image.blend(wash,fitted,opacity)
    canvas.paste(fitted,box[:2],_rounded_mask(fitted.size,radius))
    return True


def _load_page_asset(out_dir: Path, page_no: int, asset_plan: dict, selected_cover: str="") -> Image.Image | None:
    task=None
    if page_no==1 and selected_cover:
        task=next((row for row in asset_plan.get("asset_tasks",[]) if row.get("asset_id")==selected_cover),None)
    if task is None:
        task=next((row for row in asset_plan.get("asset_tasks",[]) if row.get("page_no")==page_no
                   and row.get("type")!="cover_variant"),None)
    if task is None and page_no==1:
        task=next((row for row in asset_plan.get("asset_tasks",[]) if row.get("page_no")==1),None)
    if not task:
        return None
    raw=Path(str(task.get("output_path") or ""))
    path=raw if raw.is_absolute() else out_dir/raw
    if not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            return source.convert("RGB")
    except Exception:
        return None


def _background(image: Image.Image, variant: int=0) -> ImageDraw.ImageDraw:
    draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,W,H),fill=BG)
    draw.ellipse((-150,-120,360,330),fill=PASTELS[(variant+2)%len(PASTELS)])
    draw.ellipse((800,-120,1200,260),fill=PASTELS[(variant+1)%len(PASTELS)])
    draw.ellipse((860,1170,1220,1530),fill=PASTELS[(variant+3)%len(PASTELS)])
    draw.rounded_rectangle((22,22,W-22,H-22),radius=45,outline="#C89E7E",width=4)
    for x,y,color in ((52,105,MINT),(1005,180,PEACH),(48,1270,YELLOW),(1015,1090,BLUE)):
        draw.ellipse((x-10,y-10,x+10,y+10),fill=color,outline=BROWN,width=2)
        draw.arc((x-25,y-4,x+5,y+32),200,330,fill=BROWN,width=3)
    return draw


def _header(draw, page: dict):
    draw.text((56,48),"FITNESS · GUIDE",font=_font(22,True),fill=BROWN)
    draw.rounded_rectangle((922,42,1018,94),radius=24,fill=BROWN)
    draw.text((970,68),f"{int(page.get('page_no',1)):02d}",font=_font(22,True),fill=WHITE,anchor="mm")
    badge=str(page.get("badge") or "实用指南")[:12]
    draw.rounded_rectangle((56,115,56+max(150,len(badge)*34),166),radius=25,fill=YELLOW,outline=BROWN,width=2)
    draw.text((76,127),badge,font=_font(25,True),fill=INK)
    title=str(page.get("title") or "本页重点")
    _draw_text(draw,(56,184),title,_font(60,True),INK,930,2,4)
    subtitle=str(page.get("subtitle") or "")
    if subtitle:
        _draw_text(draw,(58,307),subtitle,_font(27),CORAL,900,1)


def _footer(draw):
    draw.line((58,1360,1022,1360),fill="#D8BFA9",width=2)
    draw.text((58,1380),"AI 辅助创作 · 发布前请核对训练条件与身体反馈",font=_font(20),fill=BROWN)


def _module_title(draw, box, title: str, color: str):
    x1,y1,x2,_=box
    width=min(x2-x1-40,max(170,len(title)*38+60))
    draw.rounded_rectangle((x1+18,y1-4,x1+18+width,y1+49),radius=26,fill=color,outline=BROWN,width=2)
    draw.text((x1+18+width/2,y1+22),title or "本页重点",font=_font(27,True),fill=INK,anchor="mm")


def _icon(draw, center: tuple[int,int], index: int, color: str, label: str=""):
    x,y=center
    draw.ellipse((x-34,y-34,x+34,y+34),fill=color,outline=BROWN,width=3)
    glyph=(str(index+1) if index<9 else "•")
    draw.text((x,y),glyph,font=_font(27,True),fill=INK,anchor="mm")
    if label:
        draw.arc((x-18,y+41,x+35,y+76),190,330,fill=BROWN,width=3)


def _items(module: dict) -> list[dict]:
    return [row for row in (module.get("items") or []) if isinstance(row,dict)][:6]


def _render_flow(image,draw,module,box,asset):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or "执行顺序",BLUE)
    rows=_items(module)[:4] or [{"label":"开始","detail":"从最容易执行的一步开始"}]
    top=y1+68; columns=2 if len(rows)>2 else len(rows); lines=(len(rows)+columns-1)//columns
    gap=14; card_w=(x2-x1-24-gap*(columns-1))//max(1,columns)
    card_h=(y2-top-8-gap*(lines-1))//max(1,lines)
    for index,row in enumerate(rows):
        col=index%columns; line=index//columns
        left=x1+12+col*(card_w+gap); yy=top+line*(card_h+gap); right=left+card_w; bottom=yy+card_h
        draw.rounded_rectangle((left,yy,right,bottom),radius=24,fill=WHITE,outline="#D9BEA9",width=2)
        art_w=min(176,max(108,round(card_w*.38)))
        art=(left+10,yy+10,left+art_w,min(bottom-10,yy+card_h-10))
        if not _paste_crop(image,asset,art,segment=index,total=len(rows),radius=18,contain=True,
                           grid_columns=2 if len(rows)==4 else 0):
            _icon(draw,((art[0]+art[2])//2,(art[1]+art[3])//2),index,PASTELS[index%len(PASTELS)])
        text_x=art[2]+16; text_w=max(100,right-text_x-12)
        draw.text((text_x,yy+22),str(row.get("label") or "")[:16],font=_font(23,True),fill=INK)
        value=str(row.get("value") or "").strip()
        detail_y=yy+61
        if value:
            chip_w=min(text_w,max(90,len(value)*24))
            draw.rounded_rectangle((text_x,detail_y,text_x+chip_w,detail_y+32),radius=16,fill=PASTELS[index%len(PASTELS)])
            draw.text((text_x+10,detail_y+5),value,font=_font(17,True),fill=INK); detail_y+=40
        _draw_text(draw,(text_x,detail_y),row.get("detail") or row.get("cue") or "",
                   _font(18),BROWN,text_w,3,3)


def _column_rows(module: dict, mistake: bool=False) -> list[dict]:
    columns=[c for c in (module.get("columns") or []) if isinstance(c,dict)][:2]
    if columns:
        return columns
    rows=_items(module)
    midpoint=max(1,(len(rows)+1)//2)
    titles=("动作设置","纠正要点") if mistake else ("先看条件","再做选择")
    return [{"title":titles[0],"items":[x.get("detail") or x.get("label") for x in rows[:midpoint]]},
            {"title":titles[1],"items":[x.get("detail") or x.get("label") for x in rows[midpoint:]]}]


def _render_compare(image,draw,module,box,asset,mistake=False):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or ("这样改更稳" if mistake else "对照选择"),PEACH)
    columns=_column_rows(module,mistake); gap=22; top=y1+66; width=(x2-x1-gap-24)//2
    colors=("#F6D6D0",MINT) if mistake else (BLUE,YELLOW)
    for idx,column in enumerate(columns[:2]):
        left=x1+12+idx*(width+gap); right=left+width
        draw.rounded_rectangle((left,top,right,y2-8),radius=30,fill=WHITE,outline=BROWN,width=3)
        draw.rounded_rectangle((left+12,top+12,right-12,top+62),radius=24,fill=colors[idx])
        marker=str(idx+1)
        draw.text((left+38,top+37),marker,font=_font(26,True),fill=CORAL if idx==0 else INK,anchor="mm")
        draw.text((left+70,top+25),str(column.get("title") or f"方案{idx+1}")[:16],font=_font(25,True),fill=INK)
        items=[str(x) for x in (column.get("items") or [])][:5]
        module_h=y2-top
        if asset and module_h>210:
            art_h=max(72,min(150,module_h-145))
            _paste_crop(image,asset,(left+16,top+76,right-16,top+76+art_h),segment=idx,total=2,radius=22,focus_fill=True)
            start=top+88+art_h
        else:
            start=top+82
        for row_index,value in enumerate(items):
            yy=start+row_index*55
            if yy>y2-50: break
            draw.ellipse((left+22,yy+8,left+38,yy+24),fill=colors[idx])
            _draw_text(draw,(left+49,yy),value,_font(22),INK,width-70,2,3)


def _render_actions(image,draw,module,box,asset):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or "动作卡",YELLOW)
    rows=_items(module)[:4] or [{"label":"开始行动","detail":"选择一个最容易完成的动作"}]
    columns=2 if len(rows)>1 else 1; lines=(len(rows)+columns-1)//columns
    gap=14; top=y1+66; width=(x2-x1-gap*(columns-1)-24)//columns
    card_h=(y2-top-8-gap*(lines-1))//max(1,lines)
    for idx,row in enumerate(rows):
        col=idx%columns; line=idx//columns
        left=x1+12+col*(width+gap); yy=top+line*(card_h+gap); right=left+width; bottom=yy+card_h
        draw.rounded_rectangle((left,yy,right,bottom),radius=26,fill=WHITE,outline=BROWN,width=3)
        art_h=max(68,min(142,round(card_h*.50)))
        art_bottom=yy+10+art_h
        if not _paste_crop(image,asset,(left+10,yy+10,right-10,art_bottom),segment=idx,total=len(rows),radius=20,contain=True,
                           grid_columns=2 if len(rows)>=3 else 0):
            _icon(draw,((left+right)//2,yy+10+art_h//2),idx,PASTELS[idx])
        label_y=art_bottom+9
        draw.text(((left+right)//2,label_y),str(row.get("label") or "")[:16],font=_font(24,True),fill=INK,anchor="ma")
        value=str(row.get("value") or row.get("tag") or "").strip()[:22]
        detail_y=label_y+38
        if value:
            chip_bottom=min(bottom-54,detail_y+32)
            if chip_bottom>detail_y:
                draw.rounded_rectangle((left+24,detail_y,right-24,chip_bottom),radius=16,fill=PASTELS[idx%len(PASTELS)])
                draw.text(((left+right)//2,(detail_y+chip_bottom)//2),value,font=_font(17,True),fill=INK,anchor="mm")
                detail_y=chip_bottom+7
        if detail_y<bottom-24:
            _draw_text(draw,((left+right)//2,detail_y),row.get("detail") or row.get("cue") or "",
                       _font(18),BROWN,width-28,2,3,"ma")


def _render_grid(image,draw,module,box,asset):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or "关键细节",MINT)
    rows=_items(module)[:4] or [{"label":"重点","detail":"先把一件事做清楚"}]
    columns=2; gap=16; top=y1+66; card_w=(x2-x1-24-gap)//2
    card_h=max(100,(y2-top-18-gap)//2)
    for idx,row in enumerate(rows):
        col=idx%columns; line=idx//columns
        left=x1+12+col*(card_w+gap); yy=top+line*(card_h+gap)
        right=left+card_w; bottom=min(y2-8,yy+card_h)
        draw.rounded_rectangle((left,yy,right,bottom),radius=26,fill=WHITE,outline=BROWN,width=3)
        art=(left+14,yy+14,left+116,min(bottom-14,yy+116))
        if not _paste_crop(image,asset,art,segment=idx,total=max(1,len(rows)),radius=20,contain=True):
            _icon(draw,((art[0]+art[2])//2,(art[1]+art[3])//2),idx,PASTELS[idx])
        text_x=left+132
        draw.text((text_x,yy+20),str(row.get("label") or "")[:18],font=_font(24,True),fill=INK)
        _draw_text(draw,(text_x,yy+58),row.get("detail") or row.get("value") or "",
                   _font(20),BROWN,right-text_x-18,3,3)
        cue=str(row.get("cue") or "")
        if cue and bottom-yy>150:
            _draw_text(draw,(text_x,bottom-50),cue,_font(18),CORAL,right-text_x-18,1)


def _render_checklist(image,draw,module,box,asset):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or "行动清单",YELLOW)
    rows=_items(module)[:6] or [{"label":"选择一项开始","detail":"完成后再逐步增加"}]
    top=y1+64; columns=2 if len(rows)>3 else 1; rows_per=(len(rows)+columns-1)//columns
    col_w=(x2-x1-18*(columns-1)-24)//columns
    row_h=max(66,min(116,(y2-top-12)//max(1,rows_per)))
    for idx,row in enumerate(rows):
        col=idx//rows_per; line=idx%rows_per
        left=x1+12+col*(col_w+18); yy=top+line*row_h; right=left+col_w
        draw.rounded_rectangle((left,yy,right,min(y2-8,yy+row_h-10)),radius=22,fill=WHITE,outline="#D9BEA9",width=2)
        draw.rounded_rectangle((left+18,yy+18,left+55,yy+55),radius=10,fill=MINT,outline=BROWN,width=2)
        draw.line((left+27,yy+37,left+36,yy+46),fill=BROWN,width=4); draw.line((left+36,yy+46,left+49,yy+27),fill=BROWN,width=4)
        draw.text((left+70,yy+15),str(row.get("label") or "")[:18],font=_font(23,True),fill=INK)
        _draw_text(draw,(left+70,yy+50),row.get("detail") or row.get("cue") or "",
                   _font(19),BROWN,col_w-92,2,3)


def _render_timeline(image,draw,module,box,asset):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or "执行时间线",BLUE)
    rows=_items(module)[:5] or [{"label":"第一步","detail":"先建立稳定节奏"}]
    top=y1+72; step=max(82,(y2-top-16)//len(rows)); line_x=x1+86
    draw.line((line_x,top+20,line_x,min(y2-24,top+(len(rows)-1)*step+20)),fill="#A9C9D7",width=8)
    for idx,row in enumerate(rows):
        yy=top+idx*step
        _icon(draw,(line_x,yy+20),idx,PASTELS[idx%len(PASTELS)])
        draw.text((line_x+58,yy),str(row.get("label") or "")[:18],font=_font(24,True),fill=INK)
        _draw_text(draw,(line_x+58,yy+38),row.get("detail") or row.get("value") or "",
                   _font(20),BROWN,x2-line_x-86,2,3)


def _render_faq(image,draw,module,box,asset):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or "常见问题",PEACH)
    rows=_items(module)[:4] or [{"label":"怎么开始？","detail":"从最容易执行的一步开始"}]
    top=y1+64; row_h=max(88,(y2-top-10)//len(rows))
    for idx,row in enumerate(rows):
        yy=top+idx*row_h
        draw.rounded_rectangle((x1+12,yy,x2-12,min(y2-8,yy+row_h-12)),radius=24,fill=WHITE,outline="#D9BEA9",width=2)
        draw.ellipse((x1+30,yy+18,x1+78,yy+66),fill=PASTELS[idx%len(PASTELS)],outline=BROWN,width=2)
        draw.text((x1+54,yy+42),"?",font=_font(25,True),fill=INK,anchor="mm")
        draw.text((x1+96,yy+16),str(row.get("label") or "")[:18],font=_font(23,True),fill=INK)
        _draw_text(draw,(x1+96,yy+51),row.get("detail") or row.get("cue") or "",
                   _font(20),BROWN,x2-x1-130,2,3)


def _render_formula(image,draw,module,box,asset):
    x1,y1,x2,y2=box; _module_title(draw,box,module.get("title") or "组合公式",MINT)
    rows=_items(module)[:4] or [{"label":"基础","detail":"先满足最重要的条件"}]
    top=y1+70; gap=18; width=(x2-x1-24-gap*(len(rows)-1))//len(rows)
    for idx,row in enumerate(rows):
        left=x1+12+idx*(width+gap); right=left+width
        draw.rounded_rectangle((left,top,right,y2-10),radius=28,fill=WHITE,outline=BROWN,width=3)
        _icon(draw,((left+right)//2,top+66),idx,PASTELS[idx%len(PASTELS)])
        draw.text(((left+right)//2,top+118),str(row.get("label") or "")[:16],font=_font(23,True),fill=INK,anchor="ma")
        _draw_text(draw,((left+right)//2,top+156),row.get("detail") or row.get("value") or "",
                   _font(19),BROWN,width-20,3,3,"ma")
        if idx<len(rows)-1:
            draw.text((right+gap/2,top+82),"+",font=_font(31,True),fill=CORAL,anchor="mm")


def _legacy_modules(page: dict) -> list[dict]:
    rows=[{"label":str(value)[:18],"detail":str(value)[:46]} for value in (page.get("bullets") or [])]
    return [{"module_id":f"p{page.get('page_no',0)}_legacy","type":"fact_grid","title":"本页重点","items":rows,"columns":[]}] if rows else []


def _render_module(image,draw,module,box,asset):
    kind=module.get("type") or "fact_grid"
    if kind=="step_flow": _render_flow(image,draw,module,box,asset)
    elif kind=="comparison": _render_compare(image,draw,module,box,asset)
    elif kind=="action_cards": _render_actions(image,draw,module,box,asset)
    elif kind=="mistake_fix": _render_compare(image,draw,module,box,asset,True)
    elif kind=="timeline": _render_timeline(image,draw,module,box,asset)
    elif kind=="formula": _render_formula(image,draw,module,box,asset)
    elif kind=="faq": _render_faq(image,draw,module,box,asset)
    elif kind in ("checklist","summary"): _render_checklist(image,draw,module,box,asset)
    else: _render_grid(image,draw,module,box,asset)


def _cover_direction(asset_plan: dict, task: dict) -> dict:
    direction_id=str(task.get("cover_direction_id") or "")
    directions=[row for row in asset_plan.get("cover_directions",[]) if isinstance(row,dict)]
    return next((row for row in directions if str(row.get("id") or "")==direction_id),{})


def _cover_fallback(image: Image.Image, draw, box: tuple[int,int,int,int], variant: int):
    draw.rounded_rectangle(box,radius=42,fill=PASTELS[variant%len(PASTELS)],outline=BROWN,width=4)
    x1,y1,x2,y2=box
    for idx in range(3):
        cx=x1+round((idx+1)*(x2-x1)/4); cy=(y1+y2)//2+(idx%2)*70-35
        _icon(draw,(cx,cy),idx,PASTELS[(idx+2)%len(PASTELS)])
        if idx<2:
            nx=x1+round((idx+2)*(x2-x1)/4)
            draw.line((cx+38,cy,nx-42,cy),fill=BROWN,width=5)


def _render_cover(page: dict, image: Image.Image, draw, asset: Image.Image | None,
                  variant: int=0, direction: dict | None=None):
    """Render one of three genuinely different cover systems.

    Cover Agent decides the text zone and visual direction; this renderer owns
    reliable Chinese typography.  The old renderer changed only the asset while
    keeping one fixed card, which made all three concepts look identical.
    """
    direction=direction or {}
    zone=str(direction.get("text_zone") or "")
    layout=int(direction.get("layout_variant",variant) or 0)%3
    if zone=="top_copy": layout=1
    elif zone=="left_copy": layout=2
    elif zone=="bottom_copy": layout=0

    badge=str(page.get("badge") or "新手指南")[:12]
    title=str(page.get("title") or "健身指南")
    subtitle=str(page.get("subtitle") or "保存下来照着做")
    direction_name=str(direction.get("name") or "")[:12]

    # 01: editorial hero with a strong bottom information band.
    if layout==0:
        navy="#102D3C"; teal="#29B8A6"; coral="#F07A5A"; cream="#FFF8E9"
        draw.rectangle((0,0,W,H),fill=navy)
        art_box=(32,32,1048,936)
        if not _paste_crop(image,asset,art_box,radius=42):
            _cover_fallback(image,draw,art_box,variant)
        draw.rounded_rectangle((32,835,1048,1342),radius=48,fill=navy,outline=teal,width=5)
        label=direction_name or badge
        draw.rounded_rectangle((76,884,76+max(190,len(label)*34),942),radius=29,fill=coral)
        draw.text((96,897),label,font=_font(27,True),fill=cream)
        lines=_wrap(draw,title,_font(76,True),900,3)
        draw.multiline_text((76,976),"\n".join(lines),font=_font(76,True),fill=cream,spacing=7)
        _draw_text(draw,(80,1208),subtitle,_font(29,True),teal,870,2,4)
        draw.text((80,1300),"一台设备 · 蹲 / 拉 / 推",font=_font(22,True),fill="#CDEDE7")

    # 02: warm guide cover with copy first and an immersive lower scene.
    elif layout==1:
        cream="#FFF4DF"; sage="#A9BE91"; terra="#C9694B"; brown="#493426"
        draw.rectangle((0,0,W,H),fill=cream)
        art_box=(38,360,1042,1342)
        if not _paste_crop(image,asset,art_box,radius=48):
            _cover_fallback(image,draw,art_box,variant)
        draw.rounded_rectangle((48,48,1032,455),radius=48,fill=cream,outline=sage,width=5)
        draw.rounded_rectangle((82,82,82+max(180,len(badge)*34),138),radius=28,fill=sage)
        draw.text((102,94),badge,font=_font(27,True),fill=brown)
        lines=_wrap(draw,title,_font(70,True),880,2)
        draw.multiline_text((82,174),"\n".join(lines),font=_font(70,True),fill=brown,spacing=4)
        _draw_text(draw,(86,330),subtitle,_font(27,True),terra,850,1)
        if direction_name:
            draw.rounded_rectangle((720,388,1002,442),radius=27,fill=terra)
            draw.text((861,415),direction_name,font=_font(23,True),fill=WHITE,anchor="mm")

    # 03: modern split cover with a permanent left copy column.
    else:
        cobalt="#1759C7"; lemon="#F3D33F"; brick="#B84C3A"; cool="#EEF2F5"; dark="#17304F"
        draw.rectangle((0,0,W,H),fill=cool)
        art_box=(390,38,1042,1342)
        if not _paste_crop(image,asset,art_box,radius=42):
            _cover_fallback(image,draw,art_box,variant)
        draw.rounded_rectangle((38,38,492,1342),radius=42,fill=cobalt)
        draw.rectangle((458,38,492,1342),fill=lemon)
        label=direction_name or badge
        draw.rounded_rectangle((72,84,446,148),radius=30,fill=lemon)
        draw.text((259,116),label,font=_font(26,True),fill=dark,anchor="mm")
        lines=_wrap(draw,title,_font(61,True),330,5)
        draw.multiline_text((78,220),"\n".join(lines),font=_font(61,True),fill=WHITE,spacing=11)
        draw.line((78,760,410,760),fill=lemon,width=7)
        _draw_text(draw,(78,802),subtitle,_font(28,True),"#DDE9FF",320,4,7)
        draw.rounded_rectangle((76,1134,420,1288),radius=30,fill=brick)
        draw.text((248,1180),"新手可执行",font=_font(25,True),fill=WHITE,anchor="mm")
        draw.text((248,1236),"器械少也能练全身",font=_font(22),fill=WHITE,anchor="mm")


def render(outline: dict, visual_spec: dict, asset_plan: dict, out_dir: str | Path) -> list[str]:
    target=Path(out_dir); target.mkdir(parents=True,exist_ok=True)
    paths=[]
    for page in outline.get("pages",[]):
        no=int(page.get("page_no") or len(paths)+1)
        image=Image.new("RGB",(W,H),BG); draw=_background(image,no)
        selected=asset_plan.get("selected_cover_asset_id","") if no==1 else ""
        asset=_load_page_asset(target,no,asset_plan,selected)
        if page.get("type")=="cover":
            task=next((row for row in asset_plan.get("asset_tasks",[]) if row.get("asset_id")==selected),{})
            direction=_cover_direction(asset_plan,task)
            _render_cover(page,image,draw,asset,int(task.get("variant_index") or 0),direction)
        else:
            _header(draw,page)
            modules=list(page.get("modules") or _legacy_modules(page))[:4]
            if not modules:
                modules=[{"type":"fact_grid","title":"本页重点","items":[{"label":"先从一步开始","detail":"根据自己的基础逐步调整"}]}]
            top=365; bottom=1338; gap=20
            weights={"action_cards":1.15,"comparison":1.12,"mistake_fix":1.0,"step_flow":1.15,
                     "fact_grid":1.05,"timeline":.9,"formula":1.0,"faq":.85,"checklist":.9,"summary":.9}
            def module_weight(module: dict) -> float:
                kind=str(module.get("type") or "")
                count=len(module.get("items") or [])
                if kind=="action_cards" and count>=4: return 1.55
                if kind=="step_flow" and count>=4: return 1.4
                return weights.get(kind,1.0)
            total=sum(module_weight(module) for module in modules)
            available=bottom-top-gap*(len(modules)-1); cursor=top
            for index,module in enumerate(modules):
                height=available-cursor+top if index==len(modules)-1 else round(available*module_weight(module)/total)
                box=(52,cursor,1028,min(bottom,cursor+height))
                # One generated page asset may contain multiple separated
                # vignettes. Every module may reuse its relevant crop; limiting
                # the asset to the first module left later cards visibly blank.
                _render_module(image,draw,module,box,asset)
                cursor=box[3]+gap
        _footer(draw)
        output=target/f"page_{no:02d}.jpg"
        image.save(output,"JPEG",quality=95,subsampling=0)
        paths.append(str(output))
    return paths


def render_cover_variants(outline: dict, visual_spec: dict, asset_plan: dict,
                          out_dir: str | Path) -> list[dict]:
    """Render all three cover backgrounds with reliable local Chinese copy."""
    target=Path(out_dir); target.mkdir(parents=True,exist_ok=True)
    cover_page=next((page for page in outline.get("pages",[]) if page.get("page_no")==1),None)
    cover_visual=next((page for page in visual_spec.get("pages",[]) if page.get("page_no")==1),None)
    if not cover_page or not cover_visual:
        return []
    tasks=[task for task in asset_plan.get("asset_tasks",[]) if task.get("type")=="cover_variant"]
    directions=asset_plan.get("cover_directions",[]); results=[]
    for index,task in enumerate(tasks[:3]):
        raw=Path(str(task.get("output_path") or "")); absolute=raw if raw.is_absolute() else (target/raw).resolve()
        one_task={**task,"output_path":str(absolute)}
        one_plan={**asset_plan,"selected_cover_asset_id":task.get("asset_id"),"asset_tasks":[one_task]}
        generated=render({"pages":[cover_page]},{"pages":[cover_visual]},one_plan,target)
        if not generated: continue
        destination=target/f"cover_variant_{index+1:02d}.jpg"
        shutil.copy2(generated[0],destination)
        direction=directions[index] if index<len(directions) else {}
        results.append({"index":index,"path":str(destination),"asset_id":task.get("asset_id"),
                        "direction_id":direction.get("id"),"name":direction.get("name"),
                        "reason":direction.get("reason"),"layout_variant":direction.get("layout_variant",index)})
    selected=int(asset_plan.get("selected_cover_index") or 0)
    if results:
        selected=max(0,min(selected,len(results)-1)); shutil.copy2(results[selected]["path"],target/"page_01.jpg")
    return results
