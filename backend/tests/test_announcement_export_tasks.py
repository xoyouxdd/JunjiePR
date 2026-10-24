"""Background announcement exports retain publisher scope and original output."""
from test_announcements import app, client, login, publish, ok
from fastapi.testclient import TestClient
import time


def test_real_report_owner_isolation_and_new_session(client):
    login(client)
    announcement=publish(client)
    aid=announcement['id']
    r=client.post('/api/export-tasks',params={'target':f'/api/announcements/{aid}/export?version=1&format=xlsx'})
    assert r.status_code==202,r.text
    key=r.json()['id']
    for _ in range(200):
        row=next(j for j in ok(client.get('/api/export-tasks'))['items'] if j['id']==key)
        if row['status'] in {'completed','failed'}:break
        time.sleep(.05)
    assert row['status']=='completed',row
    response=client.get(row['download_url']);assert response.status_code==200,response.text
    assert response.content.startswith(b'PK')
    # Ownership remains a necessary condition even when another account can
    # itself view or export this announcement through the original API.
    with TestClient(app) as other:
        login(other,'HR01')
        assert other.get(row['download_url']).status_code==404
    # A new session of the same owner can retrieve the task after page reload.
    with TestClient(app) as same:
        login(same)
        assert any(j['id']==key for j in ok(same.get('/api/export-tasks'))['items'])
        assert same.get(row['download_url']).status_code==200
