"""Formulaire public facultatif de signalement de bris (désactivé par défaut)."""
from flask import Blueprint, render_template, request, abort, flash, redirect, url_for

from . import db
from .helpers import f_str
from .models import Vehicle, get_setting
from .services import create_workorder, save_attachment

bp = Blueprint("public", __name__)


@bp.route("/signaler", methods=["GET", "POST"])
def report():
    if get_setting("public_form_enabled") != "1":
        abort(404)
    if request.method == "POST":
        if request.form.get("website"):  # piège à robots
            abort(400)
        unit = f_str("unit")
        desc = f_str("description")
        name = f_str("name")
        if not unit or not desc or not name:
            flash("Numéro d'unité, nom et description sont requis.", "danger")
        else:
            v = Vehicle.query.filter_by(unit_number=unit.lstrip("#")).first()
            wo = create_workorder(kind="vehicule", title=(desc.splitlines()[0])[:120], description=desc,
                                  vehicle=v, source="formulaire", reporter_name=name,
                                  reporter_email=f_str("email"), reporter_phone=f_str("phone"))
            for fs in request.files.getlist("photos")[:5]:
                if fs and fs.filename and (fs.mimetype or "").startswith("image/"):
                    save_attachment(wo, fs.filename, fs.read(), fs.mimetype)
            db.session.commit()
            return render_template("public/thanks.html", number=wo.number)
    return render_template("public/report.html")
