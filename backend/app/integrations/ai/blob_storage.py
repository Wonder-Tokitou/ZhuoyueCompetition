"""Storage seam. Object-store adapters return opaque locators, never ORM objects."""
from pathlib import Path
from typing import Protocol


class BlobStorage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def read(self, locator: str) -> bytes: ...
    def delete(self, locator: str) -> None: ...


class LocalBlobStorage:
    def __init__(self, root: Path, base: Path):
        self.base = base.resolve()
        self.root = (root if root.is_absolute() else self.base / root).resolve()

    def _checked(self, path):
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("制品路径超出存储目录")
        return path

    def put(self, key, data):
        path = self._checked(self.root / key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        try:
            return str(path.relative_to(self.base))
        except ValueError:
            return str(path)

    def read(self, locator):
        path = Path(locator)
        return self._checked(path if path.is_absolute() else self.base / path).read_bytes()

    def delete(self, locator):
        path = Path(locator)
        checked = self._checked(path if path.is_absolute() else self.base / path)
        checked.unlink(missing_ok=True)
