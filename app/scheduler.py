"""Tâches planifiées : lecture de la boîte courriel, rappels, résumé quotidien, sauvegardes."""
import logging
import os
import shutil
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger("kubios.scheduler")
_scheduler = None


def _job(app, name, fn):
    def run():
        with app.app_context():
            try:
                result = fn()
                if result:
                    log.info("%s : %s", name, result)
            except Exception:  # noqa: BLE001
                log.exception("Tâche %s en échec", name)
                from . import db
                db.session.rollback()
    return run


def inbox_job():
    from .models import get_setting
    from .services import process_inbox
    if get_setting("inbox_enabled") == "1":
        return process_inbox()


_last_digest = {"date": None}


def digest_job():
    from .models import get_setting
    from .services import send_daily_digest
    now = datetime.now()
    hour = int(get_setting("digest_hour") or 7)
    if now.weekday() < 5 and now.hour >= hour and _last_digest["date"] != now.date():
        _last_digest["date"] = now.date()
        send_daily_digest()
        return "résumé envoyé"


def backup_db(app=None):
    from flask import current_app
    cfg = (app or current_app).config
    src = cfg["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    if not os.path.exists(src):
        return None
    dest = os.path.join(cfg["BACKUP_DIR"], f"kubios-{datetime.now():%Y%m%d-%H%M%S}.db")
    import sqlite3
    with sqlite3.connect(src) as s, sqlite3.connect(dest) as d:
        s.backup(d)
    # On garde les 30 dernières sauvegardes
    files = sorted(f for f in os.listdir(cfg["BACKUP_DIR"]) if f.endswith(".db"))
    for old in files[:-30]:
        os.remove(os.path.join(cfg["BACKUP_DIR"], old))
    return dest


def start(app):
    global _scheduler
    if _scheduler:
        return
    from .models import get_setting
    from .services import run_reminders
    with app.app_context():
        poll = max(1, int(get_setting("inbox_poll_minutes") or 5))
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_job(app, "courriels entrants", inbox_job), "interval", minutes=poll,
                       id="inbox", next_run_time=datetime.now())
    _scheduler.add_job(_job(app, "rappels", run_reminders), "interval", minutes=30, id="reminders")
    _scheduler.add_job(_job(app, "résumé", digest_job), "interval", minutes=15, id="digest")
    _scheduler.add_job(_job(app, "sauvegarde", lambda: backup_db(app)), "cron", hour=2, minute=0, id="backup")
    _scheduler.start()
    log.info("Planificateur démarré (boîte courriel aux %s min)", poll)
