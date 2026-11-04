from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from . import db
from .helpers import audit, diff_fields, editor_required, f_str, f_int, f_float, f_date
from .models import Tool, ToolCategory, ToolMove, Vehicle, TOOL_STATUSES, WO_CLOSED

bp = Blueprint("tools", __name__)


@bp.route("/outils")
@login_required
def index():
    q = request.args.get("q", "").strip()
    status = request.args.get("statut", "")
    cat = request.args.get("cat", "")
    loc = request.args.get("lieu", "")
    query = Tool.query.outerjoin(Vehicle)
    if status:
        query = query.filter(Tool.status == status)
    else:
        query = query.filter(Tool.status != "retire")
    if cat.isdigit():
        query = query.filter(Tool.category_id == int(cat))
    if loc == "entrepot":
        query = query.filter(Tool.vehicle_id.is_(None))
    elif loc.isdigit():
        query = query.filter(Tool.vehicle_id == int(loc))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Tool.name.ilike(like), Tool.asset_tag.ilike(like), Tool.serial.ilike(like),
                                 Tool.brand.ilike(like), Tool.model.ilike(like), Vehicle.unit_number == q))
    tools = query.order_by(Tool.name).all()
    return render_template("tools/index.html", tools=tools, q=q, status=status, cat=cat, loc=loc,
                           statuses=TOOL_STATUSES, categories=ToolCategory.query.order_by(ToolCategory.name).all(),
                           vehicles=_vehicle_choices())


def _vehicle_choices():
    vs = Vehicle.query.filter(Vehicle.status == "Actif").all()
    return sorted(vs, key=lambda v: (int(v.unit_number) if v.unit_number.isdigit() else 10**9, v.unit_number))


@bp.route("/outils/<int:tid>")
@login_required
def show(tid):
    t = db.session.get(Tool, tid) or abort(404)
    wos = sorted(t.workorders, key=lambda w: w.created_at, reverse=True)
    return render_template("tools/show.html", t=t, wos=wos, vehicles=_vehicle_choices())


TOOL_FIELDS = {"asset_tag": f_str, "name": f_str, "brand": f_str, "model": f_str, "serial": f_str,
               "status": f_str, "location_note": f_str, "purchase_date": f_date, "value": f_float,
               "quantity": f_int, "notes": f_str}


@bp.route("/outils/nouveau", methods=["GET", "POST"])
@bp.route("/outils/<int:tid>/modifier", methods=["GET", "POST"])
@login_required
@editor_required
def edit(tid=None):
    t = db.session.get(Tool, tid) if tid else Tool(status="en_service", quantity=1)
    if tid and t is None:
        abort(404)
    if request.method == "GET" and not tid and request.args.get("vehicule", "").isdigit():
        t.vehicle_id = int(request.args["vehicule"])
    if request.method == "POST":
        values = {k: fn(k) for k, fn in TOOL_FIELDS.items()}
        values["quantity"] = values["quantity"] or 1
        values["status"] = values["status"] or "en_service"
        cat = request.form.get("category_id")
        values["category_id"] = int(cat) if cat and cat.isdigit() else None
        vid = request.form.get("vehicle_id")
        new_vehicle_id = int(vid) if vid and vid.isdigit() else None
        if not values["name"]:
            flash("Le nom de l'outil est requis.", "danger")
        elif values["asset_tag"] and Tool.query.filter(Tool.asset_tag == values["asset_tag"],
                                                       Tool.id != (t.id or 0)).first():
            flash("Ce numéro d'inventaire existe déjà.", "danger")
        else:
            is_new = t.id is None
            old_loc = t.location_label if not is_new else None
            if is_new:
                db.session.add(t)
            changes = diff_fields(t, values)
            if t.vehicle_id != new_vehicle_id:
                t.vehicle_id = new_vehicle_id
                db.session.flush()
                db.session.refresh(t)
                db.session.add(ToolMove(tool=t, from_label=old_loc or "Nouveau", to_label=t.location_label,
                                        user_id=current_user.id))
                changes += f"; emplacement → {t.location_label}"
            db.session.flush()
            audit("creation" if is_new else "modification", "outil", t.id, f"{t.name} {changes}")
            db.session.commit()
            flash("Outil enregistré.", "success")
            return redirect(url_for("tools.show", tid=t.id))
    return render_template("tools/edit.html", t=t, statuses=TOOL_STATUSES,
                           categories=ToolCategory.query.order_by(ToolCategory.name).all(),
                           vehicles=_vehicle_choices())


@bp.route("/outils/<int:tid>/deplacer", methods=["POST"])
@login_required
@editor_required
def move(tid):
    t = db.session.get(Tool, tid) or abort(404)
    old = t.location_label
    vid = request.form.get("vehicle_id")
    t.vehicle_id = int(vid) if vid and vid.isdigit() else None
    t.location_note = f_str("location_note") if not t.vehicle_id else None
    db.session.flush()
    db.session.refresh(t)
    db.session.add(ToolMove(tool=t, from_label=old, to_label=t.location_label, user_id=current_user.id,
                            note=f_str("note")))
    audit("deplacement", "outil", t.id, f"{t.name}: {old} → {t.location_label}")
    db.session.commit()
    flash(f"Outil déplacé vers {t.location_label}.", "success")
    return redirect(url_for("tools.show", tid=t.id))


@bp.route("/outils/categories", methods=["GET", "POST"])
@login_required
@editor_required
def categories():
    if request.method == "POST":
        name = f_str("name")
        if name and not ToolCategory.query.filter_by(name=name).first():
            db.session.add(ToolCategory(name=name))
            audit("creation", "categorie_outil", None, name)
            db.session.commit()
            flash("Catégorie ajoutée.", "success")
        return redirect(url_for("tools.categories"))
    return render_template("tools/categories.html", categories=ToolCategory.query.order_by(ToolCategory.name).all())
