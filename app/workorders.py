import os
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort, send_from_directory,
                   current_app)
from flask_login import login_required, current_user
from sqlalchemy import or_

from . import db
from .helpers import audit, editor_required, f_str, f_int, f_float, f_date, f_datetime, fmt_date, fmt_datetime
from .models import (WorkOrder, Vehicle, Tool, User, Garage, Driver, Appointment, Attachment, WO_STATUSES,
                     WO_PRIORITIES, WO_SOURCES, WO_CLOSED, WO_STATUS_LABELS)
from .services import create_workorder, add_event, save_attachment, notify_assignment, notify_appointment

bp = Blueprint("workorders", __name__)


def _sorted_vehicles():
    vs = Vehicle.query.filter(Vehicle.status.in_(["Actif", "Inactif"])).all()
    return sorted(vs, key=lambda v: (int(v.unit_number) if v.unit_number.isdigit() else 10**9, v.unit_number))


def _managers():
    return User.query.filter(User.active.is_(True), User.role.in_(["admin", "gestionnaire"])).order_by(User.name).all()


@bp.route("/bons")
@login_required
def index():
    kind = request.args.get("type", "")
    status = request.args.get("statut", "ouverts")
    assigned = request.args.get("responsable", "")
    q = request.args.get("q", "").strip()
    query = WorkOrder.query.outerjoin(Vehicle).outerjoin(Tool)
    if kind:
        query = query.filter(WorkOrder.kind == kind)
    if status == "ouverts":
        query = query.filter(WorkOrder.status.notin_(WO_CLOSED))
    elif status == "retard":
        query = query.filter(WorkOrder.status.notin_(WO_CLOSED), WorkOrder.due_date < datetime.now().date())
    elif status in WO_STATUS_LABELS:
        query = query.filter(WorkOrder.status == status)
    if assigned == "moi":
        query = query.filter(WorkOrder.assigned_to_id == current_user.id)
    elif assigned.isdigit():
        query = query.filter(WorkOrder.assigned_to_id == int(assigned))
    if q:
        like = f"%{q}%"
        conds = [WorkOrder.title.ilike(like), WorkOrder.description.ilike(like), Vehicle.unit_number == q,
                 Vehicle.plate.ilike(like), Tool.name.ilike(like), Tool.asset_tag.ilike(like)]
        num = q.upper().replace("BT-", "").split("-")[-1]
        if num.isdigit():
            conds.append(WorkOrder.id == int(num))
        query = query.filter(or_(*conds))
    wos = query.order_by(WorkOrder.due_date.is_(None), WorkOrder.due_date, WorkOrder.id.desc()).limit(500).all()
    return render_template("workorders/index.html", wos=wos, kind=kind, status=status, assigned=assigned, q=q,
                           statuses=WO_STATUSES, managers=_managers())


@bp.route("/bons/nouveau", methods=["GET", "POST"])
@login_required
@editor_required
def new():
    kind = request.values.get("type", "vehicule")
    if request.method == "POST":
        title = f_str("title")
        vid, tid, aid = request.form.get("vehicle_id"), request.form.get("tool_id"), request.form.get("assigned_to_id")
        vehicle = db.session.get(Vehicle, int(vid)) if kind == "vehicule" and vid and vid.isdigit() else None
        tool = db.session.get(Tool, int(tid)) if kind == "outil" and tid and tid.isdigit() else None
        assigned = db.session.get(User, int(aid)) if aid and aid.isdigit() else None
        if not title:
            flash("Décrivez le problème (titre).", "danger")
        else:
            wo = create_workorder(
                kind=kind, title=title, description=f_str("description"), vehicle=vehicle, tool=tool,
                source=f_str("source") or "telephone", priority=f_str("priority") or "normale",
                reporter_name=f_str("reporter_name"), reporter_email=f_str("reporter_email"),
                reporter_phone=f_str("reporter_phone"), assigned_to=assigned, due_date=f_date("due_date"),
                created_by=current_user)
            for fs in request.files.getlist("files"):
                if fs and fs.filename:
                    save_attachment(wo, fs.filename, fs.read(), fs.mimetype)
            db.session.commit()
            flash(f"Bon de travail {wo.number} créé" + (f" et envoyé à {wo.assigned_to.name}." if wo.assigned_to else "."),
                  "success")
            return redirect(url_for("workorders.show", wid=wo.id))
    pre_vehicle = request.args.get("vehicule", type=int)
    pre_tool = request.args.get("outil", type=int)
    pre_driver = None
    if pre_vehicle:
        v = db.session.get(Vehicle, pre_vehicle)
        pre_driver = v.driver if v else None
    tools = Tool.query.filter(Tool.status != "retire").order_by(Tool.name).all()
    from .services import default_manager
    return render_template("workorders/new.html", kind=kind, vehicles=_sorted_vehicles(), tools=tools,
                           managers=_managers(), priorities=WO_PRIORITIES, sources=WO_SOURCES,
                           pre_vehicle=pre_vehicle, pre_tool=pre_tool, pre_driver=pre_driver,
                           default_mgr=default_manager(kind))


@bp.route("/bons/<int:wid>")
@login_required
def show(wid):
    wo = db.session.get(WorkOrder, wid) or abort(404)
    tools = Tool.query.filter(Tool.status != "retire").order_by(Tool.name).all()
    return render_template("workorders/show.html", wo=wo, statuses=WO_STATUSES, priorities=WO_PRIORITIES,
                           managers=_managers(), garages=Garage.query.order_by(Garage.name).all(),
                           drivers=Driver.query.filter_by(active=True).order_by(Driver.name).all(),
                           vehicles=_sorted_vehicles(), tools=tools)


@bp.route("/bons/<int:wid>/maj", methods=["POST"])
@login_required
@editor_required
def update(wid):
    """Mise à jour : statut, responsable, date prévue, priorité, garage, coûts + note de suivi."""
    wo = db.session.get(WorkOrder, wid) or abort(404)
    changes = []
    new_status = f_str("status") or wo.status
    if new_status != wo.status:
        changes.append(f"Statut : {wo.status_label} → {WO_STATUS_LABELS.get(new_status, new_status)}")
        wo.status = new_status
        if new_status in WO_CLOSED:
            wo.closed_at = datetime.now()
            if wo.tool and wo.tool.status == "en_reparation" and new_status == "termine":
                wo.tool.status = "en_service"
        else:
            wo.closed_at = None
    due = f_date("due_date")
    if due != wo.due_date:
        changes.append(f"Date prévue : {fmt_date(wo.due_date)} → {fmt_date(due)}")
        wo.due_date = due
        wo.last_overdue_notice = None
        wo.overdue_notice_count = 0
    pr = f_str("priority") or wo.priority
    if pr != wo.priority:
        changes.append(f"Priorité : {wo.priority} → {pr}")
        wo.priority = pr
    aid = request.form.get("assigned_to_id")
    new_assigned = int(aid) if aid and aid.isdigit() else None
    reassigned = None
    if new_assigned != wo.assigned_to_id:
        u = db.session.get(User, new_assigned) if new_assigned else None
        changes.append(f"Responsable : {wo.assigned_to.name if wo.assigned_to else '—'} → {u.name if u else '—'}")
        wo.assigned_to = u
        reassigned = u
        if u and wo.status in ("nouveau", "ouvert"):
            wo.status = "assigne"
    gid = request.form.get("garage_id")
    new_g = int(gid) if gid and gid.isdigit() else None
    if new_g != wo.garage_id:
        g = db.session.get(Garage, new_g) if new_g else None
        changes.append(f"Garage/atelier : {g.name if g else '—'}")
        wo.garage = g
    # Rattacher un véhicule/outil (bons « à trier » issus des courriels)
    vid = request.form.get("vehicle_id")
    if vid is not None:
        nv = int(vid) if vid.isdigit() else None
        if nv != wo.vehicle_id:
            wo.vehicle = db.session.get(Vehicle, nv) if nv else None
            changes.append(f"Véhicule : {wo.vehicle.label if wo.vehicle else '—'}")
            if wo.vehicle and wo.status == "nouveau":
                wo.status = "assigne" if wo.assigned_to else "ouvert"
    tid = request.form.get("tool_id")
    if tid is not None:
        nt = int(tid) if tid.isdigit() else None
        if nt != wo.tool_id:
            wo.tool = db.session.get(Tool, nt) if nt else None
            changes.append(f"Outil : {wo.tool.name if wo.tool else '—'}")
            if wo.tool:
                wo.kind = "outil"
                if wo.tool.status == "en_service" and not wo.is_closed:
                    wo.tool.status = "en_reparation"
                if wo.status == "nouveau":
                    wo.status = "assigne" if wo.assigned_to else "ouvert"
    for field, label, fn in (("cost", "Coût", f_float), ("invoice_number", "No facture", f_str),
                             ("resolution", "Résolution", f_str)):
        val = fn(field)
        if val != getattr(wo, field) and not (val is None and getattr(wo, field) in (None, "")):
            setattr(wo, field, val)
            changes.append(f"{label} mis à jour")
    km = f_int("km_current")
    if km and wo.vehicle and km != wo.vehicle.km_current:
        wo.vehicle.km_current = km
        changes.append(f"Odomètre : {km} km")
    note = f_str("note")
    if not changes and not note:
        flash("Aucun changement.", "info")
        return redirect(url_for("workorders.show", wid=wo.id))
    msg = "\n".join(changes + ([f"Note : {note}"] if note else []))
    add_event(wo, msg, "statut" if changes else "note", current_user)
    for fs in request.files.getlist("files"):
        if fs and fs.filename:
            save_attachment(wo, fs.filename, fs.read(), fs.mimetype)
    audit("mise_a_jour", "bon_travail", wo.id, f"{wo.number}: {msg}")
    db.session.commit()
    if reassigned:
        notify_assignment(wo, reassigned)
    flash("Bon de travail mis à jour.", "success")
    return redirect(url_for("workorders.show", wid=wo.id))


@bp.route("/bons/<int:wid>/rdv", methods=["POST"])
@login_required
@editor_required
def add_appointment(wid):
    wo = db.session.get(WorkOrder, wid) or abort(404)
    start = f_datetime("start_at")
    if not start:
        flash("Date et heure du rendez-vous requises.", "danger")
        return redirect(url_for("workorders.show", wid=wo.id))
    gid = request.form.get("garage_id")
    garage = db.session.get(Garage, int(gid)) if gid and gid.isdigit() else wo.garage
    att_type = request.form.get("attendee_type", "chauffeur")
    did, uid = request.form.get("attendee_driver_id"), request.form.get("attendee_user_id")
    appt = Appointment(workorder=wo, start_at=start, garage=garage, attendee_type=att_type,
                       attendee_driver_id=int(did) if att_type == "chauffeur" and did and did.isdigit() else None,
                       attendee_user_id=int(uid) if att_type == "utilisateur" and uid and uid.isdigit() else None,
                       notes=f_str("notes"))
    db.session.add(appt)
    if garage and not wo.garage:
        wo.garage = garage
    if wo.status in ("nouveau", "ouvert", "assigne"):
        wo.status = "rdv"
    db.session.flush()
    add_event(wo, f"Rendez-vous planifié le {fmt_datetime(start)} chez {garage.name if garage else '—'} — "
                  f"y va : {appt.attendee_name}", "rdv", current_user)
    audit("creation", "rendez_vous", appt.id, f"{wo.number} {fmt_datetime(start)} {appt.attendee_name}")
    db.session.commit()
    if request.form.get("notify"):
        if notify_appointment(appt, current_user):
            flash(f"Rendez-vous ajouté et convocation envoyée à {appt.attendee_name}.", "success")
        else:
            flash("Rendez-vous ajouté, mais le courriel n'a pas pu être envoyé (adresse manquante ou "
                  "courriel non configuré).", "warning")
    else:
        flash("Rendez-vous ajouté.", "success")
    return redirect(url_for("workorders.show", wid=wo.id))


@bp.route("/rdv/<int:aid>/<action>", methods=["POST"])
@login_required
@editor_required
def appointment_action(aid, action):
    appt = db.session.get(Appointment, aid) or abort(404)
    wo = appt.workorder
    if action == "annuler":
        appt.cancelled = True
        add_event(wo, f"Rendez-vous du {fmt_datetime(appt.start_at)} annulé.", "rdv", current_user)
        audit("annulation", "rendez_vous", appt.id, wo.number)
        db.session.commit()
        flash("Rendez-vous annulé.", "info")
    elif action == "envoyer":
        ok = notify_appointment(appt, current_user)
        flash("Convocation envoyée." if ok else "Échec de l'envoi (voir Administration > Courriels).",
              "success" if ok else "danger")
    return redirect(url_for("workorders.show", wid=wo.id))


@bp.route("/fichiers/<int:att_id>")
@login_required
def attachment(att_id):
    att = db.session.get(Attachment, att_id) or abort(404)
    return send_from_directory(current_app.config["UPLOAD_DIR"], att.stored_name, download_name=att.filename,
                               as_attachment=not (att.content_type or "").startswith("image/"))
