"""File storage: where uploaded and generated files' bytes live (spec §6.17).

``FileStorage`` is a port over opaque, random storage keys. ``LocalFileStorage`` (a
directory; the default) and ``S3FileStorage`` (any S3-compatible bucket) implement it;
``create_storage`` picks one from the settings. The files module owns what the keys
mean; this module never sees a user-chosen name.
"""

from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any, Protocol

from .config import Settings

KEY_PATTERN = re.compile(r"[0-9a-f]{32}")
"""Every storage key: 128 random bits in hex. Nothing else is ever a valid key, so a key
can never contain a path separator or ``..``."""


def new_key() -> str:
    return secrets.token_hex(16)


class StorageError(Exception):
    """The backend could not store or read an object."""


class FileStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes:
        """The object's bytes; raises ``KeyError`` if there is none."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object; a missing one is not an error."""
        ...


def _check(key: str) -> str:
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("not a storage key")
    return key


class LocalFileStorage:
    """Objects are files named by their key under ``root`` (a persistent volume)."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        key = _check(key)
        return self._root / key[:2] / key

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target, then rename: a reader never sees half a file.
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise KeyError(key) from None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3FileStorage:
    """Objects in an S3-compatible bucket, under their key."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings) -> S3FileStorage:
        if not settings.s3_bucket:
            raise StorageError("DAWAM_S3_BUCKET must be set when DAWAM_STORAGE_BACKEND=s3.")
        try:
            import boto3
        except ImportError:
            raise StorageError(
                "The s3 storage backend needs boto3: install DAWAM with the s3 extra."
            ) from None
        secret = settings.s3_secret_access_key
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=secret.get_secret_value() if secret else None,
        )
        return cls(client, settings.s3_bucket)

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=_check(key), Body=data)

    def get(self, key: str) -> bytes:
        try:
            return self._client.get_object(Bucket=self._bucket, Key=_check(key))["Body"].read()
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise KeyError(key) from None
            raise

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=_check(key))


def create_storage(settings: Settings) -> FileStorage:
    if settings.storage_backend == "s3":
        return S3FileStorage.from_settings(settings)
    return LocalFileStorage(settings.storage_path)
