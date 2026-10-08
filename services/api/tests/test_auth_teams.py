"""Acceptance 1, 2, 16 and auth security behaviour."""

from fastapi.testclient import TestClient

from .conftest import Session, register


def test_register_login_create_project(client: TestClient) -> None:
    s = register(client, "new@example.com", "New User")
    r = client.post("/api/v1/auth/login", json={"email": "NEW@example.com", "password": "correct horse battery"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    orgs = client.get("/api/v1/orgs", headers=h).json()
    assert len(orgs) == 1 and orgs[0]["role"] == "owner" and orgs[0]["is_personal"]
    r = client.post("/api/v1/projects", headers=h, json={"org_id": orgs[0]["id"], "name": "Robot arm"})
    assert r.status_code == 201 and r.json()["role"] == "owner"
    assert [p["name"] for p in client.get(f"/api/v1/projects?org_id={orgs[0]['id']}", headers=h).json()] == ["Robot arm"]
    assert s.get("/api/v1/auth/me").json()["email"] == "new@example.com"


def test_bad_credentials_and_duplicate_email(client: TestClient, alice: Session) -> None:
    assert client.post("/api/v1/auth/login", json={"email": alice.email, "password": "wrong password!"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever123"}).status_code == 401
    r = client.post("/api/v1/auth/register", json={"email": alice.email, "password": "another password", "display_name": "x"})
    assert r.status_code == 409
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_team_invite_roles_and_permissions(client: TestClient, alice: Session) -> None:
    team = alice.post("/api/v1/orgs", json={"name": "Robotics Team"}).json()
    inv = alice.post(f"/api/v1/orgs/{team['id']}/invitations", json={"email": "carol@example.com", "role": "viewer"})
    assert inv.status_code == 201 and inv.json()["token"]
    carol = register(client, "carol@example.com", "Carol", invitation_token=inv.json()["token"])
    my_orgs = {o["id"]: o["role"] for o in carol.get("/api/v1/orgs").json()}
    assert my_orgs[team["id"]] == "viewer"
    # viewers cannot create projects or invite
    assert carol.post("/api/v1/projects", json={"org_id": team["id"], "name": "x"}).status_code == 403
    assert carol.post(f"/api/v1/orgs/{team['id']}/invitations", json={"email": "d@example.com"}).status_code == 403
    # but can see org projects
    alice.post("/api/v1/projects", json={"org_id": team["id"], "name": "Shared"})
    assert [p["name"] for p in carol.get(f"/api/v1/projects?org_id={team['id']}").json()] == ["Shared"]
    # owner promotes to editor -> can create
    r = alice.patch(f"/api/v1/orgs/{team['id']}/members/{carol.user['id']}", json={"role": "editor"})
    assert r.status_code == 200 and r.json()["role"] == "editor"
    assert carol.post("/api/v1/projects", json={"org_id": team["id"], "name": "Mine"}).status_code == 201
    # editors cannot change roles; owner cannot be demoted when last owner
    assert carol.patch(f"/api/v1/orgs/{team['id']}/members/{alice.user['id']}", json={"role": "viewer"}).status_code == 403
    assert alice.patch(f"/api/v1/orgs/{team['id']}/members/{alice.user['id']}", json={"role": "admin"}).status_code == 409
    # invitation tokens are single-use and bound to the email
    assert client.post("/api/v1/auth/register", json={"email": "eve@example.com", "password": "correct horse battery", "display_name": "Eve", "invitation_token": inv.json()["token"]}).status_code in (400, 403)
    audit = alice.get(f"/api/v1/admin/audit?org_id={team['id']}").json()
    assert {"org.invite", "org.member_role_changed"} <= {a["action"] for a in audit}


def test_private_project_sharing(client: TestClient, alice: Session) -> None:
    team = alice.post("/api/v1/orgs", json={"name": "T"}).json()
    inv = alice.post(f"/api/v1/orgs/{team['id']}/invitations", json={"email": "dan@example.com", "role": "editor"}).json()
    dan = register(client, "dan@example.com", "Dan", invitation_token=inv["token"])
    secret = alice.post("/api/v1/projects", json={"org_id": team["id"], "name": "Secret", "visibility": "private"}).json()
    assert dan.get(f"/api/v1/projects/{secret['id']}").status_code == 404
    assert secret["name"] not in [p["name"] for p in dan.get(f"/api/v1/projects?org_id={team['id']}").json()]
    assert alice.post(f"/api/v1/projects/{secret['id']}/members", json={"user_id": dan.user["id"], "role": "viewer"}).status_code == 204
    assert dan.get(f"/api/v1/projects/{secret['id']}").json()["role"] == "viewer"
    assert dan.patch(f"/api/v1/projects/{secret['id']}", json={"name": "hacked"}).status_code == 403


def test_cookie_auth_requires_csrf_and_refresh_rotation(client: TestClient) -> None:
    r = client.post("/api/v1/auth/register", json={"email": "cookie@example.com", "password": "correct horse battery", "display_name": "C"})
    csrf = r.json()["csrf_token"]
    org_id = client.get("/api/v1/orgs").json()[0]["id"]  # cookie-authenticated GET works
    assert client.post("/api/v1/projects", json={"org_id": org_id, "name": "x"}).status_code == 403  # missing CSRF header
    assert client.post("/api/v1/projects", json={"org_id": org_id, "name": "x"}, headers={"X-CSRF-Token": csrf}).status_code == 201
    old_refresh = r.json()["refresh_token"]
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 200 and r2.json()["refresh_token"] != old_refresh
    # reusing the rotated token is treated as theft: everything is revoked
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": r2.json()["refresh_token"]}).status_code == 401


def test_cross_org_isolation(client: TestClient, alice: Session, bob: Session) -> None:
    proj = alice.post("/api/v1/projects", json={"org_id": alice.org_id, "name": "Alice private"}).json()
    up = alice.post("/api/v1/files", data={"project_id": proj["id"]}, files={"file": ("notes.txt", b"alice secret notes", "text/plain")}).json()
    conv = alice.post("/api/v1/conversations", json={"org_id": alice.org_id}).json()
    mem = alice.post("/api/v1/memory", json={"org_id": alice.org_id, "content": "alice likes titanium", "scope": "team"}).json()
    # Bob sees none of it, and cannot even confirm it exists
    assert bob.get(f"/api/v1/projects/{proj['id']}").status_code == 404
    assert bob.get(f"/api/v1/projects?org_id={alice.org_id}").status_code == 404
    assert bob.get(f"/api/v1/files/{up['id']}").status_code == 404
    assert bob.get(f"/api/v1/files/{up['id']}/download").status_code == 404
    assert bob.get(f"/api/v1/conversations/{conv['id']}").status_code == 404
    assert bob.post("/api/v1/files", data={"project_id": proj["id"]}, files={"file": ("x.txt", b"x", "text/plain")}).status_code == 404
    assert bob.patch(f"/api/v1/memory/{mem['id']}", json={"content": "x"}).status_code == 404
    assert bob.get(f"/api/v1/memory/search?org_id={bob.org_id}&q=titanium").json() == []
    assert bob.post("/api/v1/cad/generate", json={"project_id": proj["id"], "template": "standoff"}).status_code == 404
    assert bob.get(f"/api/v1/artifacts?org_id={alice.org_id}").status_code == 404


def test_upload_validation(alice: Session) -> None:
    assert alice.post("/api/v1/files", data={"org_id": alice.org_id}, files={"file": ("evil.exe", b"MZ...", "application/octet-stream")}).status_code == 415
    assert alice.post("/api/v1/files", data={"org_id": alice.org_id}, files={"file": ("fake.pdf", b"not a pdf", "application/pdf")}).status_code == 415
    assert alice.post("/api/v1/files", data={"org_id": alice.org_id}, files={"file": ("empty.txt", b"", "text/plain")}).status_code == 422
    r = alice.post("/api/v1/files", data={"org_id": alice.org_id}, files={"file": ("../../etc/passwd.txt", b"hello", "text/plain")})
    assert r.status_code == 201 and r.json()["path"] == "passwd.txt"
    d = alice.get(f"/api/v1/files/{r.json()['id']}/download")
    assert d.content == b"hello" and d.headers["content-disposition"].startswith("attachment") and d.headers["x-content-type-options"] == "nosniff"


def test_auth_rate_limit(client: TestClient) -> None:
    codes = [client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "wrong-password"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and 429 in codes[10:]
