import io

from tests.conftest import login, make_candidate

API = "/api/v1"
COLLEGE = {"name": "Indian Institute Demo", "code": "iid", "city": "Pune", "state": "MH", "contact_name": "Dr Rao",
           "contact_email": "tpo@iid.edu", "contact_phone": "+91 98765 43210", "website": "https://iid.edu"}


def mk(client, admin, **kw):
    r = client.post(f"{API}/admin/colleges", headers=admin, json={**COLLEGE, **kw})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_create_validates_and_rejects_duplicates(client, admin):
    c = mk(client, admin)
    assert c["code"] == "IID" and c["candidate_count"] == 0
    assert client.post(f"{API}/admin/colleges", headers=admin, json=COLLEGE).status_code == 409
    assert client.post(f"{API}/admin/colleges", headers=admin, json={**COLLEGE, "name": "Other"}).status_code == 409  # same code
    assert client.post(f"{API}/admin/colleges", headers=admin, json={**COLLEGE, "name": "X1", "code": "bad code!"}).status_code == 422
    assert client.post(f"{API}/admin/colleges", headers=admin, json={**COLLEGE, "name": "X2", "code": "X2", "contact_email": "nope"}).status_code == 422
    assert client.post(f"{API}/admin/colleges", headers=admin, json={**COLLEGE, "name": "X3", "code": "X3", "contact_phone": "abc"}).status_code == 422


def test_admin_only(client, admin):
    client.post(f"{API}/auth/register", json={"email": "a@x.com", "password": "Password123", "name": "A"})
    h = login(client, "a@x.com", "Password123")
    assert client.post(f"{API}/admin/colleges", headers=h, json=COLLEGE).status_code == 403
    assert client.get(f"{API}/admin/colleges", headers=h).status_code == 403


def test_public_list_is_minimal_and_active_only(client, admin):
    a = mk(client, admin)
    mk(client, admin, name="Hidden College", code="HID", is_active=False)
    r = client.get(f"{API}/colleges")  # no auth: used by the registration form
    assert r.status_code == 200 and r.json()["data"] == [{"id": a["id"], "name": a["name"]}]
    assert "contact_email" not in r.text


def test_registration_requires_a_listed_college_once_any_exist(client, admin):
    reg = {"email": "s@x.com", "password": "Password123", "name": "S"}
    assert client.post(f"{API}/auth/register", json=reg).status_code == 201          # no colleges onboarded yet
    c = mk(client, admin)
    assert client.post(f"{API}/auth/register", json={**reg, "email": "s2@x.com"}).status_code == 422
    assert client.post(f"{API}/auth/register", json={**reg, "email": "s3@x.com", "college_id": 9999}).status_code == 422
    hidden = mk(client, admin, name="Hidden", code="HID", is_active=False)
    assert client.post(f"{API}/auth/register", json={**reg, "email": "s4@x.com", "college_id": hidden["id"]}).status_code == 422
    assert client.post(f"{API}/auth/register", json={**reg, "email": "s5@x.com", "college_id": c["id"]}).status_code == 201
    h = login(client, "s5@x.com", "Password123")
    cand = client.get(f"{API}/admin/candidates", headers=admin, params={"search": "s5@"}).json()["data"][0]
    assert cand["college"] == c["name"] and cand["college_id"] == c["id"]


def test_edit_college_and_candidate_college_filter(client, admin):
    c1, c2 = mk(client, admin), mk(client, admin, name="Second College", code="SEC")
    for em, cid in (("a@x.com", c1["id"]), ("b@x.com", c1["id"]), ("c@x.com", c2["id"])):
        client.post(f"{API}/admin/candidates", headers=admin, json={"email": em, "name": em[0], "college_id": cid, "password": "Password123"})
    rows = client.get(f"{API}/admin/candidates", headers=admin, params={"college_id": c1["id"]}).json()
    assert rows["meta"]["total"] == 2
    listed = {c["id"]: c["candidate_count"] for c in client.get(f"{API}/admin/colleges", headers=admin).json()["data"]}
    assert listed[c1["id"]] == 2 and listed[c2["id"]] == 1
    r = client.put(f"{API}/admin/colleges/{c1['id']}", headers=admin, json={**COLLEGE, "city": "Mumbai"})
    assert r.json()["data"]["city"] == "Mumbai" and r.json()["data"]["candidate_count"] == 2
    assert client.put(f"{API}/admin/colleges/{c1['id']}", headers=admin, json={**COLLEGE, "name": "Second College", "code": "SEC"}).status_code == 409
    opts = client.get(f"{API}/admin/candidates/filters", headers=admin).json()["data"]["colleges"]
    assert {o["name"] for o in opts} == {c1["name"], "Second College"}


def test_candidate_create_and_edit_with_college(client, admin):
    c = mk(client, admin)
    r = client.post(f"{API}/admin/candidates", headers=admin, json={"email": "z@x.com", "name": "Z", "college_id": c["id"]}).json()["data"]
    assert r["college"] == c["name"]
    assert client.post(f"{API}/admin/candidates", headers=admin, json={"email": "y@x.com", "name": "Y", "college_id": 999}).status_code == 422
    body = {"email": "z@x.com", "name": "Z", "college_id": None, "is_active": True}
    assert client.put(f"{API}/admin/candidates/{r['id']}", headers=admin, json=body).json()["data"]["college"] == ""


def test_bulk_csv_matches_college_by_name_or_code_and_rejects_unknown(client, admin):
    mk(client, admin)
    csv_ = ("email,name,college,branch,batch\n" "a@x.com,A,Indian Institute Demo,CS,B\n" "b@x.com,B,IID,CS,B\n"
            "c@x.com,C,Nowhere Univ,CS,B\n" "d@x.com,D,,CS,B\n")
    r = client.post(f"{API}/admin/candidates/bulk", headers=admin, files={"file": ("c.csv", io.BytesIO(csv_.encode()))}).json()["data"]
    assert len(r["created"]) == 3 and len(r["skipped"]) == 1 and "unknown college" in r["skipped"][0]["reason"]
    assert {c["college"] for c in r["created"]} == {"Indian Institute Demo", ""}


def test_delete_college_blocked_while_in_use_or_moved(client, admin):
    a, b = mk(client, admin), mk(client, admin, name="B College", code="BCO")
    cid = client.post(f"{API}/admin/candidates", headers=admin, json={"email": "z@x.com", "name": "Z", "college_id": a["id"]}).json()["data"]["id"]
    r = client.delete(f"{API}/admin/colleges/{a['id']}", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["details"]["candidates"] == 1
    assert client.delete(f"{API}/admin/colleges/{a['id']}", headers=admin, params={"move_to": a["id"]}).status_code == 422
    r = client.delete(f"{API}/admin/colleges/{a['id']}", headers=admin, params={"move_to": b["id"]})
    assert r.status_code == 200 and r.json()["data"]["moved_candidates"] == 1
    assert client.get(f"{API}/admin/candidates", headers=admin).json()["data"][0]["college"] == "B College"
    assert client.delete(f"{API}/admin/colleges/{b['id']}", headers=admin, params={"move_to": 12345}).status_code == 422
    client.delete(f"{API}/admin/candidates/{cid}", headers=admin)
    assert client.delete(f"{API}/admin/colleges/{b['id']}", headers=admin).status_code == 200
