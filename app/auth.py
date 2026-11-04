from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user

from . import db
from .helpers import audit
from .models import User

bp = Blueprint("auth", __name__)

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15


def password_problem(pw):
    if len(pw) < 10:
        return "Le mot de passe doit contenir au moins 10 caractères."
    if not any(c.isdigit() for c in pw) or not any(c.isalpha() for c in pw):
        return "Le mot de passe doit contenir des lettres et des chiffres."
    return None


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Assistant du premier démarrage : création du compte administrateur."""
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        pw = request.form.get("password") or ""
        problem = password_problem(pw)
        if not email or not name:
            flash("Nom et courriel requis.", "danger")
        elif problem:
            flash(problem, "danger")
        elif pw != request.form.get("password2"):
            flash("Les mots de passe ne correspondent pas.", "danger")
        else:
            u = User(email=email, name=name, role="admin")
            u.set_password(pw)
            db.session.add(u)
            db.session.flush()
            audit("creation_admin_initial", "user", u.id, email, user=u)
            db.session.commit()
            flash("Compte administrateur créé. Connectez-vous.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        now = datetime.now()
        if user and user.locked_until and user.locked_until > now:
            flash("Compte temporairement verrouillé suite à trop de tentatives. Réessayez plus tard.", "danger")
            audit("connexion_refusee_verrou", "user", user.id, email, user=user)
            db.session.commit()
        elif user and user.active and user.check_password(pw):
            user.failed_logins = 0
            user.locked_until = None
            user.last_login = now
            login_user(user, remember=False)
            session.permanent = True
            audit("connexion", "user", user.id, user=user)
            db.session.commit()
            nxt = request.args.get("next")
            if not nxt or urlparse(nxt).netloc:
                nxt = url_for("dashboard.index")
            return redirect(nxt)
        else:
            if user:
                user.failed_logins += 1
                if user.failed_logins >= MAX_ATTEMPTS:
                    user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                    user.failed_logins = 0
                audit("connexion_echouee", "user", user.id, email, user=user)
            else:
                audit("connexion_echouee", "user", None, f"courriel inconnu: {email}")
            db.session.commit()
            flash("Courriel ou mot de passe invalide.", "danger")
    return render_template("auth/login.html")


@bp.route("/logout")
@login_required
def logout():
    audit("deconnexion", "user", current_user.id)
    db.session.commit()
    logout_user()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/mot-de-passe", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        cur = request.form.get("current") or ""
        pw = request.form.get("password") or ""
        problem = password_problem(pw)
        if not current_user.check_password(cur):
            flash("Mot de passe actuel invalide.", "danger")
        elif problem:
            flash(problem, "danger")
        elif pw != request.form.get("password2"):
            flash("Les mots de passe ne correspondent pas.", "danger")
        else:
            current_user.set_password(pw)
            current_user.must_change_password = False
            audit("changement_mot_de_passe", "user", current_user.id)
            db.session.commit()
            flash("Mot de passe modifié.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/change_password.html")
