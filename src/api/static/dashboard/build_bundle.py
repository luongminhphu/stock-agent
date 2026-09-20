"""Build dashboard.bundle.css theo thứ tự @import trong dashboard.css.

Dùng: python src/api/static/dashboard/build_bundle.py [dashboard_dir]
Sau khi sửa bất kỳ file css/**, chạy lại script này rồi bump ?v= của
dashboard.bundle.css trong index.html.
"""
from __future__ import annotations

import pathlib
import re
import sys


def build(root: pathlib.Path) -> int:
    entry = (root / "dashboard.css").read_text(encoding="utf-8")
    imports = re.findall(r"@import url\('\./(css/[^']+)'\);", entry)
    out: list[str] = []
    for rel in imports:
        css = (root / rel).read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # strip comments
        css = re.sub(r"\n\s*\n+", "\n", css).strip()  # collapse blank lines
        out.append(f"/* {rel} */\n{css}\n")
    target = root / "dashboard.bundle.css"
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"bundled {len(imports)} files -> {target.name} ({target.stat().st_size // 1024} KB)")
    return len(imports)


if __name__ == "__main__":
    base = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent
    build(base)
