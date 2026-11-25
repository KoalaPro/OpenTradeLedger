#!/usr/bin/env python3
"""CLI entrypoint & zero-docker.sock OTA hot-restart supervisor for OpenTradeLedger (交易账本)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
BASE_SRC = ROOT / "backend" / "src"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bootstrap_ota_and_sys_path(data_dir: Path) -> None:
    """Activate `<data_dir>/.ota/active.json` BEFORE importing `opentradeledger.api.app` if newer."""
    if str(BASE_SRC) not in sys.path:
        sys.path.insert(0, str(BASE_SRC))
    try:
        from opentradeledger.core.self_updater import activate_ota_overlay_if_newer

        overlay = activate_ota_overlay_if_newer(data_dir, ROOT, apply_sys_path=True)
        if overlay is not None:
            # Purge early-imported opentradeledger modules so worker loads 100% from OTA release
            for mod_name in [k for k in list(sys.modules.keys()) if k == "opentradeledger" or k.startswith("opentradeledger.")]:
                sys.modules.pop(mod_name, None)
    except Exception as exc:
        print(f"[OpenTradeLedger] OTA overlay check skipped ({exc})", flush=True)


def _run_supervisor(data_dir: Path, raw_argv: list[str]) -> int:
    restart_flag = data_dir / ".ota" / ".restart_requested"
    stopping = False
    child_proc: subprocess.Popen[bytes] | None = None

    def _forward_stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        if child_proc is not None and child_proc.poll() is None:
            try:
                child_proc.send_signal(signum)
            except OSError:
                pass

    signal.signal(signal.SIGTERM, _forward_stop)
    signal.signal(signal.SIGINT, _forward_stop)

    while True:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", *raw_argv]
        child_proc = subprocess.Popen(cmd)
        rc = child_proc.wait()

        if stopping:
            return rc

        if restart_flag.exists():
            try:
                target_ver = restart_flag.read_text(encoding="utf-8", errors="ignore").strip()
                restart_flag.unlink(missing_ok=True)
            except OSError:
                target_ver = ""
            print(
                f"[OpenTradeLedger] Hot-restarting worker into updated version {target_ver or 'latest'}...",
                flush=True,
            )
            time.sleep(0.25)
            continue

        return rc


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
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--no-supervisor",
        action="store_true",
        default=_env_bool("OTL_NO_SUPERVISOR", False),
        help="Run server directly in current process without OTA restart supervisor",
    )
    args = parser.parse_args()
    data_path = Path(args.data_dir)

    if args.reset_password:
        if str(BASE_SRC) not in sys.path:
            sys.path.insert(0, str(BASE_SRC))
        from opentradeledger.security.local_auth import LocalAuthManager

        mgr = LocalAuthManager(data_path)
        existed = mgr.reset_password()
        msg = "Local access password cleared." if existed else "No local password file was set."
        print(f"[OpenTradeLedger] {msg} Data directory: {args.data_dir}")
        return

    if not args.worker and not args.no_supervisor:
        rc = _run_supervisor(data_path, sys.argv[1:])
        raise SystemExit(rc)

    _bootstrap_ota_and_sys_path(data_path)

    import uvicorn
    from opentradeledger.api.app import create_app

    app = create_app(
        data_dir=data_path,
        demo_mode=args.demo,
        enable_background_sync=not args.no_bg_sync,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
