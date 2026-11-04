import json

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from . import db
from .helpers import audit, diff_fields, editor_required, f_str, f_int, f_float, f_date
from .models import Vehicle, Driver, Garage, Tool, InventoryCheck, VEHICLE_STATUSES, WO_CLOSED, WorkOrder

bp = Blueprint("vehicles", __name__)


@bp.route("/vehicules")
@login_required
def index():
    q = request.args.get("q", "").strip()
    status = request.args.get("statut", "Actif")
    dept = request.args.get("dept", "")
    query = Vehicle.query.outerjoin(Driver)
    if status:
        query = query.filter(Vehicle.status == status)
    if dept:
        query = query.filter(Vehicle.department == dept)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Vehicle.unit_number == q, Vehicle.plate.ilike(like), Vehicle.vin.ilike(like),
                                 Vehicle.make.ilike(like), Vehicle.model.ilike(like), Driver.name.ilike(like)))
    vehicles = query.all()
    vehicles.sort(key=lambda v: (int(v.unit_number) if v.unit_number.isdigit() else 10**9, v.unit_number))
    open_by_vehicle = dict(db.session.query(WorkOrder.vehicle_id, db.func.count(WorkOrder.id))
                           .filter(WorkOrder.status.notin_(WO_CLOSED)).group_by(WorkOrder.vehicle_id).all())
    depts = [d for (d,) in db.session.query(Vehicle.department).distinct().order_by(Vehicle.department) if d]
    return render_template("vehicles/index.html", vehicles=vehicles, q=q, status=status, dept=dept,
                           depts=depts, statuses=VEHICLE_STATUSES, open_by_vehicle=open_by_vehicle)


@bp.route("/vehicules/<int:vid>")
@login_required
def show(vid):
    v = db.session.get(Vehicle, vid) or abort(404)
    wos = sorted(v.workorders, key=lambda w: w.created_at, reverse=True)
    checks = InventoryCheck.query.filter_by(vehicle_id=v.id).order_by(InventoryCheck.created_at.desc()).limit(5).all()
    return render_template("vehicles/show.html", v=v, wos=wos, checks=checks)


VEHICLE_FIELDS = {
    "unit_number": f_str, "beneva_number": f_str, "status": f_str, "year": f_int, "make": f_str, "model": f_str,
    "plate": f_str, "vin": f_str, "department": f_str, "km_current": f_int, "km_annual": f_int,
    "ownership": f_str, "financing": f_str, "lease_end": f_date, "monthly_payment": f_float,
    "balance_due": f_float, "warranty_end": f_date, "registration_expiry": f_date,
    "mechanical_inspection": f_date, "mechanical_status": f_str, "weight": f_int, "gvwr": f_int, "remarks": f_str,
}


@bp.route("/vehicules/nouveau", methods=["GET", "POST"])
@bp.route("/vehicules/<int:vid>/modifier", methods=["GET", "POST"])
@login_required
@editor_required
def edit(vid=None):
    v = db.session.get(Vehicle, vid) if vid else Vehicle(status="Actif")
    if vid and v is None:
        abort(404)
    if request.method == "POST":
        values = {k: fn(k) for k, fn in VEHICLE_FIELDS.items()}
        drv = request.form.get("driver_id")
        values["driver_id"] = int(drv) if drv and drv.isdigit() else None
        if not values["unit_number"]:
            flash("Le numéro d'unité est requis.", "danger")
        elif Vehicle.query.filter(Vehicle.unit_number == values["unit_number"], Vehicle.id != (v.id or 0)).first():
            flash("Ce numéro d'unité existe déjà.", "danger")
        else:
            is_new = v.id is None
            if is_new:
                db.session.add(v)
            changes = diff_fields(v, values)
            db.session.flush()
            audit("creation" if is_new else "modification", "vehicule", v.id,
                  f"#{v.unit_number} " + (changes or "aucun changement"))
            db.session.commit()
            flash("Véhicule enregistré.", "success")
            return redirect(url_for("vehicles.show", vid=v.id))
    drivers = Driver.query.filter_by(active=True).order_by(Driver.name).all()
    depts = [d for (d,) in db.session.query(Vehicle.department).distinct().order_by(Vehicle.department) if d]
    return render_template("vehicles/edit.html", v=v, drivers=drivers, statuses=VEHICLE_STATUSES, depts=depts)


@bp.route("/vehicules/<int:vid>/inventaire", methods=["GET", "POST"])
@login_required
@editor_required
def inventory_check(vid):
    """Vérification des outils présents dans le camion."""
    v = db.session.get(Vehicle, vid) or abort(404)
    tools = [t for t in v.tools if t.status != "retire"]
    if request.method == "POST":
        result, missing, broken = {}, [], []
        for t in tools:
            state = request.form.get(f"t{t.id}", "present")
            result[str(t.id)] = state
            if state == "manquant":
                missing.append(t)
            elif state == "brise":
                broken.append(t)
        chk = InventoryCheck(vehicle=v, user_id=current_user.id, result_json=json.dumps(result),
                             missing_count=len(missing), broken_count=len(broken), note=f_str("note"))
        db.session.add(chk)
        audit("verification_inventaire", "vehicule", v.id,
              f"#{v.unit_number}: {len(missing)} manquant(s), {len(broken)} brisé(s)")
        db.session.commit()
        # Ouvre automatiquement un bon de travail par outil brisé
        if request.form.get("open_wo") and broken:
            from .services import create_workorder
            for t in broken:
                create_workorder(kind="outil", title=f"Outil brisé constaté à l'inventaire : {t.name}",
                                 tool=t, source="inspection", created_by=current_user)
        flash(f"Inventaire enregistré : {len(missing)} manquant(s), {len(broken)} brisé(s).",
              "warning" if missing or broken else "success")
        return redirect(url_for("vehicles.show", vid=v.id))
    return render_template("vehicles/inventory_check.html", v=v, tools=tools)


# ------------------------------------------------------------------ chauffeurs

@bp.route("/chauffeurs")
@login_required
def drivers():
    show_all = request.args.get("tous") == "1"
    q = Driver.query if show_all else Driver.query.filter_by(active=True)
    return render_template("vehicles/drivers.html", drivers=q.order_by(Driver.name).all(), show_all=show_all)


@bp.route("/chauffeurs/nouveau", methods=["GET", "POST"])
@bp.route("/chauffeurs/<int:did>", methods=["GET", "POST"])
@login_required
@editor_required
def driver_edit(did=None):
    d = db.session.get(Driver, did) if did else Driver(active=True)
    if did and d is None:
        abort(404)
    if request.method == "POST":
        values = {k: f_str(k) for k in ("name", "email", "phone", "department", "notes")}
        values["active"] = bool(request.form.get("active"))
        if values["email"]:
            values["email"] = values["email"].lower()
        if not values["name"]:
            flash("Nom requis.", "danger")
        else:
            is_new = d.id is None
            if is_new:
                db.session.add(d)
            changes = diff_fields(d, values)
            db.session.flush()
            audit("creation" if is_new else "modification", "chauffeur", d.id, f"{d.name} {changes}")
            db.session.commit()
            flash("Chauffeur enregistré.", "success")
            return redirect(url_for("vehicles.drivers"))
    return render_template("vehicles/driver_edit.html", d=d)


# ------------------------------------------------------------------ garages

@bp.route("/garages")
@login_required
def garages():
    show_inactive = request.args.get("inactifs") == "1"
    q = Garage.query if show_inactive else Garage.query.filter_by(active=True)
    return render_template("vehicles/garages.html", garages=q.order_by(Garage.name).all(),
                           show_inactive=show_inactive, inactive_count=Garage.query.filter_by(active=False).count())


@bp.route("/garages/<int:gid>/actif", methods=["POST"])
@login_required
@editor_required
def garage_toggle(gid):
    """Désactiver : gestionnaire ou administrateur. Réactiver : administrateur seulement."""
    g = db.session.get(Garage, gid) or abort(404)
    if g.active:
        g.active = False
        audit("desactivation", "garage", g.id, g.name)
        flash(f"« {g.name} » est désactivé. Il n'est plus proposé dans les bons de travail. "
              "Seul un administrateur peut le réactiver.", "success")
    else:
        if not current_user.is_admin:
            flash("Seul un administrateur peut réactiver un garage ou un atelier.", "danger")
            return redirect(url_for("vehicles.garages", inactifs=1))
        g.active = True
        audit("reactivation", "garage", g.id, g.name)
        flash(f"« {g.name} » est réactivé.", "success")
    db.session.commit()
    return redirect(request.referrer or url_for("vehicles.garages"))


@bp.route("/garages/nouveau", methods=["GET", "POST"])
@bp.route("/garages/<int:gid>", methods=["GET", "POST"])
@login_required
@editor_required
def garage_edit(gid=None):
    g = db.session.get(Garage, gid) if gid else Garage(kind="vehicule", active=True)
    if gid and g is None:
        abort(404)
    if g.id and not g.active and not current_user.is_admin:
        flash("Ce garage est désactivé. Seul un administrateur peut le modifier ou le réactiver.", "warning")
        return redirect(url_for("vehicles.garages", inactifs=1))
    if request.method == "POST":
        values = {k: f_str(k) for k in ("name", "kind", "contact", "phone", "email", "address", "notes")}
        if not values["name"]:
            flash("Nom requis.", "danger")
        else:
            is_new = g.id is None
            if is_new:
                db.session.add(g)
            changes = diff_fields(g, values)
            db.session.flush()
            audit("creation" if is_new else "modification", "garage", g.id, f"{g.name} {changes}")
            db.session.commit()
            flash("Garage / atelier enregistré.", "success")
            return redirect(url_for("vehicles.garages"))
    return render_template("vehicles/garage_edit.html", g=g)
