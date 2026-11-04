from datetime import datetime, timedelta

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from . import db
from .models import WorkOrder, Vehicle, Tool, Appointment, WO_CLOSED
from .services import fleet_alerts

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    open_q = WorkOrder.query.filter(WorkOrder.status.notin_(WO_CLOSED))
    open_wos = open_q.all()
    overdue = [w for w in open_wos if w.is_overdue]
    to_sort = [w for w in open_wos if w.status == "nouveau"]
    mine = [w for w in open_wos if w.assigned_to_id == current_user.id]
    now = datetime.now()
    appts = (Appointment.query.filter(Appointment.start_at >= now - timedelta(hours=2),
                                      Appointment.start_at <= now + timedelta(days=14),
                                      Appointment.cancelled.is_(False))
             .order_by(Appointment.start_at).all())
    stats = {
        "vehicles_active": Vehicle.query.filter_by(status="Actif").count(),
        "vehicles_in_repair": len({w.vehicle_id for w in open_wos if w.vehicle_id}),
        "open": len(open_wos),
        "overdue": len(overdue),
        "tools": db.session.query(db.func.count(Tool.id)).scalar(),
        "tools_repair": Tool.query.filter_by(status="en_reparation").count(),
    }
    return render_template("dashboard.html", stats=stats, overdue=overdue, to_sort=to_sort, mine=mine,
                           appts=appts, alerts=fleet_alerts())


@bp.route("/planification")
@login_required
def planning():
    """Vue de dispatch : rendez-vous à venir et bons ouverts par statut."""
    days = int(request.args.get("jours", 30))
    now = datetime.now()
    appts = (Appointment.query.filter(Appointment.start_at >= now.replace(hour=0, minute=0),
                                      Appointment.start_at <= now + timedelta(days=days))
             .order_by(Appointment.start_at).all())
    open_wos = (WorkOrder.query.filter(WorkOrder.status.notin_(WO_CLOSED))
                .order_by(WorkOrder.due_date.is_(None), WorkOrder.due_date).all())
    columns = [("nouveau", "À trier"), ("ouvert", "Ouvert"), ("assigne", "Assigné"), ("rdv", "RDV planifié"),
               ("en_reparation", "En réparation"), ("attente_pieces", "Attente pièces")]
    board = {k: [w for w in open_wos if w.status == k] for k, _ in columns}
    return render_template("planning.html", appts=appts, board=board, columns=columns, days=days)
