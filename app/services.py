"""Logique métier partagée : création de bons de travail, notifications, rappels."""
import os
import re
import uuid
from datetime import datetime, date, timedelta
from html import escape

from flask import current_app
from werkzeug.utils import secure_filename

from . import db
from .helpers import audit, base_url, fmt_date, fmt_datetime
from .mailer import send_mail, wrap_html
from .models import (WorkOrder, WorkOrderEvent, Attachment, Vehicle, Driver, User, Tool,
                     get_setting, WO_CLOSED)


def _int_setting(key, default):
    try:
        return int(get_setting(key) or default)
    except ValueError:
        return default


def default_manager(kind):
    key = "default_tools_manager_id" if kind == "outil" else "default_fleet_manager_id"
    uid = get_setting(key)
    if uid and uid.isdigit():
        u = db.session.get(User, int(uid))
        if u and u.active:
            return u
    return User.query.filter_by(role="admin", active=True).order_by(User.id).first()


def add_event(wo, message, kind="note", user=None):
    wo.last_activity_at = datetime.now()
    ev = WorkOrderEvent(workorder=wo, message=message, kind=kind, user_id=user.id if user else None)
    db.session.add(ev)
    return ev


def save_attachment(wo, filename, data, content_type=None):
    safe = secure_filename(filename) or "fichier"
    stored = f"{uuid.uuid4().hex}_{safe}"
    with open(os.path.join(current_app.config["UPLOAD_DIR"], stored), "wb") as f:
        f.write(data)
    db.session.add(Attachment(workorder=wo, filename=filename[:300], stored_name=stored, content_type=content_type))


def create_workorder(*, kind, title, description=None, vehicle=None, tool=None, source="manuel",
                     priority="normale", reporter_name=None, reporter_email=None, reporter_phone=None,
                     assigned_to=None, due_date=None, created_by=None, status=None):
    if assigned_to is None:
        assigned_to = default_manager(kind)
    if due_date is None:
        due_date = date.today() + timedelta(days=_int_setting("default_due_days", 7))
    if status is None:
        status = "assigne" if assigned_to else "ouvert"
        if vehicle is None and tool is None:
            status = "nouveau"
    wo = WorkOrder(kind=kind, title=title[:250], description=description, vehicle=vehicle, tool=tool,
                   source=source, priority=priority, reporter_name=reporter_name,
                   reporter_email=reporter_email, reporter_phone=reporter_phone,
                   assigned_to=assigned_to, due_date=due_date, created_by=created_by, status=status)
    db.session.add(wo)
    db.session.flush()
    if tool is not None and tool.status == "en_service":
        tool.status = "en_reparation"
    add_event(wo, f"Bon de travail créé (source : {source}).", "systeme", created_by)
    audit("creation", "bon_travail", wo.id, f"{wo.number} — {title}", user=created_by)
    db.session.commit()
    notify_new_workorder(wo)
    return wo


# ------------------------------------------------------------------ courriels

def wo_link(wo):
    return f"{base_url()}/bons/{wo.id}"


def _wo_summary(wo):
    rows = [
        ("Bon de travail", wo.number),
        ("Concerne", wo.subject_label),
        ("Problème", wo.title),
        ("Priorité", wo.priority.capitalize()),
        ("Statut", wo.status_label),
        ("Date prévue de résolution", fmt_date(wo.due_date)),
        ("Responsable", wo.assigned_to.name if wo.assigned_to else "Non assigné"),
    ]
    if wo.reporter_name or wo.reporter_email:
        rows.append(("Signalé par", " ".join(x for x in [wo.reporter_name, wo.reporter_email, wo.reporter_phone] if x)))
    if wo.vehicle and wo.vehicle.driver:
        rows.append(("Chauffeur attitré", wo.vehicle.driver.name))
    html = "<table style='border-collapse:collapse'>" + "".join(
        f"<tr><td style='padding:3px 12px 3px 0;color:#6b7280'>{escape(k)}</td>"
        f"<td style='padding:3px 0'><b>{escape(str(v))}</b></td></tr>" for k, v in rows) + "</table>"
    if wo.description:
        html += f"<p style='white-space:pre-wrap;background:#f3f4f6;padding:8px'>{escape(wo.description[:3000])}</p>"
    html += f"<p><a href='{wo_link(wo)}' style='background:#1e3a5f;color:#fff;padding:8px 14px;text-decoration:none'>Ouvrir le bon de travail</a></p>"
    return html


def notify_new_workorder(wo):
    if wo.assigned_to:
        send_mail(wo.assigned_to.email, f"Nouveau bon de travail {wo.number} — {wo.subject_label}",
                  wrap_html(f"<p>Bonjour {escape(wo.assigned_to.name)},</p><p>Un nouveau bon de travail vous est assigné. "
                            f"Merci de le prendre en charge et de mettre à jour la date prévue.</p>{_wo_summary(wo)}"),
                  workorder_id=wo.id)
    if wo.reporter_email and wo.source in ("courriel", "formulaire") and get_setting("inbox_ack_sender") == "1":
        send_mail(wo.reporter_email, f"Signalement reçu — {wo.number}",
                  wrap_html(f"<p>Bonjour,</p><p>Votre signalement a bien été reçu et le bon de travail "
                            f"<b>{wo.number}</b> a été ouvert ({escape(wo.subject_label)}). "
                            f"Le responsable ({escape(wo.assigned_to.name if wo.assigned_to else 'à déterminer')}) "
                            f"communiquera avec vous.</p>"), workorder_id=wo.id)


def notify_assignment(wo, user):
    send_mail(user.email, f"Bon de travail {wo.number} vous est assigné",
              wrap_html(f"<p>Bonjour {escape(user.name)},</p><p>Le bon de travail suivant vous a été assigné.</p>{_wo_summary(wo)}"),
              workorder_id=wo.id)


def build_ics(appt):
    wo = appt.workorder
    start = appt.start_at
    end = start + timedelta(hours=1)
    loc = ""
    if appt.garage:
        loc = ", ".join(x for x in [appt.garage.name, appt.garage.address] if x)

    def esc(s):
        return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Kubios//Flotte//FR", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:kubios-rdv-{appt.id}@kubios",
        f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}",
        f"DTSTART:{start:%Y%m%dT%H%M%S}",
        f"DTEND:{end:%Y%m%dT%H%M%S}",
        f"SUMMARY:{esc('Garage — ' + wo.subject_label)}",
        f"LOCATION:{esc(loc)}",
        f"DESCRIPTION:{esc(wo.number + ' — ' + wo.title + chr(10) + (appt.notes or ''))}",
        "END:VEVENT", "END:VCALENDAR", ""]).encode("utf-8")


def _appt_html(appt, intro):
    wo = appt.workorder
    g = appt.garage
    rows = [("Date et heure", fmt_datetime(appt.start_at)), ("Véhicule / outil", wo.subject_label)]
    if wo.vehicle:
        rows.append(("Plaque", wo.vehicle.plate or "—"))
    rows += [("Raison", wo.title),
             ("Garage", g.name if g else "—"),
             ("Adresse", g.address if g and g.address else "—"),
             ("Téléphone", g.phone if g and g.phone else "—"),
             ("Contact garage", g.contact if g and g.contact else "—"),
             ("Responsable", wo.assigned_to.name + (f" ({wo.assigned_to.phone})" if wo.assigned_to.phone else "")
              if wo.assigned_to else "—"),
             ("Bon de travail", wo.number)]
    html = f"<p>{intro}</p><table style='border-collapse:collapse'>" + "".join(
        f"<tr><td style='padding:3px 12px 3px 0;color:#6b7280'>{escape(k)}</td><td><b>{escape(str(v))}</b></td></tr>"
        for k, v in rows) + "</table>"
    if appt.notes:
        html += f"<p style='white-space:pre-wrap;background:#f3f4f6;padding:8px'>{escape(appt.notes)}</p>"
    if g and g.address:
        from urllib.parse import quote
        html += f"<p><a href='https://www.google.com/maps/search/?api=1&query={quote(g.address)}'>Itinéraire (Google Maps)</a></p>"
    return html


def notify_appointment(appt, user=None):
    to = appt.attendee_email
    if not to:
        return False
    wo = appt.workorder
    ok = send_mail(to, f"Rendez-vous garage {fmt_datetime(appt.start_at)} — {wo.subject_label}",
                   wrap_html(_appt_html(appt, f"Bonjour {escape(appt.attendee_name)},<br>Vous êtes désigné(e) pour "
                                              f"amener le véhicule/l'outil au rendez-vous suivant :")),
                   workorder_id=wo.id, attachments=[("rendez-vous.ics", build_ics(appt), "text/calendar")],
                   cc=wo.assigned_to.email if wo.assigned_to and wo.assigned_to.email != to else None)
    if ok:
        appt.notified_at = datetime.now()
        add_event(wo, f"Convocation envoyée à {appt.attendee_name} ({to}).", "courriel", user)
        db.session.commit()
    return ok


# ------------------------------------------------------------------ rappels

def run_reminders():
    """Exécuté périodiquement : bons en retard, bons sans mise à jour, rappels de rendez-vous."""
    now = datetime.now()
    today = date.today()
    stale_days = _int_setting("stale_days", 3)
    repeat_days = _int_setting("overdue_repeat_days", 1)
    escalate_after = _int_setting("escalate_after_notices", 3)
    escalation = get_setting("escalation_email") or None
    sent = 0

    open_wos = WorkOrder.query.filter(WorkOrder.status.notin_(WO_CLOSED)).all()
    for wo in open_wos:
        to = wo.assigned_to.email if wo.assigned_to else (escalation or None)
        if not to:
            continue
        # 1) Date prévue dépassée
        if wo.due_date and wo.due_date < today:
            if not wo.last_overdue_notice or now - wo.last_overdue_notice >= timedelta(days=repeat_days):
                late = (today - wo.due_date).days
                cc = escalation if escalation and wo.overdue_notice_count + 1 >= escalate_after else None
                send_mail(to, f"EN RETARD ({late} j) — {wo.number} {wo.subject_label}",
                          wrap_html(f"<p style='color:#b91c1c'><b>La date prévue de résolution "
                                    f"({fmt_date(wo.due_date)}) est dépassée de {late} jour(s).</b></p>"
                                    f"<p>Merci de mettre le bon à jour (nouvelle date, statut ou fermeture).</p>"
                                    f"{_wo_summary(wo)}"), workorder_id=wo.id, cc=cc)
                wo.last_overdue_notice = now
                wo.overdue_notice_count += 1
                db.session.add(WorkOrderEvent(workorder=wo, kind="courriel",
                                              message=f"Rappel de retard envoyé à {to}" + (f" (cc {cc})" if cc else "")))
                sent += 1
                continue
        # 2) Aucune mise à jour depuis X jours
        if now - wo.last_activity_at >= timedelta(days=stale_days):
            if not wo.last_stale_notice or now - wo.last_stale_notice >= timedelta(days=stale_days):
                send_mail(to, f"Sans mise à jour depuis {(now - wo.last_activity_at).days} j — {wo.number}",
                          wrap_html(f"<p>Le bon de travail suivant n'a pas été mis à jour depuis le "
                                    f"{fmt_datetime(wo.last_activity_at)}. Merci d'ajouter une note de suivi.</p>"
                                    f"{_wo_summary(wo)}"), workorder_id=wo.id)
                wo.last_stale_notice = now
                db.session.add(WorkOrderEvent(workorder=wo, kind="courriel",
                                              message=f"Rappel « sans mise à jour » envoyé à {to}"))
                sent += 1
    db.session.commit()

    # 3) Rappel la veille d'un rendez-vous
    from .models import Appointment
    tomorrow_start = datetime.combine(today + timedelta(days=1), datetime.min.time())
    tomorrow_end = tomorrow_start + timedelta(days=1)
    appts = Appointment.query.filter(Appointment.start_at >= now, Appointment.start_at < tomorrow_end,
                                     Appointment.cancelled.is_(False), Appointment.reminder_sent_at.is_(None)).all()
    for a in appts:
        if a.attendee_email and now.hour >= 7:
            send_mail(a.attendee_email, f"RAPPEL — rendez-vous garage {fmt_datetime(a.start_at)}",
                      wrap_html(_appt_html(a, f"Bonjour {escape(a.attendee_name)},<br>Rappel de votre rendez-vous :")),
                      workorder_id=a.workorder_id)
            a.reminder_sent_at = now
            sent += 1
    db.session.commit()
    return sent


def fleet_alerts(days=None):
    """Échéances à venir : immatriculation, vérification mécanique, fin de bail, garantie."""
    days = days if days is not None else _int_setting("expiry_warning_days", 45)
    limit = date.today() + timedelta(days=days)
    alerts = []
    fields = [("registration_expiry", "Immatriculation SAAQ"), ("mechanical_inspection", "Vérification mécanique"),
              ("lease_end", "Fin de bail / prêt"), ("warranty_end", "Fin de garantie")]
    for v in Vehicle.query.filter(Vehicle.status == "Actif").all():
        for field, label in fields:
            d = getattr(v, field)
            if d and d <= limit and d.year < 9999:
                # Vérif. mécanique passée = information historique, on ne garde que les échéances futures proches
                if field == "mechanical_inspection" and d < date.today() - timedelta(days=1):
                    continue
                alerts.append({"vehicle": v, "label": label, "date": d, "days": (d - date.today()).days})
    return sorted(alerts, key=lambda a: a["date"])


def send_daily_digest():
    """Résumé quotidien aux gestionnaires : bons en retard et échéances."""
    users = User.query.filter(User.active.is_(True), User.role.in_(["admin", "gestionnaire"])).all()
    alerts = fleet_alerts()
    for u in users:
        mine = WorkOrder.query.filter(WorkOrder.assigned_to_id == u.id, WorkOrder.status.notin_(WO_CLOSED)).all()
        if not mine and not (alerts and u.is_admin):
            continue
        rows = "".join(
            f"<tr><td><a href='{wo_link(w)}'>{w.number}</a></td><td>{escape(w.subject_label)}</td>"
            f"<td>{escape(w.title)}</td><td>{w.status_label}</td>"
            f"<td style='color:{'#b91c1c' if w.is_overdue else '#111'}'>{fmt_date(w.due_date)}</td></tr>"
            for w in sorted(mine, key=lambda w: w.due_date or date.max))
        html = f"<p>Bonjour {escape(u.name)}, voici votre résumé du {fmt_date(date.today())}.</p>"
        if mine:
            html += ("<h3>Vos bons de travail ouverts</h3><table cellpadding='4' style='border-collapse:collapse'>"
                     "<tr><th>BT</th><th>Concerne</th><th>Problème</th><th>Statut</th><th>Prévu</th></tr>"
                     f"{rows}</table>")
        if alerts and u.is_admin:
            html += "<h3>Échéances de la flotte</h3><ul>" + "".join(
                f"<li>#{escape(a['vehicle'].unit_number)} — {a['label']} : {fmt_date(a['date'])} "
                f"({a['days']} j)</li>" for a in alerts) + "</ul>"
        send_mail(u.email, f"Résumé quotidien — {len(mine)} bon(s) ouvert(s)", wrap_html(html))


# ------------------------------------------------------------------ courriels entrants

UNIT_RE = re.compile(r"(?:unit[ée]s?|camion|truck|v[ée]hicule|van|#|no\.?|num[ée]ro)\s*[:#]?\s*(\d{1,4})\b", re.I)


def identify_vehicle(text, sender_email=None):
    for m in UNIT_RE.finditer(text):
        v = Vehicle.query.filter_by(unit_number=m.group(1)).first()
        if v:
            return v
    # Plaque d'immatriculation
    compact = re.sub(r"[\s-]", "", text.upper())
    for v in Vehicle.query.filter(Vehicle.plate.isnot(None)).all():
        p = re.sub(r"[\s-]", "", v.plate.upper())
        if len(p) >= 5 and p in compact:
            return v
    # Véhicule attitré à l'expéditeur
    if sender_email:
        d = Driver.query.filter(db.func.lower(Driver.email) == sender_email.lower()).first()
        if d:
            active = [v for v in d.vehicles if v.status == "Actif"]
            if len(active) == 1:
                return active[0]
    return None


def identify_tool(text):
    for t in Tool.query.filter(Tool.asset_tag.isnot(None)).all():
        if t.asset_tag and re.search(rf"\b{re.escape(t.asset_tag)}\b", text, re.I):
            return t
    return None


def process_inbox():
    from .mailer import fetch_unread
    from .models import InboundEmail
    created = 0
    for msg in fetch_unread():
        if InboundEmail.query.filter_by(message_id=msg.message_id[:500]).first():
            continue
        text = f"{msg.subject}\n{msg.body}"
        is_tool = bool(re.search(r"\boutils?\b", msg.subject, re.I))
        tool = identify_tool(text) if is_tool else None
        vehicle = None if tool else identify_vehicle(text, msg.sender_email)
        driver = Driver.query.filter(db.func.lower(Driver.email) == msg.sender_email).first()
        wo = create_workorder(
            kind="outil" if (is_tool or tool) else "vehicule",
            title=msg.subject[:250], description=msg.body[:20000], vehicle=vehicle, tool=tool,
            source="courriel", reporter_name=msg.sender_name or (driver.name if driver else None),
            reporter_email=msg.sender_email, reporter_phone=driver.phone if driver else None)
        for name, data, mime in msg.attachments[:10]:
            save_attachment(wo, name, data, mime)
        db.session.add(InboundEmail(message_id=msg.message_id[:500], sender=msg.sender_email,
                                    subject=msg.subject[:500], workorder=wo))
        if wo.vehicle is None and wo.tool is None:
            add_event(wo, "Véhicule/outil non identifié automatiquement — à trier.", "systeme")
        db.session.commit()
        created += 1
    return created
