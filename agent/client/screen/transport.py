from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from contracts.v2.screen import ScreenFrame


class ScreenUploader:
    def __init__(self, gateway_url: str, token: str, ca_file: str | None = None):
        parts = urlsplit(gateway_url)
        self.url = urlunsplit(
            ("https" if parts.scheme == "wss" else "http", parts.netloc, "/screen/frames", "", "")
        )
        self.token = token
        if parts.scheme == "wss":
            if ca_file is not None and not Path(ca_file).is_file():
                raise FileNotFoundError(f"Gateway CA certificate not found: {ca_file}")
            self.context: ssl.SSLContext | None = ssl.create_default_context(cafile=ca_file)
        else:
            self.context = None

    def __call__(self, metadata: ScreenFrame, data: bytes) -> bool:
        request = urllib.request.Request(
            self.url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": metadata.content_type,
                "X-EECP-Frame": metadata.to_json(),
            },
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=10) as response:
                receipt = json.loads(response.read(8192))
        except urllib.error.HTTPError as exc:
            raise OSError(f"screenshot upload rejected ({exc.code}): {exc.reason}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                raise OSError(f"screenshot upload TLS verification failed: {exc.reason}") from exc
            raise OSError(f"screenshot upload failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise OSError("screenshot upload returned invalid JSON receipt") from exc
        if receipt.get("sha256") != metadata.sha256:
            raise OSError("screenshot receipt checksum mismatch")
        return receipt.get("server_stored") is True
