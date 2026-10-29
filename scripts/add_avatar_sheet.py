"""Prepare optimized thumbnail assets from the approved avatar sheet."""
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image

SOURCE = Path(sys.argv[1])
ROOT = Path(__file__).resolve().parents[1] / "backend/app/static/images/avatars"
ENTRIES = [("new-zoo-clawhauser", "豹警官·新造型"), ("new-zoo-flash", "闪电·新造型"),
           ("new-zoo-gazelle", "夏奇羊·新造型"), ("new-zoo-finnick", "芬尼克·新造型"),
           ("new-zoo-frufru", "露露·新造型"), ("new-zoo-yax", "牦牛亚克斯·新造型")]

def main():
    sheet = Image.open(SOURCE).convert("RGB")
    catalog_path = ROOT / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["avatars"] = [a for a in catalog["avatars"] if a["key"] not in {key for key, _ in ENTRIES}]
    for i, (key, name) in enumerate(ENTRIES):
        col, row = i % 3, i // 3
        left, top = 11 + col * 429, 20 + row * 608
        tile = sheet.crop((left, top, left + 410, top + 410))
        images = {}
        for size in (64, 160):
            filename = f"{key}-{size}.webp"
            tile.resize((size, size), Image.Resampling.LANCZOS).save(ROOT / filename, "WEBP", quality=75, method=6)
            data = (ROOT / filename).read_bytes()
            images[str(size)] = {"file": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        catalog["avatars"].append({"key": key, "name": name, "group": "疯狂动物城 · 手绘新造型", "kind": "character-concept", "images": images})
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
