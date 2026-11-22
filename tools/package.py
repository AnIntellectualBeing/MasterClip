"""Package application source; exclude private footage, jobs, caches, and API keys."""

from pathlib import Path
import hashlib
import zipfile

root = Path(__file__).resolve().parents[1]
out = root / "artifacts" / "ClipStudio-0.2-source.zip"
out.parent.mkdir(exist_ok=True)
files = []
for folder in ("clipstudio", "static", "docs", "tools", "tests"):
    files.extend(
        path
        for path in (root / folder).rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and "fixtures" not in path.parts
        and ".pytest_cache" not in path.parts
    )
files.extend(
    path
    for path in root.iterdir()
    if path.is_file() and path.suffix in (".md", ".py", ".txt", ".cmd", ".json", ".ps1")
)
files.append(root / "LICENSE")
files.append(root / ".gitignore")
files.append(root / ".gitattributes")
files.extend(path for path in (root / ".github").rglob("*") if path.is_file())
files.append(root / ".env.example")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, Path("ClipStudio") / path.relative_to(root))
digest = hashlib.sha256(out.read_bytes()).hexdigest()
out.with_suffix(".sha256").write_text(digest + "  " + out.name + "\n", encoding="utf-8")
print(f"{out}\n{out.stat().st_size} bytes\nSHA256 {digest}")
