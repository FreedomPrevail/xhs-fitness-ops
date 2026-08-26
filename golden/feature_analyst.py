from __future__ import annotations
import json
from . import repository, cover_vision
from .llm import call_json, provider_name

FEATURE_KEYS=["topic","audience_intent","title_hook","content","cover","transferability","media"]

def _prompt(c: dict) -> str:
    content_type=repository.normalize_content_type(c.get("content_type"))
    scope=repository.analysis_scope_for(content_type,bool((c.get("body") or "").strip()))
    if content_type == "video":
        media_rule=("这是视频候选，但系统没有视频文件、关键帧或ASR转写。正文只视为视频简介，"
                    "不得推断前3秒、口播、动作顺序、镜头节奏或字幕；这些字段必须返回unknown或空数组。")
    elif content_type == "image_text":
        media_rule=("这是图文候选。当前没有读取图片像素；只能分析标题、正文、互动和已知封面元数据，"
                    "不得假装看过内页图片或封面画面。")
    else:
        media_rule="媒介类型未知，不得把它断言为图文或视频；只分析实际提供的标题、正文和元数据。"
    return f'''你是 Golden Feature Analyst。分析一篇小红书健身帖子为什么值得学习。只输出合法 JSON，不要 markdown。
要求：区分【帖子中可观察事实】和【基于内容推断】；对于受众刷帖时间/搜索场景，只能作为推断，必须给 confidence，不可伪装成平台真实统计。
媒介类型：{content_type}
分析范围：{scope}
媒介限制：{media_rule}
原帖信息：
标题：{c.get("title","")}
正文：{c.get("body","") or "(详情正文尚未抓到；仅能基于标题弱推断)"}
关键词：{c.get("keyword","")}
点赞/收藏/评论：{c.get("likes",0)}/{c.get("collects",0)}/{c.get("comments",0)}
发布时间：{c.get("published_at","")}

严格返回以下结构：
{{
 "content_type":"{content_type}",
 "analysis_scope":"{scope}",
 "media":{{"video_file_available":false,"keyframes_available":false,"transcript_available":false,"first_3s_hook":"unknown","shot_structure":[],"speech_structure":[],"subtitle_keywords":[]}},
 "topic":{{"primary":"","subtopic":"","scenario":"","fitness_goal":"","domain_relevance":0.0}},
 "audience_intent":{{
   "audience_profile":{{"gender_tendency":"","age_range":"","fitness_level":"","goal":"","pain_points":[],"knowledge_level":""}},
   "usage_context":{{"likely_browse_periods":[],"likely_search_scenarios":[],"intent_type":"search|feed_discovery|mixed","search_intent_strength":0.0,"confidence":0.0,"basis":""}},
   "search_behavior":{{"likely_queries":[],"query_type":"","keyword_specificity":"","searchability_score":0.0}}
 }},
 "title_hook":{{"title_pattern":"","hook_type":"","emotion":"","curiosity_gap":false,"contrast":false,"learnable_elements":[]}},
 "content":{{"opening_style":"","content_structure":[],"writing_style":"","proof_types":[],"information_density":0.0,"cta_type":"","copy_strengths":[],"memorable_phrases_or_moves":[]}},
 "cover":{{"cover_type":"unknown","layout":"unknown","cover_hook":"","text_length_estimate":0,"person_present":"unknown","learnable_elements":[]}},
 "transferability":{{"content_quality":0.0,"replicability":0.0,"novelty":0.0,"celebrity_dependency":0.0,"appearance_dependency":0.0,"resource_requirement":"low|medium|high","learnable_pattern":true,"risks":[]}},
 "analysis_summary":{{"why_selected":"","most_valuable_features":[],"confidence":0.0}}
}}
所有 0~1 分数必须在范围内。不要复制大段原文；原文由 Golden Pool 独立保存。'''


def analyze_candidate(c: dict, cfg: dict) -> dict:
    f=call_json(_prompt(c),cfg,role="golden")
    for k in FEATURE_KEYS: f.setdefault(k,{})
    content_type=repository.normalize_content_type(c.get("content_type"))
    scope=repository.analysis_scope_for(content_type,bool((c.get("body") or "").strip()))
    f["content_type"]=content_type; f["analysis_scope"]=scope
    media=f["media"] if isinstance(f.get("media"),dict) else {}
    if content_type == "video":
        # 第一阶段没有视频字节，强制封住模型臆测出来的视听Feature。
        media.update({"video_file_available":False,"keyframes_available":False,"transcript_available":False,
                      "first_3s_hook":"unknown","shot_structure":[],"speech_structure":[],"subtitle_keywords":[]})
    f["media"]=media
    # 只有拿到真实封面字节并由视觉模型成功读取，才覆盖原来的元数据弱分析。
    visual=cover_vision.analyze_cover(c,cfg)
    f["cover_visual_status"]={k:v for k,v in visual.items()
                              if k in ("visual_observed","status","local_path","reason","confidence")}
    if visual.get("visual_observed"):
        cover=f["cover"] if isinstance(f.get("cover"),dict) else {}
        for key in ("cover_type","layout","dominant_colors","contrast","visual_subject",
                    "person_present","body_or_action","ocr_text","text_length_estimate",
                    "visual_focus","cover_hook","title_cover_alignment","learnable_elements","risks","confidence"):
            if key in visual: cover[key]=visual[key]
        cover["visual_observed"]=True
        f["cover"]=cover
    return f


def analyze_pending(cfg: dict, limit: int=20) -> dict:
    results=[]; errors=[]
    for c in repository.pending_candidates(limit):
        if repository.get_feature(c["candidate_id"]): continue
        try:
            f=analyze_candidate(c,cfg); repository.save_feature(c["candidate_id"],f,cfg.get("model") or provider_name(cfg,"golden")); results.append({"candidate_id":c["candidate_id"],"feature":f})
        except Exception as e: errors.append({"candidate_id":c["candidate_id"],"error":str(e)[:240]})
    return {"analyzed":len(results),"errors":errors,"results":results}
