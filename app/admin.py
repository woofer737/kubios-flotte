import os
import secrets
import tempfile

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, send_file
from flask_login import login_required, current_user

from . import db
from .helpers import audit, admin_required, f_str
from .models import (User, AuditLog, OutboundEmail, InboundEmail, DEFAULT_SETTINGS, ROLES, get_setting,
                     set_setting)

bp = Blueprint("admin", __name__, url_prefix="/admin")

SECRET_KEYS = {"smtp_password", "imap_password", "graph_client_secret", "github_token"}


@bp.before_request
@login_required
@admin_required
def _guard():
    pass


# ------------------------------------------------------------------ utilisateurs

@bp.route("/utilisateurs")
def users():
    return render_template("admin/users.html", users=User.query.order_by(User.name).all())


@bp.route("/utilisateurs/nouveau", methods=["GET", "POST"])
@bp.route("/utilisateurs/<int:uid>", methods=["GET", "POST"])
def user_edit(uid=None):
    u = db.session.get(User, uid) if uid else User(role="gestionnaire", active=True)
    if uid and u is None:
        abort(404)
    temp_pw = None
    if request.method == "POST":
        email = (f_str("email") or "").lower()
        name = f_str("name")
        role = request.form.get("role", "gestionnaire")
        active = bool(request.form.get("active"))
        if not email or not name or role not in ROLES:
            flash("Nom, courriel et rôle requis.", "danger")
        elif User.query.filter(User.email == email, User.id != (u.id or 0)).first():
            flash("Ce courriel est déjà utilisé.", "danger")
        elif u.id == current_user.id and (role != "admin" or not active):
            flash("Vous ne pouvez pas retirer vos propres droits d'administrateur.", "danger")
        else:
            is_new = u.id is None
            before = f"{u.email}|{u.name}|{u.role}|{u.active}" if not is_new else ""
            u.email, u.name, u.role, u.active, u.phone = email, name, role, active, f_str("phone")
            if is_new or request.form.get("reset_password"):
                temp_pw = secrets.token_urlsafe(9)
                u.set_password(temp_pw)
                u.must_change_password = True
                u.failed_logins, u.locked_until = 0, None
            if is_new:
                db.session.add(u)
            db.session.flush()
            audit("creation" if is_new else "modification", "user", u.id,
                  f"{before} → {u.email}|{u.name}|{u.role}|{u.active}" + (" (mot de passe réinitialisé)" if temp_pw and not is_new else ""))
            db.session.commit()
            if temp_pw:
                from .mailer import send_mail, wrap_html
                sent = send_mail(u.email, "Votre accès au logiciel de gestion de flotte",
                                 wrap_html(f"<p>Bonjour {u.name},</p><p>Un accès a été créé/réinitialisé pour vous.</p>"
                                           f"<p>Adresse : <a href='{get_setting('base_url')}'>{get_setting('base_url')}</a><br>"
                                           f"Courriel : <b>{u.email}</b><br>Mot de passe temporaire : <b>{temp_pw}</b></p>"
                                           f"<p>Vous devrez le changer à la première connexion.</p>"))
                flash(f"Utilisateur enregistré. Mot de passe temporaire : {temp_pw} "
                      + ("(aussi envoyé par courriel)" if sent else "(courriel non envoyé — transmettez-le manuellement)"),
                      "success")
            else:
                flash("Utilisateur enregistré.", "success")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_edit.html", u=u)


# ------------------------------------------------------------------ paramètres

@bp.route("/parametres", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        changed = []
        for key in DEFAULT_SETTINGS:
            if key not in request.form:
                if key in ("smtp_tls", "inbox_enabled", "inbox_ack_sender", "public_form_enabled"):
                    val = "0"
                else:
                    continue
            else:
                val = request.form.get(key, "").strip()
            if key in SECRET_KEYS and val == "":
                continue  # champ secret laissé vide = inchangé
            if val != (get_setting(key) or ""):
                set_setting(key, val)
                changed.append(key if key not in SECRET_KEYS else f"{key} (secret)")
        if changed:
            audit("modification", "parametres", None, ", ".join(changed))
        db.session.commit()
        flash("Paramètres enregistrés." + (" Redémarrez le service pour changer la fréquence de lecture des courriels."
                                           if "inbox_poll_minutes" in changed else ""), "success")
        if request.form.get("action") == "test":
            from .mailer import test_connection, send_mail, wrap_html
            ok, msg = test_connection()
            flash(f"Test de connexion : {msg}", "success" if ok else "danger")
            if ok:
                sent = send_mail(current_user.email, "Courriel de test",
                                 wrap_html("<p>La configuration du courriel fonctionne.</p>"))
                flash(f"Courriel de test {'envoyé à ' + current_user.email if sent else 'non envoyé (voir journal)'}.",
                      "success" if sent else "danger")
        elif request.form.get("action") == "inbox":
            from .services import process_inbox
            try:
                n = process_inbox()
                flash(f"Lecture de la boîte : {n} nouveau(x) bon(s) de travail.", "success")
            except Exception as e:  # noqa: BLE001
                db.session.rollback()
                flash(f"Erreur de lecture : {e}", "danger")
        return redirect(url_for("admin.settings"))
    values = {k: get_setting(k) for k in DEFAULT_SETTINGS}
    managers = User.query.filter(User.active.is_(True), User.role.in_(["admin", "gestionnaire"])).order_by(User.name).all()
    return render_template("admin/settings.html", s=values, secrets=SECRET_KEYS, managers=managers)


# ------------------------------------------------------------------ audit et courriels

@bp.route("/audit")
def audit_log():
    q = AuditLog.query
    if request.args.get("utilisateur"):
        q = q.filter(AuditLog.user_label.ilike(f"%{request.args['utilisateur']}%"))
    if request.args.get("entite"):
        q = q.filter(AuditLog.entity == request.args["entite"])
    if request.args.get("action"):
        q = q.filter(AuditLog.action == request.args["action"])
    page = request.args.get("page", 1, type=int)
    pag = q.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=100, error_out=False)
    entities = [e for (e,) in db.session.query(AuditLog.entity).distinct() if e]
    actions = [a for (a,) in db.session.query(AuditLog.action).distinct() if a]
    return render_template("admin/audit.html", pag=pag, entities=sorted(entities), actions=sorted(actions))


@bp.route("/courriels")
def emails():
    out = OutboundEmail.query.order_by(OutboundEmail.created_at.desc()).limit(200).all()
    inc = InboundEmail.query.order_by(InboundEmail.received_at.desc()).limit(100).all()
    return render_template("admin/emails.html", out=out, inc=inc)


# ------------------------------------------------------------------ import Excel

@bp.route("/import", methods=["GET", "POST"])
def import_excel():
    if request.method == "POST":
        fs = request.files.get("file")
        if not fs or not fs.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("Choisissez un fichier Excel (.xlsx).", "danger")
            return redirect(url_for("admin.import_excel"))
        from .importer import import_workbook
        from .scheduler import backup_db
        backup_db()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        try:
            fs.save(tmp.name)
            tmp.close()
            stats = import_workbook(tmp.name)
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            flash(f"Erreur d'importation : {e}", "danger")
            return redirect(url_for("admin.import_excel"))
        finally:
            os.unlink(tmp.name)
        audit("import_excel", "vehicule", None, f"{fs.filename}: {stats}")
        db.session.commit()
        flash(f"Import terminé : {stats['crees']} créé(s), {stats['mis_a_jour']} mis à jour, "
              f"{stats['chauffeurs']} chauffeur(s) ajouté(s), {stats['saaq']} fiche(s) SAAQ, "
              f"{stats['hors_service']} hors service.", "success")
        return redirect(url_for("vehicles.index"))
    return render_template("admin/import.html")


# ------------------------------------------------------------------ sauvegardes

def _backup_file(name):
    """Retourne le chemin d'une sauvegarde existante (nom vérifié, aucun chemin accepté)."""
    d = current_app.config["BACKUP_DIR"]
    if name != os.path.basename(name) or not name.startswith("kubios-") \
            or not name.endswith((".zip", ".db")) or name not in os.listdir(d):
        abort(404)
    return os.path.join(d, name)


@bp.route("/sauvegardes", methods=["GET", "POST"])
def backups():
    from .backup import create_backup, list_backups
    if request.method == "POST":
        path = create_backup(label="manuelle")
        audit("sauvegarde", "systeme", None, os.path.basename(path))
        db.session.commit()
        flash(f"Sauvegarde créée : {os.path.basename(path)}", "success")
        return redirect(url_for("admin.backups"))
    return render_template("admin/backups.html", files=list_backups(),
                           folder=current_app.config["BACKUP_DIR"])


@bp.route("/sauvegardes/<name>")
def backup_download(name):
    path = _backup_file(name)
    audit("telechargement_sauvegarde", "systeme", None, name)
    db.session.commit()
    return send_file(path, as_attachment=True)


def _do_restore(path, label):
    from .backup import restore_backup
    from .updater import schedule_restart
    who = f"{current_user.email} (id {current_user.id})"
    try:
        safety = restore_backup(path)
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        audit("restauration_echec", "systeme", None, f"{label}: {e}")
        db.session.commit()
        flash(f"Restauration impossible : {e}", "danger")
        return redirect(url_for("admin.backups"))
    # L'audit est écrit dans la base restaurée : on garde ainsi la trace de l'opération.
    audit("restauration", "systeme", None, f"{label} restaurée par {who}; état précédent conservé dans {safety}",
          user=False)
    db.session.commit()
    schedule_restart(1.5)
    return render_template("admin/restored.html", label=label, safety=safety)


@bp.route("/sauvegardes/<name>/restaurer", methods=["POST"])
def backup_restore(name):
    path = _backup_file(name)
    if request.form.get("confirm") != "RESTAURER":
        flash("Tapez RESTAURER pour confirmer.", "warning")
        return redirect(url_for("admin.backups"))
    return _do_restore(path, name)


@bp.route("/sauvegardes/televerser", methods=["POST"])
def backup_upload():
    """Restaurer à partir d'un fichier venant d'ailleurs (ex. migration vers un nouvel ordinateur)."""
    fs = request.files.get("file")
    if not fs or not fs.filename.lower().endswith((".zip", ".db")):
        flash("Choisissez un fichier de sauvegarde Kubios (.zip ou .db).", "danger")
        return redirect(url_for("admin.backups"))
    if request.form.get("confirm") != "RESTAURER":
        flash("Tapez RESTAURER pour confirmer.", "warning")
        return redirect(url_for("admin.backups"))
    ext = ".zip" if fs.filename.lower().endswith(".zip") else ".db"
    from datetime import datetime
    name = f"kubios-{datetime.now():%Y%m%d-%H%M%S}-televersee{ext}"
    path = os.path.join(current_app.config["BACKUP_DIR"], name)
    fs.save(path)
    return _do_restore(path, f"{fs.filename} (copiée sous {name})")


# ------------------------------------------------------------------ mises à jour

@bp.route("/mises-a-jour", methods=["GET", "POST"])
def updates():
    from . import updater
    from .scheduler import backup_db
    info, error, log = None, None, None
    action = request.form.get("action") if request.method == "POST" else None
    if get_setting("github_repo"):
        # Ouvrir la page ou cliquer « Vérifier maintenant » interroge GitHub immédiatement.
        info = updater.check_and_store()
        if action == "check":
            audit("verification_mise_a_jour", "systeme", None,
                  f"dernière version : {info['latest']}" if info else "échec")
            db.session.commit()
            st = updater.cached_status()
            if st["error"]:
                flash(f"Vérification impossible : {st['error']}", "danger")
            elif info and info["available"]:
                flash(f"Nouvelle version disponible : {info['latest']}", "success")
            else:
                flash("Vérification faite : le logiciel est à jour.", "info")
            return redirect(url_for("admin.updates"))
    if action == "install" and info and info["available"]:
        try:
            log = updater.apply(info["zip_url"], backup_db)
            audit("mise_a_jour_logiciel", "systeme", None, f"{info['current']} → {info['latest']}")
            db.session.commit()
            updater.schedule_restart()
        except Exception as e:  # noqa: BLE001
            error = f"Échec de la mise à jour : {e}"
            audit("mise_a_jour_echec", "systeme", None, error)
            db.session.commit()
    status = updater.cached_status()
    if not error and status["error"]:
        error = status["error"]
    return render_template("admin/updates.html", info=info, error=error, log=log, repo=get_setting("github_repo"),
                           status=status, every=get_setting("update_check_hours"))
