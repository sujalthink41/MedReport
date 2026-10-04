"""One suite, every FileStorage implementation.

This is the test that makes "pluggable storage" true rather than claimed. Each
case is written against the port; the subclasses supply an implementation.

The R2 variant is skipped unless credentials are present, so the suite is useful
on a laptop with no cloud account — but when credentials exist it proves the two
adapters are genuinely interchangeable rather than merely similarly named.
"""

import os
from pathlib import Path

import pytest

from app.domain.ports.services import StorageObjectNotFoundError

PDF = b"%PDF-1.4 fake report bytes"


class FileStorageContract:
    """Behaviour every FileStorage must exhibit."""

    async def test_what_goes_in_comes_back_out(self, storage) -> None:  # type: ignore[no-untyped-def]
        await storage.put("reports/a.pdf", PDF, "application/pdf")

        assert await storage.get("reports/a.pdf") == PDF

    async def test_bytes_are_returned_exactly(self, storage) -> None:  # type: ignore[no-untyped-def]
        # Medical documents must round-trip byte for byte. Any encoding step that
        # "helpfully" normalises content would corrupt a scanned report.
        awkward = bytes(range(256)) * 10
        await storage.put("reports/binary", awkward, "application/octet-stream")

        assert await storage.get("reports/binary") == awkward

    async def test_a_missing_key_raises_our_error(self, storage) -> None:  # type: ignore[no-untyped-def]
        # The single most important case. One adapter raising FileNotFoundError
        # while the other returns None would make the swap a lie, and callers
        # would have to know which implementation they got.
        with pytest.raises(StorageObjectNotFoundError):
            await storage.get("reports/never-written")

    async def test_writing_twice_replaces(self, storage) -> None:  # type: ignore[no-untyped-def]
        await storage.put("reports/b.pdf", b"first", "application/pdf")
        await storage.put("reports/b.pdf", b"second", "application/pdf")

        # Re-processing a report must not depend on which write landed last.
        assert await storage.get("reports/b.pdf") == b"second"

    async def test_delete_removes_the_object(self, storage) -> None:  # type: ignore[no-untyped-def]
        await storage.put("reports/c.pdf", PDF, "application/pdf")

        await storage.delete("reports/c.pdf")

        with pytest.raises(StorageObjectNotFoundError):
            await storage.get("reports/c.pdf")

    async def test_deleting_twice_is_not_an_error(self, storage) -> None:  # type: ignore[no-untyped-def]
        await storage.put("reports/d.pdf", PDF, "application/pdf")

        await storage.delete("reports/d.pdf")
        await storage.delete("reports/d.pdf")

        # Idempotent: a retried cleanup job must not fail because it already
        # succeeded once.

    async def test_deleting_something_that_never_existed_is_not_an_error(
        self,
        storage,  # type: ignore[no-untyped-def]
    ) -> None:
        await storage.delete("reports/imaginary")

    async def test_nested_keys_work(self, storage) -> None:  # type: ignore[no-untyped-def]
        # Keys are namespaced by profile, so slashes must be ordinary characters
        # and intermediate "directories" must not need creating by the caller.
        key = "profiles/abc/reports/2026/01/report.pdf"
        await storage.put(key, PDF, "application/pdf")

        assert await storage.get(key) == PDF

    async def test_a_signed_url_is_produced(self, storage) -> None:  # type: ignore[no-untyped-def]
        await storage.put("reports/e.pdf", PDF, "application/pdf")

        url = await storage.signed_url("reports/e.pdf", ttl_seconds=60)

        assert isinstance(url, str)
        assert url
        # Signed, not a bare path. A guessable URL to a medical document is a
        # breach with extra steps.
        assert "signature" in url.lower() or "x-amz-signature" in url.lower()


class TestLocalDiskStorage(FileStorageContract):
    @pytest.fixture
    def storage(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        from app.adapters.storage.local import LocalDiskStorage

        return LocalDiskStorage(root=tmp_path)

    async def test_keys_cannot_escape_the_root(self, storage) -> None:  # type: ignore[no-untyped-def]
        # Local-only: path traversal has no meaning for an object store. Keys come
        # from our own code, but treating them as untrusted costs nothing and
        # closes the hole before anyone ever builds a key from user input.
        from app.domain.errors import StorageUnavailableError

        with pytest.raises(StorageUnavailableError):
            await storage.put("../../escaped.txt", b"x", "text/plain")


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("MEDREPORT_R2_ACCOUNT_ID"),
    reason="R2 credentials not configured",
)
class TestR2Storage(FileStorageContract):
    """The same suite against real Cloudflare R2.

    Skipped without credentials so the laptop suite stays green, and run in CI
    against a scratch bucket when they are present.
    """

    @pytest.fixture
    def storage(self):  # type: ignore[no-untyped-def]
        from app.adapters.storage.r2 import R2Storage

        return R2Storage(
            account_id=os.environ["MEDREPORT_R2_ACCOUNT_ID"],
            access_key_id=os.environ["MEDREPORT_R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["MEDREPORT_R2_SECRET_ACCESS_KEY"],
            bucket=os.environ["MEDREPORT_R2_BUCKET"],
        )
