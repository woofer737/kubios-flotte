import functools
from datetime import date, datetime

from flask import abort, request, flash, redirect, url_for
from flask_login import current_user

from . import db
from .models import AuditLog, get_setting, WO_STATUS_LABELS, WO_PRIORITIES, WO_SOURCES, ROLES


def audit(action, entity=None, entity_id=None, details=None, user=None):
    """Enregistre une action dans le journal d'audit (commit fait par l'appelant)."""
    if user is False:  # action humaine, mais l'id utilisateur n'est plus fiable (ex. après restauration)
        label = current_user.email if current_user and current_user.is_authenticated else "système"
        u = None
    else:
        u = user if user is not None else (current_user if current_user and current_user.is_authenticated else None)
        label = u.email if u else "système"
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    except RuntimeError:
        ip = None  # hors requête (tâche planifiée)
    db.session.add(AuditLog(
        user_id=u.id if u else None,
        user_label=label,
        action=action, entity=entity, entity_id=entity_id,
        details=details, ip=ip,
    ))


def diff_fields(obj, new_values):
    """Applique new_values à obj et retourne une description textuelle des changements."""
    changes = []
    for key, new in new_values.items():
        old = getattr(obj, key)
        if (old or None) != (new or None) and str(old) != str(new):
            changes.append(f"{key}: «{_fmt(old)}» → «{_fmt(new)}»")
            setattr(obj, key, new)
    return "; ".join(changes)


def _fmt(v):
    if v is None or v == "":
        return "∅"
    return str(v)


def editor_required(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        if not current_user.can_edit:
            flash("Vous n'avez pas les droits de modification.", "danger")
            return redirect(request.referrer or url_for("dashboard.index"))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return f(*a, **kw)
    return wrapper


# ---------- Conversion des champs de formulaire ----------

def f_str(name):
    v = (request.form.get(name) or "").strip()
    return v or None


def f_int(name):
    v = (request.form.get(name) or "").strip().replace(" ", "")
    try:
        return int(float(v.replace(",", "."))) if v else None
    except ValueError:
        return None


def f_float(name):
    v = (request.form.get(name) or "").strip().replace(" ", "").replace("$", "")
    try:
        return float(v.replace(",", ".")) if v else None
    except ValueError:
        return None


def f_date(name):
    v = (request.form.get(name) or "").strip()
    try:
        return datetime.strptime(v, "%Y-%m-%d").date() if v else None
    except ValueError:
        return None


def f_datetime(name):
    v = (request.form.get(name) or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def base_url():
    return (get_setting("base_url") or "").rstrip("/")


# ---------- Filtres de gabarits ----------

MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def fmt_date(d):
    if not d:
        return "—"
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def fmt_datetime(d):
    if not d:
        return "—"
    return f"{fmt_date(d)} {d:%H:%M}"


def fmt_money(v):
    if v is None:
        return "—"
    return f"{v:,.2f} $".replace(",", " ").replace(".", ",")


def days_until(d):
    if not d:
        return None
    return (d - date.today()).days


def register_template_helpers(app):
    app.jinja_env.filters.update(
        date=fmt_date, datetime=fmt_datetime, money=fmt_money, days_until=days_until,
    )

    @app.context_processor
    def _ctx():
        return dict(
            WO_STATUS_LABELS=WO_STATUS_LABELS,
            WO_PRIORITY_LABELS=dict(WO_PRIORITIES),
            WO_SOURCE_LABELS=dict(WO_SOURCES),
            ROLES=ROLES,
            today=date.today(),
            now=datetime.now(),
            app_version=app.config["APP_VERSION"],
            company_name=get_setting("company_name"),
        )
