#!/usr/bin/env python3
"""平台时效热度分析(方案A)—— 从健身样本库算"当下什么火"。

三个能力:
  1. platform_heat: 选题关键词的平台热度分
     热度分 = log10(中位点赞) × (1 + 爆款率) × 新鲜度乘数
     不只简单匹配关键词,还分析命中的帖子集合的标题共性(共同词组/句式)
  2. analyze_factors: 爆火因子分析
     对比 top-25% 爆款 vs 其余帖子的标题特征差异
  3. extract_trending_keywords: 从高赞标题中提取今日热搜词组
     2-6 字滑动窗口,点赞加权计数,只过滤纯语义停用词
     "健身""运动"等核心词不再屏蔽——它们是健身赛道的根词,屏蔽了反丢信息
"""
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

try:
    import jieba
except ImportError:  # The dashboard can still open before optional NLP deps are installed.
    class _JiebaFallback:
        @staticmethod
        def add_word(_word):
            return None

        @staticmethod
        def cut(text):
            return re.findall(r"[\u4e00-\u9fff]{2,4}", text or "")
    jieba = _JiebaFallback()

# 向 jieba 注册健身领域专有词,防止被错误切分
_FITNESS_TERMS = [
    "帕梅拉", "暴汗", "跟练", "燃脂", "减脂餐", "低卡", "快瘦",
    "体态", "塑形", "马甲线", "腹肌", "有氧", "无氧", "暴瘦",
    "轻断食", "减脂", "增肌", "拉伸", "核心力量", "跳绳",
    "爬坡", "骑行", "器械", "力量训练", "普拉提", "椭圆机",
    "划船机", "战绳", "壶铃", "波比跳", "开合跳", "平板支撑",
    "低碳水", "高蛋白", "卡路里", "热量缺口", "代谢", "体脂",
    "瘦肚子", "瘦腿", "瘦手臂", "瘦腰", "瘦背", "瘦脸",
    "蜜桃臀", "直角肩", "天鹅颈", "女团腿", "漫画腿",
    "HIIT", "Tabata", "CrossFit", "瑜伽", "冥想",
    "热身", "冷身", "筋膜", "泡沫轴", "拉伸放松",
    "超级组", "递减组", "力竭", "充血", "泵感",
    "干净饮食", "吃干净", "快手减脂", "低卡快手",
    "经典燃脂", "燃脂三部曲", "HIIT暴汗", "暴汗挑战",
    "吃瘦", "低脂", "低卡", "低卡低脂", "低卡减脂",
    "快手减脂汤", "低卡快手减脂", "低卡快手减脂汤",
    "减脂汤", "瘦肚子", "瘦腿", "瘦手臂",
    "减脂期", "两周", "一人食", "掉秤", "生活化", "吃瘦",
    "减脂期吃", "不重样", "保姆级", "练出腹肌", "练全身",
    "划水", "新人", "启动", "启动", "协会", "学会",
]
for _t in _FITNESS_TERMS:
    jieba.add_word(_t)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = ROOT / "data" / "sample.db"

PLATFORM_HEAT_WEIGHTS = {
    "engagement_quality": .40,
    "trend_momentum": .25,
    "high_heat_sample_rate": .20,
    "sample_coverage": .15,
}

# 热搜词提取时的停用词 —— 只过滤纯虚词/语气词/代词/量词,
# "健身""运动""减脂""燃脂"等赛道根词保留,它们是有效的热搜信号
_STOP_WORDS = {
    "一个", "什么", "这个", "那个", "如何", "怎么", "还是", "可以",
    "不用", "没有", "已经", "真的", "就是", "不是", "不要", "不能",
    "感觉", "自己", "为了", "但是", "因为", "所以", "如果", "而且",
    "不过", "只是", "还有", "很多", "一些", "起来", "哈哈哈",
    "一次", "一周", "一天", "半年", "一年", "今天", "明天", "昨天",
    "会", "能", "要", "了", "的", "吗", "吧", "呢", "啊", "呀",
    "哦", "哈", "嗯", "嘛", "啦", "哟", "哇", "嘿", "噢",
    "这", "那", "我", "你", "他", "她", "它", "们",
    "在", "和", "与", "或", "很", "都", "也", "就", "才", "又",
    "把", "被", "让", "给", "从", "到", "对", "比", "向", "跟",
    "上", "下", "里", "外", "前", "后", "左", "右", "中",
    "大", "小", "多", "少", "好", "坏", "快", "慢", "高", "低",
    "最", "更", "非常", "特别", "比较", "有点", "稍微",
    "做", "做", "是", "有", "说", "看", "来", "去", "想", "知道",
    "觉得", "应该", "需要", "可能", "可以", "已经", "开始",
    # 分词后新增的纯虚词/量词/单位
    "分钟", "小时", "每天", "每次", "才能", "就能", "只要",
    "不用", "不要", "也能", "还可", "并且", "只会", "还不",
    "一种", "一样", "这么", "那么", "不太", "不过", "而且",
    "不行", "不同", "不了", "不会", "不想", "不敢", "不再",
    "为什么", "怎么样", "什么样", "干嘛", "咋",
    "第", "第", "第", "种", "条", "个", "岁", "斤", "块", "元",
    "卡", "千", "万", "亿", "只", "次", "倍", "步", "期", "号",
    "手", "身", "款", "招", "类", "系列", "入门", "适合",
    "真的", "真的", "实在", "绝对", "必须", "一定", "简直",
    "搞定", "拿捏", "安排", "安排", "收藏", "点赞", "关注",
    "热门", "推荐", "发现", "搜索", "搜索", "查看", "点击",
    "立即", "马上", "赶紧", "赶紧", "记得", "赶紧", "抓紧",
    "到底", "终于", "果然", "反正", "反正", "基本上",
    "篇", "篇文章", "视频", "笔记", "内容", "分享", "分享",
    "先", "再", "还", "然后", "然后", "最后", "接着", "另外",
    "其实", "其实", "全部", "部分", "除了", "除了", "包括",
    "属于", "具有", "拥有", "存在", "存在", "包含", "基于",
    "根本", "完全", "彻底", "彻底", "突然", "突然", "偶尔",
    "刚才", "刚刚", "以前", "以后", "刚才", "暂时", "永远",
    "就是", "也是", "还是", "只是", "还是", "不是", "而是",
    "整个", "这种", "那种", "各种", "某种", "其他", "其余",
    "这些", "那些", "哪些", "那样", "直接", "一下", "第一次",
    # 健身语境中的通用词(单独出现无意义,但在复合词中有意义)
    "干净", "改善", "快手", "效果", "挑战", "教程", "真的",
    "适合", "运动", "训练", "动作", "身体", "姿势", "方法",
    "技巧", "秘诀", "干货", "合集", "系列", "计划", "方案",
    "经典", "睡前", "夏天", "冬天", "春天", "秋天", "早上",
    "晚上", "中午", "早晨", "饭后", "饭前", "在家", "在家",
    "女生", "男生", "新手", "老手", "高手", "小白", "达人",
    "好看", "好看", "好穿", "好用", "好吃", "好喝", "好玩",
    "最好", "更好", "刚好", "最好用", "附", "附上", "建议",
    "建议", "推荐", "推荐", "必备", "必入", "必须入", "入手",
    "期吃", "不直", "下肢", "两周", "学会", "太细", "惊人",
    "重样", "黑椒", "一人", "搞懂", "犯困", "轻松", "深度",
    "惊人", "紧致", "消失", "问题", "启动", "划水", "抗阻",
    "瘦", "胖", "粗", "细", "长", "短", "直", "弯", "硬", "软",
    "吃", "喝", "睡", "走", "跑", "跳", "蹲", "站", "坐", "躺",
    "放", "拿", "带", "穿", "脱", "洗", "切", "炒", "煮", "蒸",
    "买", "卖", "花", "省", "赚", "赔", "涨", "跌", "升", "降",
    # 太泛的通用词(单独出现无意义,加入停用词避免成为热搜词)
    "晚餐", "搭配", "多少", "长相", "筷子", "吃饭", "掉落",
    "中国", "美女", "是不是", "这样", "我们", "一根",
    "重要", "小动作", "重要体态",
    "夏日", "备战", "动感",
}


def _days_ago(published_at: str) -> int | None:
    """'2026-05-31' -> 距今天数;解析失败返回 None。"""
    try:
        d = datetime.strptime(published_at.strip()[:10], "%Y-%m-%d").date()
        return (date.today() - d).days
    except Exception:
        return None


def _has_detail_columns(con: sqlite3.Connection) -> bool:
    cols = {r[1] for r in con.execute("PRAGMA table_info(samples)").fetchall()}
    return {"collects_num", "comments_num", "detail_fetched"}.issubset(cols)


def _weighted_interaction(likes: int, collects: int = 0, comments: int = 0, detailed: int = 0) -> float:
    """平台互动信号。详情未抓到时只用点赞；有详情时收藏/评论给予更高价值。"""
    likes = int(likes or 0)
    if not detailed:
        return float(likes)
    return float(likes + 2 * int(collects or 0) + 3 * int(comments or 0))


def _median(values: list[float]) -> float | None:
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return None
    middle = len(clean) // 2
    return clean[middle] if len(clean) % 2 else (clean[middle - 1] + clean[middle]) / 2


def _percentile_rank(value: float | None, baseline: list[float]) -> float | None:
    clean = sorted(float(v) for v in baseline if v is not None)
    if value is None or not clean:
        return None
    if len(clean) == 1:
        return .5
    lower = sum(1 for v in clean if v < float(value))
    equal = sum(1 for v in clean if v == float(value))
    return max(0.0, min(1.0, (lower + .5 * equal) / len(clean)))


def _weighted_available(values: list[tuple[float | None, float]]) -> float | None:
    clean = [(float(v), float(w)) for v, w in values if v is not None]
    denom = sum(w for _, w in clean)
    return sum(v * w for v, w in clean) / denom if denom else None


def _rows_as_dicts(con: sqlite3.Connection, query: str, params=()) -> list[dict]:
    cur = con.execute(query, params)
    names = [x[0] for x in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _sample_columns(con: sqlite3.Connection) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(samples)").fetchall()}


def _select_fields(columns: set[str]) -> str:
    defaults = {
        "title": "''", "author": "''", "keyword": "''", "likes_num": "0",
        "collects_num": "0", "comments_num": "0", "detail_fetched": "0",
        "published_at": "''", "collected": "''", "collected_at": "''",
        "url": "''", "note_id": "''",
    }
    return ",".join((name if name in columns else f"{default} AS {name}")
                    for name, default in defaults.items())


def _dedupe_latest(rows: list[dict]) -> list[dict]:
    out = {}
    for i, row in enumerate(rows):
        key = str(row.get("note_id") or row.get("url") or f"title:{row.get('title')}:{i}")
        old = out.get(key)
        stamp = str(row.get("collected_at") or row.get("collected") or "")
        old_stamp = str((old or {}).get("collected_at") or (old or {}).get("collected") or "")
        if old is None or stamp >= old_stamp:
            out[key] = row
    return list(out.values())


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:19])
        return parsed.replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _growth_signal(con: sqlite3.Connection, identities: set[str], columns: set[str]) -> tuple[float | None, int]:
    """同一帖子跨采集快照的相对增长速度；只有真实重复快照才返回值。"""
    if not identities:
        return None, 0
    fields = _select_fields(columns)
    history = _rows_as_dicts(con, f"SELECT {fields} FROM samples ORDER BY collected,collected_at")
    grouped = {}
    for row in history:
        key = str(row.get("note_id") or row.get("url") or "")
        if not key or key not in identities:
            continue
        # 同一帖子同一天因命中多个关键词产生的重复行不是增长快照。
        round_key=str(row.get("collected") or row.get("collected_at") or "")[:10]
        if not round_key:
            continue
        grouped.setdefault(key, {})[round_key]=row
    rates = []
    for snapshots in grouped.values():
        values=[snapshots[k] for k in sorted(snapshots)]
        if len(values) < 2:
            continue
        first, last = values[0], values[-1]
        t1 = _parse_datetime(first.get("collected_at") or first.get("collected"))
        t2 = _parse_datetime(last.get("collected_at") or last.get("collected"))
        if not t1 or not t2:
            continue
        days = max((t2 - t1).total_seconds() / 86400, 1 / 24)
        a = _weighted_interaction(first.get("likes_num"), first.get("collects_num"),
                                  first.get("comments_num"), first.get("detail_fetched"))
        b = _weighted_interaction(last.get("likes_num"), last.get("collects_num"),
                                  last.get("comments_num"), last.get("detail_fetched"))
        rates.append(max(0.0, (b - a) / max(1.0, a) / days))
    if not rates:
        return None, 0
    relative_daily = sum(rates) / len(rates)
    # 一周相对增长约 10% 映射为中等动量；保持0~1且不伪造缺失。
    return max(0.0, min(1.0, 1 - math.exp(-7 * relative_daily))), len(rates)


def platform_heat(keyword: str, con: sqlite3.Connection | None = None) -> dict:
    """四维 PlatformHeat：40%互动、25%趋势、20%高热率、15%覆盖。"""
    close = False
    if con is None:
        con = sqlite3.connect(DB)
        close = True
    columns = _sample_columns(con)
    fields = _select_fields(columns)
    rows = _rows_as_dicts(
        con, f"SELECT {fields} FROM samples WHERE keyword=? "
             "AND collected=(SELECT MAX(collected) FROM samples WHERE keyword=?)",
        (keyword, keyword))
    if not rows and "title" in columns:
        rows = _rows_as_dicts(
            con, f"SELECT {fields} FROM samples WHERE title LIKE ? "
                 "AND collected=(SELECT MAX(collected) FROM samples) ORDER BY likes_num DESC LIMIT 30",
            (f"%{keyword}%",))
    rows = _dedupe_latest(rows)
    if not rows:
        if close:
            con.close()
        return {"heat": 0.0, "median_likes": 0, "median_interaction": 0,
                "viral_ratio": 0.0, "freshness": 0.0, "n": 0,
                "detail_n": 0, "common_phrases": [], "dimensions": {},
                "data_coverage": 0.0, "missing_dimensions": list(PLATFORM_HEAT_WEIGHTS)}

    global_rows = _dedupe_latest(_rows_as_dicts(
        con, f"SELECT {fields} FROM samples WHERE collected=(SELECT MAX(collected) FROM samples)"))
    titles = [str(r.get("title") or "") for r in rows]
    likes = sorted(int(r.get("likes_num") or 0) for r in rows)
    interactions = [_weighted_interaction(r.get("likes_num"), r.get("collects_num"),
                                          r.get("comments_num"), r.get("detail_fetched")) for r in rows]
    global_interactions = [_weighted_interaction(r.get("likes_num"), r.get("collects_num"),
                                                 r.get("comments_num"), r.get("detail_fetched")) for r in global_rows]
    median_likes = _median(likes) or 0
    median_interaction = _median(interactions) or 0
    engagement_quality = _percentile_rank(median_interaction, global_interactions)
    threshold = sorted(global_interactions)[max(0, int(.75 * (len(global_interactions) - 1)))] if global_interactions else None
    high_heat_rate = (sum(1 for v in interactions if threshold is not None and v >= threshold) / len(interactions)
                      if interactions and threshold is not None else None)
    viral = sum(1 for lk in likes if lk >= 1000) / len(likes)

    days_values = [_days_ago(str(r.get("published_at") or "")) for r in rows]
    observed_days = [d for d in days_values if d is not None]
    freshness = (sum(1 for days in observed_days if -1 <= days <= 30) / len(observed_days)
                 if observed_days else None)
    identities = {str(r.get("note_id") or r.get("url") or "") for r in rows}
    identities.discard("")
    growth_score, growth_n = _growth_signal(con, identities, columns)
    trend_momentum = _weighted_available([(growth_score, .60), (freshness, .40)])

    authors = {str(r.get("author") or "").strip() for r in rows if str(r.get("author") or "").strip()}
    keywords = {str(r.get("keyword") or "").strip() for r in rows if str(r.get("keyword") or "").strip()}
    rounds = {str(r.get("collected") or "").strip() for r in rows if str(r.get("collected") or "").strip()}
    sample_coverage = (.40 * min(1.0, len(rows) / 12)
                       + .20 * (len(authors) / len(rows) if rows else 0)
                       + .20 * min(1.0, len(keywords) / 3)
                       + .20 * min(1.0, max(len(rounds), growth_n + 1) / 3))
    dimensions = {
        "engagement_quality": engagement_quality,
        "trend_momentum": trend_momentum,
        "high_heat_sample_rate": high_heat_rate,
        "sample_coverage": sample_coverage,
    }
    available = {k: v for k, v in dimensions.items() if v is not None}
    available_weight = sum(PLATFORM_HEAT_WEIGHTS[k] for k in available)
    heat = (sum(PLATFORM_HEAT_WEIGHTS[k] * float(v) for k, v in available.items()) / available_weight
            if available_weight else 0.0)
    detail_n = sum(1 for r in rows if int(r.get("detail_fetched") or 0) == 1)
    common_phrases = _extract_common_phrases(titles, top_n=5)
    if close:
        con.close()
    return {
        "heat": round(heat, 3),
        "median_likes": round(median_likes, 1),
        "median_interaction": round(median_interaction, 1),
        "viral_ratio": round(viral, 2),
        "freshness": round(freshness, 3) if freshness is not None else None,
        "growth_score": round(growth_score, 3) if growth_score is not None else None,
        "growth_snapshot_n": growth_n,
        "n": len(rows),
        "detail_n": detail_n,
        "dimensions": {k: (round(v, 3) if v is not None else None) for k, v in dimensions.items()},
        "dimension_weights": dict(PLATFORM_HEAT_WEIGHTS),
        "data_coverage": round(available_weight, 3),
        "missing_dimensions": [k for k in PLATFORM_HEAT_WEIGHTS if k not in available],
        "unique_authors": len(authors), "unique_keywords": len(keywords),
        "collection_rounds": max(len(rounds), growth_n + 1 if growth_n else len(rounds)),
        "common_phrases": common_phrases,
    }

# 热搜词领域门禁:热搜词组必须命中至少一个健身根词才算有效。
# 否则视为无关内容(明星八卦/社会新闻/人名/地点)被剔除。
# 注意:只放"健身赛道的根词",不放"身材""美女"这类跨域通用词——
# "身材火辣"出现在八卦帖里一样会命中"身材",这正是污染来源。
_FITNESS_ROOTS = [
    "健身", "运动", "减脂", "燃脂", "瘦", "练", "瑜伽", "跑步", "拉伸",
    "体态", "腹肌", "马甲线", "增肌", "塑形", "有氧", "无氧", "卡路里",
    "热量", "饮食", "低卡", "食谱", "蛋白", "肌肉", "腿", "臀", "手臂",
    "核心", "力量", "跳绳", "游泳", "骑行", "爬坡", "器械",
    "跟练", "暴汗", "减肥", "轻断食", "帕梅拉", "普拉提", "椭圆机",
    "划船机", "壶铃", "波比", "开合跳", "平板支撑", "体脂", "代谢",
    "掉秤", "减重", "塑身", "蜜桃臀", "直角肩", "天鹅颈", "女团腿",
    "漫画腿", "HIIT", "Tabata", "CrossFit",
    # 耐力/球类/户外/康复扩展(避免热搜门禁只放行减脂塑形词)
    "马拉松", "越野", "慢跑", "健走", "徒步", "登山", "户外",
    "羽毛球", "篮球", "足球", "网球", "乒乓球", "排球", "飞盘", "攀岩",
    "滑雪", "冲浪", "撸铁", "动感单车", "功能性训练", "运动康复", "康复",
    "铁三", "体能", "爆发力", "柔韧", "平衡",
]


def _is_fitness_phrase(phrase: str) -> bool:
    """热搜词组命中任一健身根词才算有效;否则剔除(防明星八卦等无关词混入)。"""
    return any(root in phrase for root in _FITNESS_ROOTS)


def _jieba_ngrams(text: str) -> list[str]:
    """用 jieba 分词后构建 1-3 词相邻 n-gram,返回语义完整的词组列表。
    相比字符级滑动窗口,消除了"帕梅""梅拉分"等碎片,产出"帕梅拉""HIIT暴汗挑战"等有意义的词组。
    过滤规则: n-gram 中任一组成词是停用词则丢弃;任一组成词是单字则丢弃(太泛,避免"腿腿""的配"等碎片)。"""
    text = re.sub(r"[^\u4e00-\u9fff]+", " ", text or "")
    words = [w.strip() for w in jieba.cut(text) if w.strip() and w.strip() not in _STOP_WORDS]
    if not words:
        return []
    phrases = []
    for n in (1, 2, 3):  # 1-3 个相邻词,覆盖 2-12 字词组
        for i in range(len(words) - n + 1):
            chunk = words[i:i + n]
            # 过滤: 任一组成词是单字(太泛)则丢弃
            if any(len(w) == 1 for w in chunk):
                continue
            # 过滤: 同一词出现两次(如"体态重要体态"),无意义
            if len(set(chunk)) < len(chunk):
                continue
            phrase = "".join(chunk)
            if 2 <= len(phrase) <= 8:
                phrases.append(phrase)
    return phrases


def _extract_common_phrases(titles: list[str], top_n: int = 5) -> list[str]:
    """从一组标题中提取高频共现词组,
    只保留在 >=2 个标题中出现的词组,按出现标题数降序。
    用于回答"这个话题下大家都在讨论什么角度"。"""
    c = Counter()
    for title in titles:
        seen = set()
        for phrase in _jieba_ngrams(title):
            if phrase not in seen:
                seen.add(phrase)
                c[phrase] += 1
    # 过滤只出现在1个标题中的(纯偶然),去除2字短词优先保留长词组
    candidates = [(w, n) for w, n in c.most_common(top_n * 3) if n >= 2]
    # 去重: 短词被长词包含则合并到长词
    result = []
    for w, n in candidates:
        if any(w in longer and w != longer for longer, _ in result):
            continue
        result.append((w, n))
    return [w for w, _ in result[:top_n]]


def analyze_factors() -> dict:
    """分析爆火因子:标题特征与点赞的关联。

    对比 top-25% 爆款 vs 其余帖子的差异维度:
    - 标题长度
    - 含数字比例
    - 含 emoji 比例
    - 含情绪词比例(必看/绝了/太强/神器/救命/…)
    - 发布时间距今天数
    - 爆款样本 top-5 标题(供人工观察)
    """
    con = sqlite3.connect(DB)
    detailed = _has_detail_columns(con)
    if detailed:
        rows = con.execute(
            "SELECT title, likes_num, collects_num, comments_num, detail_fetched, published_at FROM samples "
            "WHERE collected=(SELECT MAX(collected) FROM samples)").fetchall()
    else:
        rows = con.execute(
            "SELECT title, likes_num, 0, 0, 0, published_at FROM samples "
            "WHERE collected=(SELECT MAX(collected) FROM samples)").fetchall()
    con.close()
    if not rows:
        return {"ok": False, "error": "样本库为空,请先采集"}

    def has_num(t):
        return any(c.isdigit() for c in t)

    def has_emoji(t):
        # emoji 都在基本多语言平面之外
        return any(ord(c) > 0x2000 for c in t)

    _emotion_words = {"必看", "绝了", "太强", "神器", "救命", "封神", "炸裂",
                      "绝绝子", "天花板", "逆天", "惊呆", "暴汗", "暴瘦", "秒睡",
                      "有救了", "见效", "亲测", "吹爆", "安利", "后悔没早"}
    def has_emotion(t):
        return any(w in t for w in _emotion_words)

    hi = sorted(rows, key=lambda r: _weighted_interaction(r[1], r[2], r[3], r[4]), reverse=True)
    cut = max(1, len(hi) // 4)
    top = hi[:cut]
    rest = hi[cut:]

    def avg(lst, f):
        return round(sum(f(x) for x in lst) / len(lst), 2) if lst else 0

    return {
        "ok": True, "sample_n": len(rows),
        "top_avg_title_len": avg(top, lambda r: len(r[0])),
        "rest_avg_title_len": avg(rest, lambda r: len(r[0])),
        "top_num_ratio": avg(top, lambda r: 1 if has_num(r[0]) else 0),
        "rest_num_ratio": avg(rest, lambda r: 1 if has_num(r[0]) else 0),
        "top_emoji_ratio": avg(top, lambda r: 1 if has_emoji(r[0]) else 0),
        "rest_emoji_ratio": avg(rest, lambda r: 1 if has_emoji(r[0]) else 0),
        "top_emotion_ratio": avg(top, lambda r: 1 if has_emotion(r[0]) else 0),
        "rest_emotion_ratio": avg(rest, lambda r: 1 if has_emotion(r[0]) else 0),
        "top_avg_days": avg([r for r in top if _days_ago(r[5]) is not None],
                            lambda r: _days_ago(r[5])),
        "detail_n": sum(1 for r in rows if int(r[4] or 0) == 1),
        "top_examples": [{"likes": r[1], "collects": r[2], "comments": r[3], "title": r[0]} for r in top[:5]],
    }


def _recent_search_keywords(con: sqlite3.Connection, limit: int = 15) -> list[str]:
    """近期(样本库里实际搜索过)的健身关键词,按最近采集时间倒序。

    用于让"今日热搜词"兼顾之前搜过的词,避免每次跑都把上一轮发现的动态词丢掉。
    只保留命中健身根词的 keyword,天然过滤掉历史上的八卦/无关污染词。
    """
    try:
        rows = con.execute(
            "SELECT keyword, MAX(collected) AS last_d, SUM(likes_num) AS total_likes "
            "FROM samples WHERE keyword != '_feed_hot' "
            "GROUP BY keyword ORDER BY last_d DESC, total_likes DESC LIMIT ?",
            (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for kw, _, _ in rows:
        kw = str(kw or "").strip()
        if kw and kw not in out and _is_fitness_phrase(kw):
            out.append(kw)
    return out


def extract_trending_keywords(
    con: sqlite3.Connection | None = None,
    min_count: float = 2.0,
    top_n: int = 10,
) -> list[str]:
    """从样本库最新采集的高赞帖标题中提取高频词组作为今日热搜词。

    改进点:
    - jieba 分词 + 相邻词 n-gram(1-3词),产出语义完整的词组,告别字符碎片
    - 只过滤纯虚词语气词("的""了""一个"等),保留赛道根词("健身""运动""减脂")
    - 点赞数 log10 压缩后作为权重(同一标题内同一词组只计一次)
    - 同一词组出现多个长度时(如"暴汗"和"暴汗挑战"),只保留最长的版本

    min_count: 加权频次阈值,默认 2.0(约等于出现在2个普通热门帖或1个爆款帖)
    """
    close = False
    if con is None:
        if not DB.exists():
            return []
        con = sqlite3.connect(DB)
        close = True
    detailed = _has_detail_columns(con)
    if detailed:
        rows = con.execute(
            "SELECT title, likes_num, collects_num, comments_num, detail_fetched FROM samples "
            "WHERE collected=(SELECT MAX(collected) FROM samples) "
            "ORDER BY likes_num DESC LIMIT 50"
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT title, likes_num, 0, 0, 0 FROM samples "
            "WHERE collected=(SELECT MAX(collected) FROM samples) "
            "ORDER BY likes_num DESC LIMIT 50"
        ).fetchall()
    if close:
        con.close()
    if not rows:
        return []

    c = Counter()
    for title, likes, collects, comments, detail_fetched in rows:
        signal = _weighted_interaction(likes, collects, comments, detail_fetched)
        weight = math.log10(max(signal, 1) + 1)
        seen = set()
        for phrase in _jieba_ngrams(title):
            if phrase in seen:
                continue
            seen.add(phrase)
            # 领域门禁:只保留命中健身根词的热搜词组,剔除明星八卦/社会新闻词
            if not _is_fitness_phrase(phrase):
                continue
            c[phrase] += weight

    # 去重:同一词组多长度版本,保留最长的(频次合并到最长版本)
    # 如 "暴汗"(2字)和"暴汗挑战"(4字),保留后者
    c_dedup = Counter()
    for w, n in c.most_common():
        if any(w in longer and w != longer for longer in c_dedup):
            for longer in c_dedup:
                if w in longer and w != longer:
                    c_dedup[longer] += n
                    break
        else:
            c_dedup[w] += n

    trending = [w for w, n in c_dedup.most_common(50) if n >= min_count][:top_n]
    return trending


def main():
    import json
    print("=== 各关键词平台热度 ===")
    con = sqlite3.connect(DB)
    kws = [r[0] for r in con.execute("SELECT DISTINCT keyword FROM samples").fetchall()]
    for kw in kws:
        h = platform_heat(kw, con)
        print(f"  {kw:<15} 热度={h['heat']}  中位赞={h['median_likes']}  "
              f"新鲜度={h['freshness']}  样本={h['n']}")
        if h.get("common_phrases"):
            print(f"    高频共现: {', '.join(h['common_phrases'])}")
    con.close()

    print("\n=== 热搜词组 ===")
    con = sqlite3.connect(DB)
    kws = extract_trending_keywords(con, min_count=2.0, top_n=15)
    con.close()
    print(f"  {kws}")

    print("\n=== 爆火因子分析 ===")
    print(json.dumps(analyze_factors(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
