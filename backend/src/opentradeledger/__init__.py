"""OpenTradeLedger (交易账本) — Local-first, read-only multi-exchange trade ledger."""

import os

# OTA overlays can run on an older image whose Python environment lacks the SOCKS extra.
if os.environ.get("OTL_OTA_ACTIVE") == "1":
    from importlib.util import find_spec
    from pathlib import Path
    import sys

    if find_spec("socksio") is None:
        wheel = Path(__file__).with_name("_ota_wheels") / "socksio-1.0.0-py3-none-any.whl"
        if wheel.is_file():
            sys.path.append(str(wheel))

__version__ = "1.4.1"
__app_name__ = "OpenTradeLedger"
__app_name_zh__ = "交易账本"
