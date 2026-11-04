"""Commandes d'administration en ligne de commande.

    python manage.py import-excel "chemin\\fichier.xlsx"
    python manage.py create-admin courriel "Nom complet"
    python manage.py reminders      (exécute les rappels immédiatement)
    python manage.py backup
"""
import secrets
import sys

from app import create_app, db
from app.models import User


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    app = create_app()
    with app.app_context():
        if cmd == "import-excel":
            from app.importer import import_workbook
            print(import_workbook(sys.argv[2]))
        elif cmd == "create-admin":
            pw = secrets.token_urlsafe(10)
            u = User(email=sys.argv[2].lower(), name=sys.argv[3], role="admin", must_change_password=True)
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            print(f"Administrateur créé. Mot de passe temporaire : {pw}")
        elif cmd == "reminders":
            from app.services import run_reminders
            print(f"{run_reminders()} rappel(s) envoyé(s)")
        elif cmd == "backup":
            from app.scheduler import backup_db
            print(backup_db(app))
        else:
            print(__doc__)


if __name__ == "__main__":
    main()
