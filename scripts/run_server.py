#!/usr/bin/env python3
"""CLI entrypoint for OpenTradeLedger (交易账本)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn

from opentradeledger.api.app import create_app
from opentradeledger.security.local_auth import LocalAuthManager


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenTradeLedger — Local-first read-only trade ledger")
    parser.add_argument("--host", default=os.environ.get("OTL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OTL_PORT", "8012")))
    parser.add_argument("--data-dir", default=os.environ.get("OTL_DATA_DIR", str(ROOT / "data")))
    parser.add_argument(
        "--demo",
        action="store_true",
        default=_env_bool("OTL_DEMO_MODE", False),
        help="Launch in built-in sanitized Demo Mode",
    )
    parser.add_argument(
        "--no-bg-sync",
        action="store_true",
        default=_env_bool("OTL_NO_BG_SYNC", False),
        help="Disable background polling loop (useful when previewing side-by-side with another instance)",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Clear local access password (.auth.json) so the next web visit shows the fresh setup prompt",
    )
    args = parser.parse_args()

    if args.reset_password:
        mgr = LocalAuthManager(Path(args.data_dir))
        existed = mgr.reset_password()
        msg = "Local access password cleared." if existed else "No local password file was set."
        print(f"[OpenTradeLedger] {msg} Data directory: {args.data_dir}")
        return

    app = create_app(
        data_dir=Path(args.data_dir),
        demo_mode=args.demo,
        enable_background_sync=not args.no_bg_sync,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
