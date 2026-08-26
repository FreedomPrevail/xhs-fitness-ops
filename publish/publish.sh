#!/usr/bin/env bash
# L2 发布 —— 读 content/drafts/ 里的草稿 JSON,调小红书 CLI 发布。
# 默认 --draft(存草稿不直接发),人工审核后再去创作者中心确认发布。
# 用法: bash publish.sh            # 发布今天所有草稿(草稿模式)
#       bash publish.sh --live     # 直接发布(慎用,需已人工审核)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRAFTS="$ROOT/content/drafts"
MAP="$ROOT/data/note_topic_map.json"   # note_id -> topic_id 归因映射
TODAY="$(date +%F)"
export PYTHONIOENCODING=utf-8          # 内联 python 提取含 emoji 字段时不崩

# 用 TAB 分隔一次性提取草稿字段,避免多次 python 调用 & 编码问题
extract() {  # $1=文件  输出: title<TAB>topics<TAB>topic_id<TAB>images<TAB>bodylen
  python - "$1" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print('\t'.join([
    d.get('title',''),
    ','.join(d.get('topics',[])),
    d.get('topic_id',''),
    d.get('images',''),
    str(len(d.get('body',''))),
]))
PY
}

DRAFT_FLAG="--draft"
DRY_RUN=0
for arg in "$@"; do
  [ "$arg" = "--live" ] && DRAFT_FLAG=""
  [ "$arg" = "--dry-run" ] && DRY_RUN=1
done

if [ "$DRY_RUN" != "1" ]; then
  echo "[人工发布策略] 已阻止自动发布/保存平台草稿。请在官方客户端发布；本脚本仅保留 --dry-run 预览。" >&2
  exit 2
fi

# 频率控制:读 schedule.yaml 的 max_per_day(简单 grep,避免额外依赖)
MAX=$(grep -E "^\s*max_per_day:" "$ROOT/config/schedule.yaml" | grep -oE "[0-9]+" | head -1)
MAX="${MAX:-2}"

count=0
shopt -s nullglob
for f in "$DRAFTS"/${TODAY}_*.json; do
  if [ "$count" -ge "$MAX" ]; then
    echo "[频控] 已达每日上限 $MAX 篇,剩余草稿留待明日。"
    break
  fi

  IFS=$'\t' read -r title topics topic_id images bodylen < <(extract "$f")
  body=$(python - "$f" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding='utf-8')).get('body',''))
PY
)

  echo "[发布${DRAFT_FLAG:+(草稿)}] 《$title》 topics=$topics"
  IMG_ARG=()
  [ -n "$images" ] && IMG_ARG=(--images "$images")

  # 小红书图文笔记要求至少1张图;无图则跳过并提示(封面图由 L1 环节补)
  if [ -z "$images" ]; then
    echo "  ⚠ 该草稿无封面图(images 字段为空)。小红书图文笔记需≥1张图,已跳过。"
    echo "     请在 L1 生成环节补封面图路径到草稿的 images 字段后再发。"
    continue
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry-run] 将执行: opencli xiaohongshu publish <正文${#body}字> --title \"$title\" --topics \"$topics\" --images \"$images\" $DRAFT_FLAG"
    count=$((count + 1))
    continue
  fi

  # shellcheck disable=SC2086
  result=$(opencli xiaohongshu publish "$body" \
      --title "$title" \
      --topics "$topics" \
      "${IMG_ARG[@]}" \
      $DRAFT_FLAG \
      -f json 2>&1) || { echo "  ✗ 发布失败: $result"; continue; }

  echo "  ✓ $result"
  # 归因:若返回 note_id,写入映射表(供 collect.py 关联 topic_id)
  nid=$(echo "$result" | python -c "import json,sys;
try:
  d=json.load(sys.stdin); print(d.get('note_id') or d.get('id') or '')
except: print('')" 2>/dev/null || echo "")
  if [ -n "$nid" ]; then
    python -c "import json,os,sys;
p=sys.argv[1]; m=json.load(open(p,encoding='utf-8')) if os.path.exists(p) else {};
m[sys.argv[2]]=sys.argv[3]; json.dump(m,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)" \
      "$MAP" "$nid" "$topic_id"
  fi

  count=$((count + 1))
  mv "$f" "$f.published"   # 标记已处理,避免重复发布
done

echo "[发布完成] 本次处理 $count 篇。"
