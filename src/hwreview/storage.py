"""Private blob store. Keys are server-generated hex; client names never touch the filesystem."""

import os
import re
import secrets
from pathlib import Path

from hwreview.errors import ErrorCode, HWError

_KEY = re.compile(r"^[0-9a-f]{64}$")


class BlobStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise HWError(ErrorCode.NOT_FOUND, "invalid storage key")
        return self.root / key[:2] / key

    def put(self, data: bytes) -> str:
        key = secrets.token_hex(32)
        path = self.path_for(key)
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
        return key

    def get(self, key: str) -> bytes:
        path = self.path_for(key)
        if not path.is_file():
            raise HWError(ErrorCode.NOT_FOUND, "blob not found")
        return path.read_bytes()
