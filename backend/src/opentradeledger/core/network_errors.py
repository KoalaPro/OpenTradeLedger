"""Transport errors from HTTPX and its optional SOCKS handshake parser."""

import httpx

try:
    from socksio.exceptions import ProtocolError as SocksProtocolError
except ImportError:
    # The base image can load the OTA updater before a bundled SOCKS driver is available.
    SocksProtocolError = httpx.ProxyError

HTTP_CLIENT_ERRORS = (httpx.HTTPError, SocksProtocolError)
