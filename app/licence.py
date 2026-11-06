"""Licences Kubios.

Une licence est un petit texte signé par Groupe SRB avec le logiciel « Kubios Licences » :
    KBL1.<données en base64>.<signature Ed25519 en base64>
Kubios ne contient que la clé PUBLIQUE : il peut vérifier une licence, mais pas en fabriquer.

Données : id, client, courriel, debut, fin (inclus), responsables (nombre maximal d'utilisateurs
Administrateur + Gestionnaire actifs), installation (facultatif : lie la licence à un poste), emis, note.

États :
  valide    licence en vigueur
  bientot   expire dans 30 jours ou moins (avertissement aux administrateurs)
  grace     expirée depuis 7 jours ou moins : tout fonctionne, bandeau rouge
  essai     aucune licence : période d'essai de 30 jours, 3 responsables
  expiree / essai_termine / invalide : accès bloqué (les administrateurs gardent l'accès à la page
            Licence et aux sauvegardes) ; courriels entrants, rappels et résumés suspendus.
"""
import base64
import json
import uuid
from datetime import date, timedelta

from .models import User, get_setting, set_setting

# Clés publiques acceptées (la première est la clé actuelle de Groupe SRB).
PUBLIC_KEYS = [
    "PAsKsTFGyvKEQjJ7QyZTTqbA2THTHh1PKlUBnt32hNQ=",
]

PREFIX = "KBL1"
TRIAL_DAYS = 30
TRIAL_MANAGERS = 3
WARN_DAYS = 30
GRACE_DAYS = 7
BLOCKED_STATES = ("expiree", "essai_termine", "invalide")
MANAGER_ROLES = ("admin", "gestionnaire")


class LicenceError(ValueError):
    pass


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def decode(key):
    """Vérifie la signature et retourne les données de la licence. Lève LicenceError sinon."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = "".join((key or "").split())  # tolère retours de ligne / espaces du copier-coller
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenceError("Ce texte n'est pas une clé de licence Kubios.")
    try:
        payload, sig = _b64d(parts[1]), _b64d(parts[2])
    except ValueError:
        raise LicenceError("Clé de licence endommagée (copie incomplète ?).") from None
    for pub in PUBLIC_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(sig, payload)
            break
        except (InvalidSignature, ValueError):
            continue
    else:
        raise LicenceError("Signature invalide : cette licence n'a pas été émise par Groupe SRB ou a été modifiée.")
    data = json.loads(payload.decode("utf-8"))
    try:
        data["debut_d"] = date.fromisoformat(data["debut"])
        data["fin_d"] = date.fromisoformat(data["fin"])
        data["responsables"] = int(data["responsables"])
    except (KeyError, ValueError):
        raise LicenceError("Licence incomplète.") from None
    return data


def installation_id():
    iid = get_setting("installation_id")
    if not iid:
        h = uuid.uuid4().hex.upper()
        iid = f"KB-{h[0:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"
        set_setting("installation_id", iid)
        from . import db
        db.session.commit()
    return iid


def managers_used():
    return User.query.filter(User.active.is_(True), User.role.in_(MANAGER_ROLES)).count()


def check_key(key):
    """Valide une clé pour CETTE installation (sans l'enregistrer). Retourne les données."""
    data = decode(key)
    if data.get("installation") and data["installation"] != installation_id():
        raise LicenceError(f"Cette licence est liée à une autre installation ({data['installation']}). "
                           f"L'ID de cette installation est {installation_id()}.")
    return data


def status(today=None):
    today = today or date.today()
    s = {"state": None, "lic": None, "error": None, "days_left": None, "used": managers_used(),
         "max": None, "installation": installation_id()}
    key = get_setting("license_key")
    if key:
        try:
            lic = check_key(key)
        except LicenceError as e:
            s.update(state="invalide", error=str(e), max=0)
            return s
        s["lic"], s["max"] = lic, lic["responsables"]
        left = (lic["fin_d"] - today).days
        s["days_left"] = left
        if today < lic["debut_d"]:
            s.update(state="invalide", error=f"Cette licence débute seulement le {lic['debut']}.")
        elif left >= WARN_DAYS + 1:
            s["state"] = "valide"
        elif left >= 0:
            s["state"] = "bientot"
        elif left >= -GRACE_DAYS:
            s["state"] = "grace"
        else:
            s["state"] = "expiree"
        return s
    start = get_setting("trial_start")
    if not start:
        start = today.isoformat()
        set_setting("trial_start", start)
        from . import db
        db.session.commit()
    end = date.fromisoformat(start) + timedelta(days=TRIAL_DAYS - 1)
    s.update(max=TRIAL_MANAGERS, days_left=(end - today).days, trial_end=end)
    s["state"] = "essai" if today <= end else "essai_termine"
    return s


def blocked():
    try:
        return status()["state"] in BLOCKED_STATES
    except Exception:  # noqa: BLE001 — une erreur de licence ne doit jamais faire planter le logiciel
        return False


def can_add_manager():
    """Peut-on ajouter un responsable (admin / gestionnaire actif) de plus ?"""
    s = status()
    return s["max"] is None or s["used"] < s["max"], s


def install(key, user_label=None):
    data = check_key(key)
    set_setting("license_key", "".join(key.split()))
    return data
