"""Offline preparation of approved Imagegen art; never imported by the server.

Only the medal area is retained. Sample headings, counts and dates are excluded;
the application renders those from authenticated data instead of image pixels.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CROP = (145, 190, 875, 1055)
VARIANTS = {'thumb': (240, 80, 24_000), 'detail': (640, 84, 100_000)}


def build(source: Path) -> dict:
    payloads, months = {}, []
    for number in range(1, 13):
        month = f'{number:02}'
        original = source / f'attendance-2027-{month}.png'
        with Image.open(original) as image:
            if image.size != (1024, 1536):
                raise ValueError(f'Unexpected approved-image dimensions: {original}')
            artwork = image.convert('RGB').crop(CROP)
        row = {'month': month}
        for variant, (width, quality, limit) in VARIANTS.items():
            height = round(artwork.height * width / artwork.width)
            resized = artwork.resize((width, height), Image.Resampling.LANCZOS)
            stream = BytesIO()
            resized.save(stream, 'WEBP', quality=quality, method=6)
            data = stream.getvalue()
            if len(data) > limit:
                raise ValueError(f'{month}-{variant} exceeds static-resource budget: {len(data)}')
            name = f'{month}-{variant}.webp'
            payloads[name] = data
            row[variant] = {'file': name, 'width': width, 'height': height, 'bytes': len(data),
                            'sha256': hashlib.sha256(data).hexdigest()}
        months.append(row)
    version = 'seasonal-' + hashlib.sha256(b''.join(payloads[name] for name in sorted(payloads))).hexdigest()[:12]
    target = ROOT / 'backend/app/static/images/attendance-medals' / version
    target.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        path = target / name
        if path.exists():
            if path.read_bytes() != data:
                raise RuntimeError(f'Immutable asset would be overwritten: {path}')
        else:
            path.write_bytes(data)
    manifest = {'version': version, 'source_crop': CROP, 'shared_across_years': True,
                'runtime_generation': False, 'months': months,
                'total_bytes': sum(len(data) for data in payloads.values()),
                'all_thumbnails_bytes': sum(r['thumb']['bytes'] for r in months)}
    manifest_path = target / 'catalog.json'
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    if manifest_path.exists() and manifest_path.read_bytes() != encoded:
        raise RuntimeError('Immutable asset catalog differs')
    manifest_path.write_bytes(encoded)
    return {'directory': target.relative_to(ROOT).as_posix(), **manifest}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'output/attendance-medals-12-months-preview-20261107-v1')
    args = parser.parse_args()
    result = build(args.source)
    print(json.dumps({key: result[key] for key in ('directory', 'version', 'total_bytes', 'all_thumbnails_bytes')},
                     ensure_ascii=False, indent=2))
