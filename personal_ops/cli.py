"""CLI entry for the local daily content workflow."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .workflow import run_daily


def main() -> None:
    parser=argparse.ArgumentParser(description="Run the personal content workflow")
    parser.add_argument("--payload",help="optional JSON containing manual candidates/metrics/materials")
    parser.add_argument("--llm-provider",choices=("http","codex_cli"),help="override text LLM backend")
    parser.add_argument("--reasoning",choices=("low","medium","high","xhigh"),help="override Codex reasoning for all agents")
    parser.add_argument("--generate-images",action="store_true",help="explicitly run Codex image generation")
    parser.add_argument("--max-image-assets",type=int,default=6)
    args=parser.parse_args()
    payload={}
    if args.payload:
        payload=json.loads(Path(args.payload).read_text(encoding="utf-8"))
    source_cfg=dict(payload.get("model_config") or {})
    base_provider=args.llm_provider or os.environ.get("XHS_LLM_PROVIDER") or source_cfg.get("provider", "http")
    global_reasoning=args.reasoning or os.environ.get("XHS_CODEX_REASONING") or source_cfg.get("reasoning_effort", "")
    default_agent_reasoning={
      "topic":os.environ.get("XHS_CODEX_TOPIC_REASONING","high"),
      "golden":os.environ.get("XHS_CODEX_GOLDEN_REASONING","high"),
      "content":os.environ.get("XHS_CODEX_CONTENT_REASONING","high"),
      "outline":os.environ.get("XHS_CODEX_OUTLINE_REASONING","medium"),
      "cover":os.environ.get("XHS_CODEX_COVER_REASONING","medium"),
      "golden_cover":os.environ.get("XHS_CODEX_COVER_REASONING","medium"),
      "image":os.environ.get("XHS_CODEX_IMAGE_REASONING","low"),
    }
    supplied_routes=source_cfg.get("agent_providers") if isinstance(source_cfg.get("agent_providers"),dict) else {}
    if base_provider=="codex_cli":
        default_routes={role:"codex_cli" for role in ("topic","golden","content","outline","cover","golden_cover","image")}
    else:
        default_routes={"topic":"http","golden":"http","content":"http",
                        "outline":"codex_cli","cover":"codex_cli","golden_cover":"http","image":"codex_cli"}
    env_routes={
      "topic":os.environ.get("XHS_TOPIC_PROVIDER"),
      "golden":os.environ.get("XHS_GOLDEN_PROVIDER"),
      "content":os.environ.get("XHS_CONTENT_PROVIDER"),
      "outline":os.environ.get("XHS_OUTLINE_PROVIDER"),
      "cover":os.environ.get("XHS_COVER_PROVIDER"),
      "golden_cover":os.environ.get("XHS_GOLDEN_COVER_PROVIDER"),
      "image":os.environ.get("XHS_IMAGE_PROVIDER"),
    }
    routes={**default_routes,**supplied_routes,
            **{key:value for key,value in env_routes.items() if value}}
    source_providers=source_cfg.get("providers") if isinstance(source_cfg.get("providers"),dict) else {}
    source_http=source_providers.get("http") if isinstance(source_providers.get("http"),dict) else {}
    source_codex=source_providers.get("codex_cli") if isinstance(source_providers.get("codex_cli"),dict) else {}
    http_config={**source_http,
      "base_url":os.environ.get("XHS_LLM_BASE_URL") or source_cfg.get("base_url", ""),
      "api_key":os.environ.get("XHS_LLM_API_KEY") or source_cfg.get("api_key", ""),
      "model":os.environ.get("XHS_LLM_MODEL") or source_cfg.get("model", ""),
    }
    codex_config={**source_codex,
      "codex_cli_path":os.environ.get("CODEX_CLI_PATH") or source_cfg.get("codex_cli_path", ""),
      "model":os.environ.get("XHS_CODEX_MODEL") or source_codex.get("model", "")
              or (source_cfg.get("model", "") if base_provider=="codex_cli" else ""),
    }
    payload["model_config"]={**source_cfg,
      "provider":base_provider,
      **http_config,
      "providers":{**source_providers,"http":http_config,"codex_cli":codex_config},
      "agent_providers":routes,
      "reasoning_effort":global_reasoning,
      "agent_reasoning":{} if global_reasoning else (source_cfg.get("agent_reasoning") or default_agent_reasoning),
      "codex_cli_path":codex_config["codex_cli_path"],
    }
    payload["generate_images"]=bool(args.generate_images)
    payload["max_image_assets"]=max(3,args.max_image_assets)
    print(json.dumps(run_daily(payload),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
