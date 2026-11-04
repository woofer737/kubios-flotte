"""Tâches planifiées : lecture de la boîte courriel, rappels, résumé quotidien, sauvegardes."""
import logging
import os
import shutil
from datetime import datetime, timedelta

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


def backup_db(app=None, label=None):
    """Sauvegarde complète (.zip : base + paramètres + pièces jointes + clé). Voir backup.py."""
    from .backup import create_backup
    return create_backup(app, label=label)


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
    with app.app_context():
        try:
            update_hours = int(get_setting("update_check_hours") or 6)
        except ValueError:
            update_hours = 6
    if update_hours > 0:
        from .updater import check_and_store
        _scheduler.add_job(_job(app, "vérification des mises à jour", check_and_store), "interval",
                           hours=update_hours, id="update_check",
                           next_run_time=datetime.now() + timedelta(minutes=2))
    _scheduler.start()
    log.info("Planificateur démarré (boîte courriel aux %s min)", poll)
