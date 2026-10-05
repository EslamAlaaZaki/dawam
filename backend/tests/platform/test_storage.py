"""The storage backends: opaque keys, no way out of the storage directory."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dawam.platform.storage import LocalFileStorage, S3FileStorage, new_key


def test_local_storage_round_trips_and_deletes(tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    key = new_key()

    storage.put(key, b"hello")
    assert storage.get(key) == b"hello"
    storage.put(key, b"again")
    assert storage.get(key) == b"again"
    storage.delete(key)
    storage.delete(key)  # already gone: fine
    with pytest.raises(KeyError):
        storage.get(key)


@pytest.mark.parametrize("key", ["../etc/passwd", "a/b", "..", "", "A" * 32, "abc", "/" + "a" * 31])
def test_local_storage_refuses_anything_but_a_random_key(tmp_path: Path, key: str):
    storage = LocalFileStorage(tmp_path / "root")

    with pytest.raises(ValueError):
        storage.put(key, b"x")
    with pytest.raises(ValueError):
        storage.get(key)
    with pytest.raises(ValueError):
        storage.delete(key)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket, Key):
        try:
            return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}
        except KeyError:
            error = Exception("NoSuchKey")
            error.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
            raise error from None

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_s3_storage_uses_the_key_in_its_bucket():
    client = FakeS3Client()
    storage = S3FileStorage(client, "docs")
    key = new_key()

    storage.put(key, b"hello")
    assert client.objects == {("docs", key): b"hello"}
    assert storage.get(key) == b"hello"
    storage.delete(key)
    with pytest.raises(KeyError):
        storage.get(key)
    with pytest.raises(ValueError):
        storage.put("../x", b"")
