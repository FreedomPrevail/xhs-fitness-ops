"""Command-line entry point for Canva authorization and imports."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import webbrowser
from pathlib import Path

from .client import CanvaClient, CanvaError, delete_tokens, load_config, save_credentials

ROOT = Path(__file__).resolve().parent.parent


def _package_dir(post_id: str) -> Path:
    post_id = str(post_id or "").strip()
    if not post_id or Path(post_id).name != post_id or not post_id.startswith("xhs_"):
        raise CanvaError("post_id 无效")
    folder = ROOT / "content" / "canva_packages" / post_id
    if not folder.is_dir():
        raise CanvaError(f"找不到 Canva 包：{folder}")
    return folder


def resolve_deck(path: str = "", post_id: str = "") -> Path:
    if path:
        deck = Path(path).expanduser().resolve()
        if not deck.is_file():
            raise CanvaError(f"找不到文件：{deck}")
        return deck
    if post_id:
        folder = _package_dir(post_id)
    else:
        folders = sorted((ROOT / "content" / "canva_packages").glob("xhs_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not folders:
            raise CanvaError("还没有生成 Canva 图文包")
        folder = folders[0]
    from content.carousel.pptx_builder import ensure_editable_pptx
    return ensure_editable_pptx(folder)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m canva_connect", description="Canva OAuth 与可编辑 PPTX 自动导入")
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure", help="把 Canva Client ID/Secret 保存进系统安全凭证库")
    configure.add_argument("--client-id", required=True)
    configure.add_argument("--client-secret", default="", help="不建议写在命令历史；省略后安全提示输入")
    login = sub.add_parser("login", help="浏览器授权一次并保存 OAuth 令牌")
    login.add_argument("--profile", default="default")
    login.add_argument("--no-open", action="store_true")
    login.add_argument("--timeout", type=int, default=300)
    status = sub.add_parser("status", help="检查本机 Canva 连接状态")
    status.add_argument("--profile", default="default")
    upload = sub.add_parser("upload", help="把 PPTX 导入 Canva 并返回编辑链接")
    upload.add_argument("path", nargs="?", default="")
    upload.add_argument("--post-id", default="")
    upload.add_argument("--title", default="")
    upload.add_argument("--profile", default="default")
    upload.add_argument("--open", action="store_true", dest="open_design")
    upload.add_argument("--timeout", type=int, default=240)
    logout = sub.add_parser("logout", help="清除本机保存的 Canva OAuth 令牌")
    logout.add_argument("--profile", default="default")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "configure":
            secret = args.client_secret or getpass.getpass("Canva Client Secret（不会显示）: ")
            save_credentials(args.client_id, secret)
            result = {"ok": True, "configured": True}
        elif args.command == "login":
            result = CanvaClient(load_config(args.profile)).login(open_browser=not args.no_open, timeout=args.timeout)
        elif args.command == "status":
            result = CanvaClient(load_config(args.profile)).status()
        elif args.command == "upload":
            deck = resolve_deck(args.path, args.post_id)
            result = CanvaClient(load_config(args.profile)).import_design(deck, title=args.title or None, poll_timeout=args.timeout)
            if args.open_design and result.get("designs"):
                url = result["designs"][0].get("edit_url")
                if url:
                    webbrowser.open(url)
        elif args.command == "logout":
            config = load_config(args.profile)
            delete_tokens(config)
            result = {"ok": True, "authenticated": False}
        else:
            raise CanvaError("未知命令")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except CanvaError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
