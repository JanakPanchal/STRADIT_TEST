import re

from app.services import mail
from tests.conftest import add_q, login, make_candidate, make_test, mcq, new_client

API = "/api/v1"


def token_from(msg):
    return re.search(r"/reset\?token=([\w\-]+)", msg.text).group(1)


def add_staff(client, admin, email, role, name="Team Member", password="Staff-Pass-12345"):
    r = client.post(f"{API}/admin/staff", headers=admin, json={"email": email, "full_name": name, "role": role})
    assert r.status_code == 201, r.text
    msg = mail.OUTBOX[-1]
    assert client.post(f"{API}/auth/reset", json={"token": token_from(msg), "new_password": password}).status_code == 200
    return r.json()["data"]["id"], login(client, email, password)


def test_new_member_gets_an_invitation_never_a_password(client, admin):
    r = client.post(f"{API}/admin/staff", headers=admin, json={"email": "Priya@x.com", "full_name": "Priya N", "role": "viewer"})
    assert r.status_code == 201 and r.json()["data"]["email"] == "priya@x.com"
    m = mail.OUTBOX[-1]
    assert m.to == "priya@x.com" and "view-only" in m.text and "/reset?token=" in m.text and "assessment team" in m.subject
    # cannot sign in until they set their own password
    assert client.post(f"{API}/auth/login", data={"username": "priya@x.com", "password": "anything-123"}).status_code == 401
    assert client.post(f"{API}/admin/staff", headers=admin, json={"email": "priya@x.com", "full_name": "x", "role": "admin"}).status_code == 409
    assert client.post(f"{API}/admin/staff", headers=admin, json={"email": "z@x.com", "full_name": "z", "role": "superuser"}).status_code == 422


def test_viewer_can_read_but_not_change_anything(client, admin):
    tid = make_test(client, admin, "aptitude")
    add_q(client, admin, tid, mcq())
    cid = make_candidate(client, admin)
    _, v = add_staff(client, admin, "view@x.com", "viewer")
    for path in ("/admin/tests", f"/admin/tests/{tid}", f"/admin/tests/{tid}/questions", f"/admin/tests/{tid}/summary", f"/admin/tests/{tid}/analytics",
                 "/admin/candidates", f"/admin/candidates/{cid}/results", "/admin/colleges", "/admin/audit", "/admin/staff", f"/admin/results/export?test_id={tid}"):
        assert client.get(API + path, headers=v).status_code == 200, path
    blocked = [("post", "/admin/tests", {"title": "t", "stage": "aptitude", "duration_min": 5}), ("put", f"/admin/tests/{tid}", {"title": "t", "stage": "aptitude", "duration_min": 5}),
               ("delete", f"/admin/tests/{tid}", None), ("post", f"/admin/tests/{tid}/publish", None), ("post", f"/admin/tests/{tid}/questions", mcq()),
               ("post", f"/admin/candidates/{cid}/disqualify", None), ("delete", f"/admin/candidates/{cid}", None), ("post", f"/admin/candidates/{cid}/reset-password", None),
               ("post", "/admin/candidates/invite", {"all_pending": True}), ("post", "/admin/colleges", {"name": "Zed College", "code": "ZED"}),
               ("post", "/admin/staff", {"email": "n@x.com", "full_name": "n", "role": "admin"})]
    for method, path, body in blocked:
        r = getattr(client, method)(API + path, headers=v, **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and r.json()["error"]["code"] in ("read_only", "forbidden"), (method, path, r.status_code)
    assert client.get(f"{API}/admin/tests/{tid}", headers=admin).json()["data"]["title"] == "aptitude test"   # nothing changed
    assert client.get(f"{API}/tests", headers=v).status_code == 403          # viewers are not candidates either


def test_viewer_cannot_use_staff_management(client, admin):
    _, v = add_staff(client, admin, "view@x.com", "viewer")
    uid = client.get(f"{API}/admin/staff", headers=admin).json()["data"][0]["id"]
    assert client.put(f"{API}/admin/staff/{uid}", headers=v, json={"full_name": "x", "role": "admin", "is_active": True}).status_code == 403
    assert client.delete(f"{API}/admin/staff/{uid}", headers=v).status_code == 403
    assert client.post(f"{API}/admin/staff/{uid}/send-link", headers=v).status_code == 403


def test_second_admin_has_full_power_and_role_changes_apply(client, admin):
    bid, b = add_staff(client, admin, "second@x.com", "admin")
    assert client.post(f"{API}/admin/tests", headers=b, json={"title": "By B", "stage": "aptitude", "duration_min": 5}).status_code == 201
    assert client.put(f"{API}/admin/staff/{bid}", headers=admin, json={"full_name": "Second", "role": "viewer", "is_active": True}).status_code == 200
    assert client.post(f"{API}/admin/tests", headers=b, json={"title": "again", "stage": "aptitude", "duration_min": 5}).status_code == 403   # demoted at once


def test_cannot_lock_yourself_out_or_remove_yourself(client, admin):
    bid, b = add_staff(client, admin, "second@x.com", "admin")
    me = next(s for s in client.get(f"{API}/admin/staff", headers=b).json()["data"] if s["email"] == "second@x.com")
    for body in ({"full_name": "x", "role": "viewer", "is_active": True}, {"full_name": "x", "role": "admin", "is_active": False}):
        r = client.put(f"{API}/admin/staff/{me['id']}", headers=b, json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "self_lockout"
    assert client.delete(f"{API}/admin/staff/{me['id']}", headers=b).status_code == 409
    assert client.put(f"{API}/admin/staff/{me['id']}", headers=b, json={"full_name": "Renamed", "role": "admin", "is_active": True}).status_code == 200   # harmless edits are fine


def test_deactivating_cuts_access_at_once(client, admin):
    uid, v = add_staff(client, admin, "view@x.com", "viewer")
    assert client.get(f"{API}/admin/tests", headers=v).status_code == 200
    client.put(f"{API}/admin/staff/{uid}", headers=admin, json={"full_name": "x", "role": "viewer", "is_active": False})
    assert client.get(f"{API}/admin/tests", headers=v).status_code == 401
    assert client.post(f"{API}/auth/login", data={"username": "view@x.com", "password": "Staff-Pass-12345"}).status_code == 401
    assert client.post(f"{API}/admin/staff/{uid}/send-link", headers=admin).status_code == 409   # reactivate first


def test_send_link_and_remove(client, admin):
    uid, _ = add_staff(client, admin, "view@x.com", "viewer")
    mail.OUTBOX.clear()
    assert client.post(f"{API}/admin/staff/{uid}/send-link", headers=admin).status_code == 200 and len(mail.OUTBOX) == 1
    assert client.delete(f"{API}/admin/staff/{uid}", headers=admin).status_code == 200
    assert client.post(f"{API}/auth/login", data={"username": "view@x.com", "password": "Staff-Pass-12345"}).status_code == 401
    make_candidate(client, admin)
    cand_user_id = client.get(f"{API}/auth/me", headers=login(client, "c1@x.com", "Password123")).json()["data"]["id"]   # a USER id, not a candidate id
    assert client.put(f"{API}/admin/staff/{cand_user_id}", headers=admin, json={"full_name": "x", "role": "admin", "is_active": True}).status_code == 404   # candidates are not staff


def test_sign_out_everywhere(client, admin):
    client.post(f"{API}/auth/register", json={"email": "a@x.com", "password": "Password123", "name": "A"})
    c1, c2 = new_client(), new_client()
    for c in (c1, c2):
        assert c.post(f"{API}/auth/login", data={"username": "a@x.com", "password": "Password123"}).status_code == 200
    h = {"Authorization": "Bearer " + c1.post(f"{API}/auth/refresh").json()["data"]["access_token"]}
    assert c1.post(f"{API}/auth/logout-all", headers=h).json()["data"]["signed_out_sessions"] >= 2
    assert c2.post(f"{API}/auth/refresh").status_code == 401 and c1.post(f"{API}/auth/refresh").status_code == 401
    assert client.post(f"{API}/auth/logout-all").status_code == 401
