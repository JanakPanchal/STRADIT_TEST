import re
import socketserver
import threading
from datetime import timedelta

from app.core.config import get_settings
from app.core.security import utcnow
from app.services import mail
from tests.conftest import login, make_candidate, new_client

API = "/api/v1"


def token_from(msg):
    return re.search(r"/reset\?token=([\w\-]+)", msg.text).group(1)


def register(client, email="a@x.com", password="Password123", name="Asha"):
    client.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name})


def test_forgot_password_end_to_end(client):
    register(client)
    r = client.post(f"{API}/auth/forgot", json={"email": "A@x.com"})
    assert r.status_code == 200 and len(mail.OUTBOX) == 1
    msg = mail.OUTBOX[0]
    assert msg.to == "a@x.com" and "http://testserver/reset?token=" in msg.text and "30 minutes" in msg.text
    tok = token_from(msg)
    assert client.get(f"{API}/auth/reset-check", params={"token": tok}).json()["data"] == {"valid": True, "purpose": "reset"}
    assert client.post(f"{API}/auth/reset", json={"token": tok, "new_password": "BrandNew-Pass9"}).status_code == 200
    login(client, "a@x.com", "BrandNew-Pass9")
    assert client.post(f"{API}/auth/login", data={"username": "a@x.com", "password": "Password123"}).status_code == 401
    # single use
    again = client.post(f"{API}/auth/reset", json={"token": tok, "new_password": "Another-Pass99"})
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_link"
    assert client.get(f"{API}/auth/reset-check", params={"token": tok}).json()["data"]["valid"] is False


def test_unknown_email_gets_the_same_answer_and_no_mail(client):
    register(client)
    known = client.post(f"{API}/auth/forgot", json={"email": "a@x.com"}).json()
    mail.OUTBOX.clear()
    unknown = client.post(f"{API}/auth/forgot", json={"email": "nobody@x.com"}).json()
    assert known == unknown and mail.OUTBOX == []        # nothing reveals which addresses exist


def test_cooldown_limits_mail_bombing(client):
    register(client)
    for _ in range(4):
        client.post(f"{API}/auth/forgot", json={"email": "a@x.com"})
    assert len(mail.OUTBOX) == 1


def test_expired_and_replaced_tokens_are_rejected(client, db_session):
    from app.models import PasswordToken
    register(client)
    client.post(f"{API}/auth/forgot", json={"email": "a@x.com"})
    old = token_from(mail.OUTBOX[0])
    with db_session() as s:   # expire it
        s.query(PasswordToken).update({"expires_at": utcnow() - timedelta(minutes=1)})
        s.commit()
    assert client.post(f"{API}/auth/reset", json={"token": old, "new_password": "BrandNew-Pass9"}).status_code == 400
    with db_session() as s:   # lift the cooldown, ask again: the new link works and replaces the old
        s.query(PasswordToken).update({"created_at": utcnow() - timedelta(minutes=5)})
        s.commit()
    client.post(f"{API}/auth/forgot", json={"email": "a@x.com"})
    new = token_from(mail.OUTBOX[-1])
    assert new != old and client.post(f"{API}/auth/reset", json={"token": new, "new_password": "BrandNew-Pass9"}).status_code == 200


def test_reset_unlocks_account_signs_out_everywhere_and_rejects_weak_passwords(client):
    register(client)
    client.post(f"{API}/auth/login", data={"username": "a@x.com", "password": "Password123"})
    stolen = client.cookies.get("refresh_token")
    for _ in range(5):
        client.post(f"{API}/auth/login", data={"username": "a@x.com", "password": "wrong"})
    assert client.post(f"{API}/auth/login", data={"username": "a@x.com", "password": "Password123"}).status_code == 423   # locked
    client.post(f"{API}/auth/forgot", json={"email": "a@x.com"})
    tok = token_from(mail.OUTBOX[0])
    assert client.post(f"{API}/auth/reset", json={"token": tok, "new_password": "short"}).status_code == 422
    assert client.get(f"{API}/auth/reset-check", params={"token": tok}).json()["data"]["valid"] is True   # a weak attempt does not burn the link
    client.post(f"{API}/auth/reset", json={"token": tok, "new_password": "BrandNew-Pass9"})
    login(client, "a@x.com", "BrandNew-Pass9")                      # no longer locked
    old = new_client()
    old.cookies.set("refresh_token", stolen, path="/api/v1/auth")
    assert old.post(f"{API}/auth/refresh").status_code == 401      # old sessions are gone


def test_inactive_account_gets_no_reset_mail(client, admin):
    cid = make_candidate(client, admin, email="gone@x.com")
    client.put(f"{API}/admin/candidates/{cid}", headers=admin, json={"email": "gone@x.com", "name": "g", "is_active": False})
    client.post(f"{API}/auth/forgot", json={"email": "gone@x.com"})
    assert mail.OUTBOX == []


def test_invitations_flow(client, admin):
    ids = [make_candidate(client, admin, email=f"i{n}@x.com") for n in range(3)]
    r = client.post(f"{API}/admin/candidates/invite", headers=admin, json={"ids": ids[:2]}).json()["data"]
    assert r["queued"] == 2 and r["backend"] == "memory" and len(mail.OUTBOX) == 2
    msg = next(m for m in mail.OUTBOX if m.to == "i0@x.com")
    assert "http://testserver/login" in msg.text and "7 days" in msg.text and "Password123" not in msg.text   # never emails a password
    tok = token_from(msg)
    assert client.get(f"{API}/auth/reset-check", params={"token": tok}).json()["data"]["purpose"] == "invite"
    client.post(f"{API}/auth/reset", json={"token": tok, "new_password": "My-Own-Pass-1"})
    h = login(client, "i0@x.com", "My-Own-Pass-1")
    assert client.get(f"{API}/auth/me", headers=h).json()["data"]["must_change_password"] is False
    # sign-in tracking + filter + "everyone who hasn't signed in"
    rows = {c["email"]: c for c in client.get(f"{API}/admin/candidates", headers=admin).json()["data"]}
    assert rows["i0@x.com"]["last_login_at"] and rows["i0@x.com"]["invited_at"] and rows["i2@x.com"]["last_login_at"] is None and rows["i2@x.com"]["invited_at"] is None
    never = client.get(f"{API}/admin/candidates", headers=admin, params={"access": "never"}).json()
    assert {c["email"] for c in never["data"]} == {"i1@x.com", "i2@x.com"} and never["meta"]["counts"]["never_signed_in"] == 2
    assert client.get(f"{API}/admin/candidates", headers=admin, params={"access": "signed_in"}).json()["meta"]["total"] == 1
    mail.OUTBOX.clear()
    r2 = client.post(f"{API}/admin/candidates/invite", headers=admin, json={"all_pending": True}).json()["data"]
    assert r2["queued"] == 2 and {m.to for m in mail.OUTBOX} == {"i1@x.com", "i2@x.com"}   # not the one who already signed in


def test_reinviting_replaces_the_old_link(client, admin):
    cid = make_candidate(client, admin, email="r@x.com")
    client.post(f"{API}/admin/candidates/invite", headers=admin, json={"ids": [cid]})
    first = token_from(mail.OUTBOX[-1])
    client.post(f"{API}/admin/candidates/invite", headers=admin, json={"ids": [cid]})
    second = token_from(mail.OUTBOX[-1])
    assert client.get(f"{API}/auth/reset-check", params={"token": first}).json()["data"]["valid"] is False
    assert client.get(f"{API}/auth/reset-check", params={"token": second}).json()["data"]["valid"] is True


def test_invite_rules_and_html_escaping(client, admin):
    assert client.post(f"{API}/admin/candidates/invite", headers=admin, json={}).status_code == 422
    cid = client.post(f"{API}/admin/candidates", headers=admin, json={"email": "x@x.com", "name": '<script>alert(1)</script>', "password": "Password123"}).json()["data"]["id"]
    client.post(f"{API}/admin/candidates/invite", headers=admin, json={"ids": [cid]})
    m = mail.OUTBOX[-1]
    assert "<script>" not in m.html and "&lt;script&gt;" in m.html and "<script>" in m.text     # escaped in HTML, plain in text
    client.post(f"{API}/auth/register", json={"email": "s@x.com", "password": "Password123", "name": "S"})
    h = login(client, "s@x.com", "Password123")
    assert client.post(f"{API}/admin/candidates/invite", headers=h, json={"ids": [cid]}).status_code == 403
    client.put(f"{API}/admin/candidates/{cid}", headers=admin, json={"email": "x@x.com", "name": "x", "is_active": False})
    mail.OUTBOX.clear()
    r = client.post(f"{API}/admin/candidates/invite", headers=admin, json={"ids": [cid]}).json()["data"]
    assert r["queued"] == 0 and r["skipped_inactive"] == 1 and mail.OUTBOX == []


def test_smtp_backend_delivers_a_real_message(monkeypatch):
    got = {}

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            w = lambda t: self.wfile.write((t + "\r\n").encode())
            w("220 fake ESMTP")
            data, in_data = [], False
            for raw in self.rfile:
                line = raw.decode().rstrip("\r\n")
                if in_data:
                    if line == ".":
                        in_data = False; got["body"] = "\n".join(data); w("250 queued")
                    else:
                        data.append(line)
                    continue
                cmd = line.split(" ")[0].upper()
                if cmd == "EHLO": w("250 fake")
                elif cmd == "MAIL": got["from"] = line; w("250 ok")
                elif cmd == "RCPT": got["rcpt"] = line; w("250 ok")
                elif cmd == "DATA": in_data = True; w("354 go")
                elif cmd == "QUIT": w("221 bye"); return
                else: w("250 ok")

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = get_settings()
    for k, v in dict(mail_backend="smtp", smtp_host="127.0.0.1", smtp_port=srv.server_address[1], smtp_starttls=False, smtp_user="").items():
        monkeypatch.setattr(s, k, v)
    mail.send(mail.reset_email("a@x.com", "Asha", "http://h/reset?token=abc123", 30))
    srv.shutdown()
    import email
    parsed = email.message_from_string(got["body"])                      # decode it the way a mail client would
    plain = next(p for p in parsed.walk() if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
    html = next(p for p in parsed.walk() if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    assert "a@x.com" in got["rcpt"] and parsed["Subject"] == "Reset your password" and parsed["To"] == "a@x.com"
    assert "http://h/reset?token=abc123" in plain and 'href="http://h/reset?token=abc123"' in html


def test_mail_failure_never_breaks_the_request(client, monkeypatch):
    register(client)
    monkeypatch.setattr(get_settings(), "mail_backend", "smtp")
    monkeypatch.setattr(get_settings(), "smtp_host", "")           # misconfigured
    assert client.post(f"{API}/auth/forgot", json={"email": "a@x.com"}).status_code == 200
