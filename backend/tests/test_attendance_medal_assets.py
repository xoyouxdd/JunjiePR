"""Shipped artwork budgets and real static serving; no app startup or real DB."""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile

os.environ['RECOGNITION_V2_DATA_DIR'] = tempfile.mkdtemp(prefix='medal-static-assets-')

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import event

from app.main import app
from app.database.connection import engine

STATIC = Path(__file__).resolve().parents[1] / 'app/static'
MODULE = STATIC / 'js/app/attendance-medals.js'
ART = re.search(r"const MEDAL_ART_ROOT='([^']+)'", MODULE.read_text(encoding='utf-8')).group(1)
CATALOG = json.loads(
    (STATIC / ART.removeprefix('/static/') / 'catalog.json').read_text(encoding='utf-8'))


def test_all_twelve_months_have_small_valid_content_addressed_webp_assets():
    assert re.fullmatch(r'/static/images/attendance-medals/seasonal-[0-9a-f]{12}', ART)
    assert [m['month'] for m in CATALOG['months']] == [f'{n:02}' for n in range(1,13)]
    assert CATALOG['runtime_generation'] is False and CATALOG['shared_across_years'] is True
    total=0;thumbnails=0;hashes=set()
    for month in CATALOG['months']:
        for variant,limit in [('thumb',24_000),('detail',100_000)]:
            item=month[variant]
            payload=(STATIC / ART.removeprefix('/static/') / item['file']).read_bytes()
            assert len(payload)==item['bytes'] and len(payload)<=limit
            assert hashlib.sha256(payload).hexdigest()==item['sha256']
            with Image.open(BytesIO(payload)) as image:
                assert image.format=='WEBP' and image.size==(item['width'],item['height'])
                image.verify()
            total+=len(payload)
            if variant=='thumb':thumbnails+=len(payload);hashes.add(item['sha256'])
    assert len(hashes)==12 and thumbnails==CATALOG['all_thumbnails_bytes'] and thumbnails<200_000
    assert total==CATALOG['total_bytes'] and total<1_500_000


@pytest.mark.parametrize('variant',['thumb','detail'])
def test_static_art_uses_long_cache_and_etag_without_sql(variant):
    queries=[]
    def record(*args):queries.append(args[2])
    event.listen(engine,'before_cursor_execute',record)
    try:
        # Without a context manager: deliberately do not run app startup/workers.
        client=TestClient(app)
        response=client.get(f'{ART}/01-{variant}.webp')
        assert response.status_code==200 and response.headers['content-type']=='image/webp'
        assert response.headers['cache-control']=='public, max-age=31536000, immutable'
        assert 'etag' in response.headers
        cached=client.get(f'{ART}/01-{variant}.webp',headers={'If-None-Match':response.headers['etag']})
        assert cached.status_code==304 and not cached.content
        assert not queries, 'Artwork requests must never touch the database'
        client.close()
    finally:
        event.remove(engine,'before_cursor_execute',record)


def test_missing_art_is_not_cached_as_success():
    client=TestClient(app)
    response=client.get(f'{ART}/missing.webp')
    assert response.status_code==404 and 'immutable' not in response.headers.get('cache-control','')
    client.close()
