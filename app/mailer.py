"""Envoi et réception de courriels : SMTP/IMAP ou Microsoft 365 (Graph)."""
import base64
import email
import imaplib
import logging
import smtplib
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr

import requests

from . import db
from .models import get_setting, OutboundEmail

log = logging.getLogger("kubios.mail")
_graph_token = {"value": None, "exp": 0}


def backend():
    return get_setting("mail_backend") or "aucun"


# ----------------------------------------------------------------- envoi

def send_mail(to, subject, html, workorder_id=None, attachments=None, cc=None):
    """Envoie un courriel. attachments = [(nom, bytes, mimetype)]. Retourne True/False."""
    recipients = [t for t in ([to] if isinstance(to, str) else (to or [])) if t and "@" in t]
    cc = [c for c in ([cc] if isinstance(cc, str) else (cc or [])) if c and "@" in c]
    if not recipients:
        return False
    prefix = get_setting("company_name") or "Kubios"
    subject = f"[{prefix}] {subject}"
    ok, err = True, None
    try:
        b = backend()
        if b == "smtp_imap":
            _send_smtp(recipients, cc, subject, html, attachments or [])
        elif b == "graph":
            _send_graph(recipients, cc, subject, html, attachments or [])
        else:
            ok, err = False, "Courriel non configuré (Administration > Paramètres)"
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)
        log.exception("Échec d'envoi de courriel")
    db.session.add(OutboundEmail(to=", ".join(recipients + cc), subject=subject, ok=ok, error=err,
                                 workorder_id=workorder_id))
    db.session.commit()
    return ok


def wrap_html(body):
    company = get_setting("company_name") or "Kubios"
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:640px">
<div style="background:#1e3a5f;color:#fff;padding:12px 16px;font-weight:600">{company} — Gestion flotte et outils</div>
<div style="padding:16px;border:1px solid #e5e7eb;border-top:0">{body}</div>
<div style="font-size:11px;color:#6b7280;padding:8px 0">Courriel automatique — ne pas répondre directement sauf indication.</div></div>"""


def _send_smtp(to, cc, subject, html, attachments):
    msg = EmailMessage()
    msg["From"] = get_setting("mail_from") or get_setting("smtp_user")
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content("Ce message est en format HTML.")
    msg.add_alternative(html, subtype="html")
    for name, data, mime in attachments:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    host, port = get_setting("smtp_host"), int(get_setting("smtp_port") or 587)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if get_setting("smtp_tls") == "1":
            server.starttls()
    try:
        if get_setting("smtp_user"):
            server.login(get_setting("smtp_user"), get_setting("smtp_password"))
        server.send_message(msg)
    finally:
        server.quit()


def _graph_headers():
    if _graph_token["value"] and _graph_token["exp"] > time.time() + 60:
        return {"Authorization": f"Bearer {_graph_token['value']}"}
    tenant = get_setting("graph_tenant_id")
    r = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": get_setting("graph_client_id"),
            "client_secret": get_setting("graph_client_secret"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }, timeout=30)
    r.raise_for_status()
    data = r.json()
    _graph_token.update(value=data["access_token"], exp=time.time() + int(data.get("expires_in", 3600)))
    return {"Authorization": f"Bearer {_graph_token['value']}"}


def _graph_mailbox():
    return get_setting("graph_mailbox") or get_setting("mail_from")


def _send_graph(to, cc, subject, html, attachments):
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
    }
    if attachments:
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name, "contentType": mime,
            "contentBytes": base64.b64encode(data).decode(),
        } for name, data, mime in attachments]
    r = requests.post(f"https://graph.microsoft.com/v1.0/users/{_graph_mailbox()}/sendMail",
                      headers=_graph_headers(), json={"message": message, "saveToSentItems": True}, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Graph sendMail {r.status_code}: {r.text[:300]}")


# ----------------------------------------------------------------- réception

class Incoming:
    def __init__(self, message_id, sender_name, sender_email, subject, body, attachments):
        self.message_id = message_id
        self.sender_name = sender_name
        self.sender_email = (sender_email or "").lower()
        self.subject = subject or "(sans objet)"
        self.body = body or ""
        self.attachments = attachments  # [(nom, bytes, mime)]


def fetch_unread():
    """Retourne la liste des courriels non lus de la boîte de signalement et les marque lus."""
    b = backend()
    if b == "smtp_imap":
        return _fetch_imap()
    if b == "graph":
        return _fetch_graph()
    return []


def _decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return value or ""


def _fetch_imap():
    host, port = get_setting("imap_host"), int(get_setting("imap_port") or 993)
    conn = imaplib.IMAP4_SSL(host, port)
    out = []
    try:
        conn.login(get_setting("imap_user"), get_setting("imap_password"))
        conn.select(get_setting("imap_folder") or "INBOX")
        typ, data = conn.search(None, "UNSEEN")
        for num in (data[0].split() if data and data[0] else [])[:50]:
            typ, msg_data = conn.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            name, addr = parseaddr(_decode(msg.get("From")))
            body, atts = "", []
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "")
                if "attachment" in disp or (part.get_filename() and ctype.startswith("image/")):
                    payload = part.get_payload(decode=True)
                    if payload:
                        atts.append((_decode(part.get_filename()) or "piece_jointe", payload, ctype))
                elif ctype == "text/plain" and not body:
                    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                elif ctype == "text/html" and not body:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                    body = _strip_html(html)
            mid = msg.get("Message-ID") or f"imap-{num.decode()}-{msg.get('Date')}"
            out.append(Incoming(mid.strip(), name, addr, _decode(msg.get("Subject")), body, atts))
            conn.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


def _fetch_graph():
    base = f"https://graph.microsoft.com/v1.0/users/{_graph_mailbox()}"
    h = _graph_headers()
    r = requests.get(f"{base}/mailFolders/inbox/messages",
                     params={"$filter": "isRead eq false", "$top": "50",
                             "$select": "id,internetMessageId,subject,from,body,hasAttachments"},
                     headers={**h, "Prefer": 'outlook.body-content-type="text"'}, timeout=60)
    r.raise_for_status()
    out = []
    for m in r.json().get("value", []):
        atts = []
        if m.get("hasAttachments"):
            ra = requests.get(f"{base}/messages/{m['id']}/attachments", headers=h, timeout=60)
            if ra.ok:
                for a in ra.json().get("value", []):
                    if a.get("contentBytes"):
                        atts.append((a.get("name", "piece_jointe"), base64.b64decode(a["contentBytes"]),
                                     a.get("contentType")))
        sender = (m.get("from") or {}).get("emailAddress") or {}
        out.append(Incoming(m.get("internetMessageId") or m["id"], sender.get("name"), sender.get("address"),
                            m.get("subject"), (m.get("body") or {}).get("content", ""), atts))
        requests.patch(f"{base}/messages/{m['id']}", headers=h, json={"isRead": True}, timeout=30)
    return out


def _strip_html(html):
    import re
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    import html as h
    return re.sub(r"[ \t]+", " ", h.unescape(text)).strip()


def test_connection():
    """Vérifie la configuration; retourne (ok, message)."""
    b = backend()
    try:
        if b == "smtp_imap":
            host, port = get_setting("smtp_host"), int(get_setting("smtp_port") or 587)
            s = smtplib.SMTP_SSL(host, port, timeout=15) if port == 465 else smtplib.SMTP(host, port, timeout=15)
            if port != 465 and get_setting("smtp_tls") == "1":
                s.starttls()
            if get_setting("smtp_user"):
                s.login(get_setting("smtp_user"), get_setting("smtp_password"))
            s.quit()
            msg = "SMTP OK."
            if get_setting("imap_host"):
                c = imaplib.IMAP4_SSL(get_setting("imap_host"), int(get_setting("imap_port") or 993))
                c.login(get_setting("imap_user"), get_setting("imap_password"))
                c.logout()
                msg += " IMAP OK."
            return True, msg
        if b == "graph":
            _graph_token["value"] = None
            _graph_headers()
            return True, "Jeton Microsoft 365 obtenu."
        return False, "Aucun système de courriel sélectionné."
    except Exception as e:  # noqa: BLE001
        return False, str(e)
