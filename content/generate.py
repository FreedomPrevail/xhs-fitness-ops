#!/usr/bin/env python3
"""L1 内容生成 —— 按权重抽样选题,调 Claude CLI 生成小红书文案。

流程:
  1. 读本地 data/topic_weights.json,按 weight 加权抽样(epsilon 概率随机探索)选出今日选题
  2. 可选:opencli xiaohongshu search 拉同类竞品热帖作为参考(注入 prompt)
  3. 调 Claude CLI 生成 标题+正文+封面提示词
  4. 保险合规硬校验(命中红线词则重生成/丢弃)
  5. 落盘为草稿 JSON 到 content/drafts/,供 publish.sh 发布

薄荷工坊(Mint Atelier)可作为可视化替代:它同样支持 Claude Code CLI 作后端,
把这里的 persona/选题喂进去,人工在工作台里精修封面图。两条路产出同构的草稿。
"""
import json
import random
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml  # pip install pyyaml
from compliance import operation_policy

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config"
DRAFTS = ROOT / "content" / "drafts"

# Windows 下 opencli/claude 均为 npm .cmd 包装,subprocess 需解析真实路径
OPENCLI = shutil.which("opencli") or "opencli"
CLAUDE = shutil.which("claude") or "claude"


def load(name):
    p = CFG / name
    if name.endswith(".json"):
        return json.loads(p.read_text(encoding="utf-8"))
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def pick_topic(weights: dict) -> dict:
    """按最近一次完整Pipeline提交的35/30/30/5 Topic Score抽样。"""
    from analyze import analyze
    if not analyze.has_committed_score(weights):
        raise RuntimeError("尚无完整流程提交的Topic Score，请先运行一键选题科学化")
    topics = weights["topics"]
    if random.random() < weights.get("epsilon", 0.2):
        return random.choice(topics)                       # explore
    w = [t["final_score"] for t in topics]
    return random.choices(topics, weights=w, k=1)[0]        # exploit


def fetch_references(topic: dict, limit: int = 5) -> list:
    """用小红书 CLI 拉同类热帖作为写作参考(可选,失败不阻塞)。"""
    if operation_policy.blocked_result("platform_search"):
        return []
    kw = topic["label"]
    try:
        proc = subprocess.run(
            [OPENCLI, "xiaohongshu", "search", kw, "--limit", str(limit), "-f", "json"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        data = json.loads((proc.stdout or "").strip() or "[]")
        if isinstance(data, dict) and data.get("ok") is False:
            return []  # 未登录主站等错误,跳过参考,不阻塞生成
        return data if isinstance(data, list) else data.get("results", [])
    except Exception as e:
        print(f"  (参考抓取跳过: {e})", file=sys.stderr)
        return []


def build_prompt(topic, persona, insurance, refs) -> str:
    """明确的祈使指令 + 规定输出用固定分隔符,便于 Python 端解析。"""
    is_insurance = topic["category"] in ("health_risk", "insurance_soft")
    redlines = insurance["compliance_redlines"]
    ref_titles = "、".join(r.get("title", "") for r in refs[:5]) or "(无)"
    aigc = "正文结尾标注「本文由AI辅助创作」。" if persona["compliance"]["aigc_label"] else ""
    # 结构照搬手动验证成功的版本:任务在前、格式在末尾(措辞自然,不加"系统指令"式硬话)
    lines = [
        f"任务:立即写一篇小红书居家健身/健康类图文笔记,选题「{topic['label']}」,直接产出不要反问。",
        f"账号定位:{persona['account']['positioning']};语气:{'; '.join(persona['account']['tone'])}。",
        f"标题参考句式:{topic['title_patterns']};同类热帖参考:{ref_titles}。",
    ]
    if is_insurance:
        product = insurance.get("product") or {}
        brand = str(product.get("brand") or "").strip()
        brand_note = f"{brand}仅作信息分享自然提及," if brand else "如需提及具体产品，只能依据已核验资料，"
        lines.append(
            f"合规(必须遵守):禁用词{redlines['forbidden_phrases']};"
            f"{'; '.join(redlines['rules'])};结尾附免责声明:{redlines['disclaimer']}。"
            f"{brand_note}不硬广、不报价、不承诺理赔。")
    body_note = "正文分段带emoji给可执行步骤" + ("；" + aigc if aigc else "")
    lines.append(
        "输出格式(严格遵守,否则无法使用):\n"
        "- 你只需输出文本,不要创建、写入或保存任何文件,不要提及文件/权限。\n"
        "- 回复的第一个字符必须是 ===TITLE===,前面不要任何开场白、说明或寒暄。\n"
        "- 四个分隔行 ===TITLE=== / ===BODY=== / ===TOPICS=== / ===COVER=== 各占一行,必须齐全。\n"
        "- 标题写在 ===TITLE=== 下一行:一句完整的话(≤20字有钩子),不要用【】** 等符号包裹,不要写成动作清单或编号条目。\n"
        f"- 正文写在 ===BODY=== 下:{body_note}。\n"
        "- ===TOPICS=== 下:3-5个话题标签,逗号分隔,不含#。\n"
        "- ===COVER=== 下:一句封面文字。\n"
        "示例:\n===TITLE===\n新手健身别踩这3个坑\n===BODY===\n姐妹们……\n===TOPICS===\n健身,减脂,新手\n===COVER===\n避开3个坑")
    return "\n".join(lines)


import re as _re


def _section(text: str, name: str) -> str:
    """从 ===NAME=== 分隔的文本里取某段。"""
    marks = ["TITLE", "BODY", "TOPICS", "COVER"]
    start_tag = f"==={name}==="
    i = text.find(start_tag)
    if i < 0:
        return ""
    i += len(start_tag)
    end = len(text)
    for m in marks:
        j = text.find(f"==={m}===", i)
        if j != -1:
            end = min(end, j)
    return text[i:end].strip()


def parse_freeform(raw: str) -> dict:
    """降级解析:claude 不守 ===分隔符=== 时,从它惯用的 markdown 结构抽字段。

    覆盖:【封面标题】/【正文】、# 标题、**加粗标题**、行首 emoji 标题、
    以及 #话题标签。尽量从自由文本里恢复出结构化字段。
    """
    lines = raw.splitlines()
    # 去掉开头的废话行(claude 常先说"没权限写文件/直接展示如下"之类)
    _junk = ("产出", "权限", "写入", "文件", "拒绝", "展示如下", "直接输出", "直接在这里",
             "内容如下", "笔记内容", "output", "permission", "denied", "wasn't approved")
    while lines and (not lines[0].strip() or lines[0].strip() in ("---", "```")
                     or lines[0].strip().endswith(("笔记。", "这里。", "immediately.", "here.", "：", ":"))
                     or any(j in lines[0].lower() for j in [x.lower() for x in _junk])):
        lines.pop(0)
    body = "\n".join(lines).strip()

    def clean(s: str) -> str:
        return _re.sub(r"[#*>【】\[\]`]", "", s or "").strip()

    def looks_like_tag_string(s: str) -> bool:
        """判断是否是话题标签串(如'居家健身 健身新手 减脂',或含#),不是真标题。"""
        if "#" in s:
            return True
        parts = s.split()
        # 多个短词(≤5字)用空格连 → 像标签串,不是句子
        return len(parts) >= 3 and all(len(p) <= 5 for p in parts)

    title = ""
    blines = body.splitlines()

    # 规则1(最高优先级):加粗方括号 **【实际标题】** —— 小红书封面标题最常见格式
    for m in _re.finditer(r"\*\*[【\[](.+?)[】\]]\*\*", body):
        c = clean(m.group(1))
        if c and not looks_like_tag_string(c) and not any(w in c for w in ("标题", "正文", "话题标签")):
            title = c
            break

    # 规则2:含"标题"二字的标记行,取其后首个实质内容行
    if not title:
        for idx, ln in enumerate(blines):
            if _re.search(r"[【\[][^】\]]*标题[^】\]]*[】\]]", ln) or ("封面" in ln and "标题" in ln):
                for nxt in blines[idx + 1:]:
                    raw_nxt = nxt.strip()
                    if not raw_nxt or raw_nxt.startswith(("#", "-", "=")):
                        continue
                    c = clean(raw_nxt)
                    if c and not looks_like_tag_string(c):
                        title = c
                        break
                break

    # 规则3:首个 markdown # 标题(排除结构词)
    if not title:
        m = _re.search(r"^#{1,3}\s*(.+)$", body, _re.M)
        if m and not any(w in m.group(1) for w in ("笔记", "正文", "标题")) and not looks_like_tag_string(clean(m.group(1))):
            title = clean(m.group(1))

    # 规则4:首个加粗、不含结构词、不像标签串的
    if not title:
        for m in _re.finditer(r"\*\*(.+?)\*\*", body):
            c = clean(m.group(1))
            if c and not looks_like_tag_string(c) and not any(w in c for w in ("标题", "正文", "封面", "话题")):
                title = c
                break
    title = title[:30]

    # 话题标签:全文所有 #xxx(排除标题误抓)
    topics = _re.findall(r"#([一-龥A-Za-z0-9]+)", raw)
    # 封面:含"封面"的引用段
    cover = ""
    m = _re.search(r"封面[^\n]*\n+((?:\s*>.*\n?)+)", raw)
    if m:
        cover = _re.sub(r"[>\s]+", " ", m.group(1)).strip()[:80]

    return {
        "title": title or "(待补标题)",
        "body": body,
        "topics": topics[:5],
        "cover_text": cover,
    }


def call_claude(prompt: str) -> dict:
    """调 Claude CLI 取纯文本输出,Python 端按分隔符解析成结构化字段。

    实测该 claude CLI 的 -p 输出为纯文本正文(无 JSON 外层),故不依赖
    --output-format json,改用固定分隔符 + 本地解析,更稳。
    """
    # --bare 跳过 hooks/plugin;--tools 空列表=不给任何工具,强制纯文本生成
    # (否则该 CLI 会尝试 Write 并输出"需要授权"开场白、忽略格式要求)
    proc = subprocess.run(
        [CLAUDE, "-p", prompt, "--bare", "--tools", ""],
        capture_output=True, text=True, timeout=240,
        encoding="utf-8", errors="replace")
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError(f"Claude 无输出。stderr: {proc.stderr[:300]}")
    title = _section(raw, "TITLE")
    body = _section(raw, "BODY")
    topics = [t.strip() for t in _section(raw, "TOPICS").replace("，", ",").split(",") if t.strip()]
    cover = _section(raw, "COVER")
    if title and body:            # 严格格式命中
        return {"title": title, "body": body, "topics": topics, "cover_text": cover}
    return parse_freeform(raw)    # 降级:从自由 markdown 结构抽字段


def check_compliance(content: dict, insurance: dict) -> list[str]:
    """保险合规硬校验:返回命中的红线词列表(空=通过)。"""
    text = f"{content.get('title','')}\n{content.get('body','')}"
    hits = [w for w in insurance["compliance_redlines"]["forbidden_phrases"] if w in text]
    return hits


def postprocess(content: dict, topic: dict, persona: dict, insurance: dict) -> dict:
    """落盘前清洗与强制合规(不依赖模型自觉):
      1. 切除 claude 尾部的对话残留(如"需要我再出一版...吗?")
      2. 强制补 AIGC 标注(小红书新规硬要求)
      3. 保险/健康类强制补免责声明
    """
    import re
    body = content.get("body", "").strip()

    # 1) 切尾部对话残留:从末尾往前,删掉以问句/"需要我"/"可以帮你"等开头的收尾段
    tail_patterns = ("需要我", "要不要", "你想", "可以帮你", "可以直接给你", "如果需要", "希望这")
    blines = body.splitlines()
    while blines:
        last = _re.sub(r"[#*>【】\s]", "", blines[-1])
        if not last:
            blines.pop(); continue
        if last.endswith(("?", "?")) and any(p in last for p in tail_patterns):
            blines.pop(); continue
        if any(last.startswith(p) for p in tail_patterns):
            blines.pop(); continue
        break
    body = "\n".join(blines).strip()

    # 2) 强制 AIGC 标注
    if persona["compliance"]["aigc_label"] and "AI" not in body:
        body += "\n\n———\n本文由AI辅助创作 🤖"

    # 3) 保险/健康类强制免责声明
    if topic["category"] in ("health_risk", "insurance_soft"):
        disc = insurance["compliance_redlines"]["disclaimer"]
        if disc[:6] not in body and "合同条款" not in body:
            body += f"\n\n⚠️ {disc}"

    content["body"] = body
    return content


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1, help="生成篇数")
    args = ap.parse_args()

    persona = load("persona.yaml")
    insurance = load("insurance.yaml")
    from analyze import analyze
    weights = analyze.load_weights()
    DRAFTS.mkdir(parents=True, exist_ok=True)

    for i in range(args.count):
        topic = pick_topic(weights)
        print(f"[{i+1}/{args.count}] 选题: {topic['label']} (weight={topic['weight']})")
        refs = fetch_references(topic)
        prompt = build_prompt(topic, persona, insurance, refs)
        content = call_claude(prompt)
        content = postprocess(content, topic, persona, insurance)

        hits = check_compliance(content, insurance)
        if hits:
            print(f"  ✗ 合规校验未通过,命中红线词 {hits},已丢弃。", file=sys.stderr)
            continue

        draft = {
            "topic_id": topic["id"],
            "category": topic["category"],
            "generated_at": date.today().isoformat(),
            **content,
        }
        fn = DRAFTS / f"{date.today().isoformat()}_{topic['id']}_{i}.json"
        fn.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ 草稿已保存: {fn.name}  标题《{content.get('title','')}》")


if __name__ == "__main__":
    main()
