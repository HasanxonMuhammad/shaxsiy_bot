from pathlib import Path


class MemoryStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._read_files: set[str] = set()

    def _validate(self, name: str) -> Path:
        if ".." in name:
            raise ValueError("Path traversal rejected")
        path = self.base_dir / name
        resolved = path.resolve()
        if not str(resolved).startswith(str(self.base_dir.resolve())):
            raise ValueError("Path outside memory directory")
        return path

    def create(self, name: str, content: str):
        path = self._validate(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read(self, name: str) -> str:
        path = self._validate(name)
        content = path.read_text(encoding="utf-8")
        self._read_files.add(name)
        return content

    def edit(self, name: str, content: str):
        if name not in self._read_files:
            raise ValueError(f"Must read before editing: {name}")
        path = self._validate(name)
        path.write_text(content, encoding="utf-8")

    def delete(self, name: str):
        path = self._validate(name)
        if path.exists():
            path.unlink()
        self._read_files.discard(name)

    def list_all(self) -> list[str]:
        files = []
        for p in self.base_dir.rglob("*"):
            if p.is_file():
                files.append(str(p.relative_to(self.base_dir)))
        return sorted(files)

    def search(self, query: str) -> list[tuple[str, str]]:
        results = []
        q = query.lower()
        for name in self.list_all():
            path = self.base_dir / name
            try:
                content = path.read_text(encoding="utf-8")
                if q in content.lower() or q in name.lower():
                    results.append((name, content[:200]))
            except Exception:
                pass
        return results
