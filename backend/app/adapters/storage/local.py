"""Local disk storage. Development and tests.

Must behave *identically* to ``R2Storage`` — same errors, same semantics for a
missing key. An adapter that only mostly matches is worse than none, because the
difference surfaces in production rather than in the test suite. The contract suite
in ``tests/contracts/test_file_storage.py`` runs both against the same tests.
"""

import asyncio
import base64
import hashlib
import hmac
import time
from pathlib import Path

from app.domain.errors import StorageUnavailableError
from app.domain.ports.services import StorageObjectNotFoundError


class LocalDiskStorage:
    def __init__(self, root: Path, signing_secret: str = "local-dev") -> None:
        self._root = root
        self._signing_secret = signing_secret
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys arrive from our own code, but treating them as untrusted costs
        # nothing and closes a path-traversal hole: a key of "../../etc/passwd"
        # would otherwise write outside the root.
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root.resolve()):
            raise StorageUnavailableError(reason="invalid_key")
        return candidate

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temporary file then rename. os.replace is atomic, so a
            # crash halfway through never leaves a half-written medical document
            # that later reads as corrupt.
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)

        # to_thread: file IO is blocking, and blocking inside async freezes every
        # other request on this worker.
        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            # Translated to OUR error, so callers behave the same whichever adapter
            # is wired in. Letting FileNotFoundError escape would make the two
            # implementations observably different.
            raise StorageObjectNotFoundError(key=key) from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)

        def _remove() -> None:
            path.unlink(missing_ok=True)  # deleting twice is not an error

        await asyncio.to_thread(_remove)

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        """A signed local URL, so development exercises the same flow as production.

        Signed rather than a plain path even locally: if dev used unsigned URLs,
        the first time anyone tested signature expiry would be in production.
        """
        expires = int(time.time()) + ttl_seconds
        message = f"{key}:{expires}".encode()
        signature = hmac.new(self._signing_secret.encode(), message, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        return f"/files/{key}?expires={expires}&signature={token}"
