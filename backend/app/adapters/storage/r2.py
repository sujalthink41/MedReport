"""Cloudflare R2 storage. Production.

R2 speaks the S3 API, so this is a boto3 client pointed at a different endpoint.
Chosen over S3 for one commercial reason: R2 charges nothing for egress, and this
product serves medical documents back to users repeatedly.
"""

import asyncio
from typing import Any, cast

from app.core.logging import get_logger
from app.domain.errors import StorageUnavailableError
from app.domain.ports.services import StorageObjectNotFoundError

log = get_logger(__name__)


class R2Storage:
    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            # R2 ignores regions but the SDK insists on one.
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                # Bounded retries here, on top of our own. Without a cap, a storage
                # outage turns one upload into minutes of blocked thread.
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                # Encrypted at rest. These are medical documents; the cost is zero
                # and the alternative is indefensible.
                ServerSideEncryption="AES256",
            )

        await self._call(_put, key=key, operation="put")

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body

        return cast("bytes", await self._call(_get, key=key, operation="get"))

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        await self._call(_delete, key=key, operation="delete")

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        """A short-lived URL the client fetches directly.

        Short-lived and signed because these are medical documents: a permanent or
        guessable URL is a data breach with extra steps. Serving the bytes through
        our own API instead would work, but would put every megabyte of every
        report through the application servers for no benefit.
        """

        def _sign() -> str:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl_seconds,
            )
            return url

        # _call is deliberately untyped (it forwards any boto3 call); the cast
        # restores the contract the port promises.
        return cast("str", await self._call(_sign, key=key, operation="sign"))

    async def _call(self, fn: Any, *, key: str, operation: str) -> Any:
        """Run a blocking boto3 call off the event loop, translating its errors.

        boto3 is synchronous. Called directly inside async it blocks every other
        request on this worker for the duration of a network round trip.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return await asyncio.to_thread(fn)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404"}:
                raise StorageObjectNotFoundError(key=key) from exc
            log.warning("storage_call_failed", operation=operation, code=code)
            # Infrastructure error: retryable, 5xx. A botocore exception reaching
            # the domain would break the dependency rule and map to a 500 with no
            # retry, which is wrong for a transient outage.
            raise StorageUnavailableError(operation=operation) from exc
        except BotoCoreError as exc:
            log.warning("storage_unreachable", operation=operation)
            raise StorageUnavailableError(operation=operation) from exc
