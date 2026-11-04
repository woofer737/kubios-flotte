"""Kubios Gestion flotte véhiculaire et inventaire / réparation outils."""
import os
import secrets
from datetime import timedelta

from flask import Flask, redirect, url_for, request
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.environ.get("KUBIOS_DATA_DIR", os.path.join(BASE_DIR, "data"))

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def get_version():
    try:
        with open(os.path.join(BASE_DIR, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def _secret_key():
    """Clé secrète persistée dans data/ (générée au premier démarrage)."""
    path = os.path.join(DATA_DIR, "secret.key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def create_app(start_scheduler=False):
    for sub in ("", "uploads", "backups"):
        os.makedirs(os.path.join(DATA_DIR, sub), exist_ok=True)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_secret_key(),
        SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(DATA_DIR, "kubios.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_DIR=os.path.join(DATA_DIR, "uploads"),
        BACKUP_DIR=os.path.join(DATA_DIR, "backups"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=10),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        APP_VERSION=get_version(),
    )

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Veuillez vous connecter."
    login_manager.login_message_category = "warning"

    from . import models  # noqa: F401
    from .auth import bp as auth_bp
    from .dashboard import bp as dash_bp
    from .vehicles import bp as veh_bp
    from .tools import bp as tools_bp
    from .workorders import bp as wo_bp
    from .admin import bp as admin_bp
    from .public import bp as public_bp

    for bp in (auth_bp, dash_bp, veh_bp, tools_bp, wo_bp, admin_bp, public_bp):
        app.register_blueprint(bp)

    from .helpers import register_template_helpers
    register_template_helpers(app)

    with app.app_context():
        db.create_all()
        models.seed_defaults()

    @app.before_request
    def _first_run():
        # Tant qu'aucun utilisateur n'existe, on redirige vers l'assistant de configuration.
        if request.endpoint in ("auth.setup", "static"):
            return
        if models.User.query.count() == 0:
            return redirect(url_for("auth.setup"))
        if current_user.is_authenticated and current_user.must_change_password \
                and request.endpoint not in ("auth.change_password", "auth.logout"):
            return redirect(url_for("auth.change_password"))

    if start_scheduler:
        from .scheduler import start
        start(app)

    return app
