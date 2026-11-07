"""Prize-photo normalization, ownership, lifecycle and private delivery."""
from datetime import datetime, timedelta
from io import BytesIO
import uuid
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app.services.activity_photos import normalize_photo, MAX_BYTES, MAX_SIDE
from test_supervisor_performance import app, SessionLocal, login, employee_id
from test_activities import payload, post, find
from app.activities_models import Activity, ActivityPrizePhoto
from app.v2_models import Employee, Attraction


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def picture(size=(2400, 1800), fmt='PNG', color=(40, 90, 150)):
    image = Image.new('RGB', size, color)
    output = BytesIO()
    image.save(output, fmt)
    return output.getvalue()


def upload(client, data=None):
    response = client.post('/api/activities/photos', files={'file': ('camera.png', picture() if data is None else data, 'image/png')})
    assert response.status_code == 200, response.text
    return response.json()


def test_compression_size_orientation_and_metadata():
    image = Image.new('RGB', (2400, 1800), (10, 80, 160))
    exif = image.getexif()
    exif[274], exif[270] = 6, 'PRIVATE_CAMERA_METADATA'
    output = BytesIO()
    image.save(output, 'JPEG', exif=exif)
    data, width, height = normalize_photo(output.getvalue())
    assert (width, height) == (1200, 1600)
    assert len(data) <= MAX_BYTES and max(width, height) <= MAX_SIDE
    with Image.open(BytesIO(data)) as saved:
        assert saved.format == 'JPEG' and not saved.getexif()
    assert b'PRIVATE_CAMERA_METADATA' not in data


def test_noisy_image_compresses_and_transparent_background_is_white():
    noise = Image.effect_noise((2000, 2000), 100).convert('RGB')
    output = BytesIO()
    noise.save(output, 'PNG')
    data, width, height = normalize_photo(output.getvalue())
    assert len(data) <= MAX_BYTES and max(width, height) <= MAX_SIDE
    transparent = Image.new('RGBA', (10, 10), (0, 0, 0, 0))
    output = BytesIO()
    transparent.save(output, 'PNG')
    data, _, _ = normalize_photo(output.getvalue())
    with Image.open(BytesIO(data)) as saved:
        assert all(channel > 245 for channel in saved.getpixel((5, 5)))


@pytest.mark.parametrize('body', [b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', b'not a photograph'])
def test_upload_rejects_non_photos_and_requires_gsm(client, body):
    assert client.post('/api/activities/photos', files={'file': ('x.jpg', body)}).status_code == 401
    login(client, 'CMTEST01')
    assert client.post('/api/activities/photos', files={'file': ('x.jpg', body)}).status_code == 403
    login(client, 'GSMTEST01')
    assert client.post('/api/activities/photos', files={'file': ('x.jpg', body)}).status_code == 422


def test_input_byte_and_pixel_limits(client, monkeypatch):
    from app.services import activity_photos
    login(client, 'GSMTEST01')
    with monkeypatch.context() as context:
        context.setattr(activity_photos, 'MAX_UPLOAD', 32)
        assert client.post('/api/activities/photos', files={'file': ('x.jpg', b'x'*33)}).status_code == 413
    with monkeypatch.context() as context:
        context.setattr(activity_photos, 'MAX_PIXELS', 100)
        assert client.post('/api/activities/photos', files={'file': ('x.png', picture((20,20)))}).status_code == 413


@pytest.mark.parametrize('kind', ['draw', 'auction'])
def test_photo_binding_private_access_and_publication(client, kind):
    login(client, 'GSMTEST01')
    photo = upload(client)
    assert photo['bytes'] <= MAX_BYTES and photo['width'] <= MAX_SIDE
    assert client.get(photo['url']).status_code == 200
    body = payload(kind)
    body['lots'][0]['prizes'][0]['photo_id'] = photo['id']
    created = client.post('/api/activities', json=body)
    assert created.status_code == 200, created.text
    event_id = created.json()['id']
    draft = find(client, event_id)
    assert draft['lots'][0]['prizes'][0]['photo_url'] == photo['url']
    # A replay of draft creation retains the same photo and cannot create another activity.
    assert client.post('/api/activities', json=body).json() == created.json()
    login(client, 'CMTEST01')
    assert client.get(photo['url']).status_code == 404
    login(client, 'GSMTEST01')
    assert post(client, f'{event_id}/publish').status_code == 200
    login(client, 'CMTEST01')
    response = client.get(photo['url'])
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.headers['cache-control'] == 'private, no-cache'
    assert client.get(photo['url'], headers={'If-None-Match': response.headers['etag']}).status_code == 304
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (photo['width'], photo['height'])
    client.post('/api/logout')
    assert client.get(photo['url'], headers={'If-None-Match': response.headers['etag']}).status_code == 401


def test_foreign_or_reused_photo_rolls_back_whole_activity(client):
    login(client, 'GSMTEST01')
    photo = upload(client)
    body = payload()
    body['lots'][0]['prizes'][0]['photo_id'] = photo['id']
    login(client, 'AMTEST01')
    assert client.post('/api/activities', json=body).status_code == 422
    assert client.get(photo['url']).status_code == 404
    login(client, 'GSMTEST01')
    body['lots'][0]['prizes'][1]['photo_id'] = photo['id']
    assert client.post('/api/activities', json=body).status_code == 422
    with SessionLocal() as db:
        assert db.get(ActivityPrizePhoto, photo['id']).prize_id is None
        assert db.query(Activity).filter_by(title=body['title']).count() == 0
    del body['lots'][0]['prizes'][1]['photo_id']
    assert client.post('/api/activities', json=body).status_code == 200
    body['request_key'] = str(uuid.uuid4())
    assert client.post('/api/activities', json=body).status_code == 422


def test_cleanup_never_deletes_bound_photos(client):
    login(client, 'GSMTEST01')
    kept, abandoned = upload(client), upload(client)
    body = payload()
    body['lots'][0]['prizes'][0]['photo_id'] = kept['id']
    assert client.post('/api/activities', json=body).status_code == 200
    with SessionLocal() as db:
        for token in [kept['id'], abandoned['id']]:
            db.get(ActivityPrizePhoto, token).created_at = datetime.now()-timedelta(days=8)
        db.commit()
    upload(client)
    assert client.get(kept['url']).status_code == 200
    assert client.get(abandoned['url']).status_code == 404


def test_scoped_photo_not_visible_outside_event_scope(client):
    with SessionLocal() as db:
        own = db.get(Employee, employee_id('CMTEST01')).attraction_id
        other = db.query(Attraction).filter(Attraction.id != own, Attraction.employee_circle.is_(True), Attraction.active.is_(True)).first().id
    login(client, 'GSMTEST01')
    photo = upload(client)
    body = payload(circle_ids=[other])
    body['lots'][0]['prizes'][0]['photo_id'] = photo['id']
    created = client.post('/api/activities', json=body)
    assert created.status_code == 200, created.text
    assert post(client, f'{created.json()["id"]}/publish').status_code == 200
    login(client, 'CMTEST01')
    assert client.get(photo['url']).status_code == 404
