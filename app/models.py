import json
from datetime import datetime, date

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from . import db, login_manager

ROLES = {
    "admin": "Administrateur",
    "gestionnaire": "Gestionnaire",
    "lecture": "Lecture seule",
}

VEHICLE_STATUSES = ["Actif", "Inactif", "Hors service", "Vendu"]

WO_STATUSES = [
    ("nouveau", "Nouveau / à trier"),
    ("ouvert", "Ouvert"),
    ("assigne", "Assigné"),
    ("rdv", "Rendez-vous planifié"),
    ("en_reparation", "En réparation"),
    ("attente_pieces", "En attente de pièces"),
    ("termine", "Terminé"),
    ("annule", "Annulé"),
]
WO_STATUS_LABELS = dict(WO_STATUSES)
WO_CLOSED = ("termine", "annule")
WO_PRIORITIES = [("basse", "Basse"), ("normale", "Normale"), ("haute", "Haute"), ("urgente", "Urgente")]
WO_SOURCES = [("telephone", "Téléphone"), ("courriel", "Courriel"), ("formulaire", "Formulaire web"),
              ("manuel", "Saisie manuelle"), ("inspection", "Inspection")]

TOOL_STATUSES = [
    ("en_service", "En service"),
    ("en_reparation", "En réparation"),
    ("perdu", "Perdu / volé"),
    ("retire", "Retiré"),
]
TOOL_STATUS_LABELS = dict(TOOL_STATUSES)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50))
    password_hash = db.Column(db.String(300), nullable=False)
    role = db.Column(db.String(20), default="gestionnaire", nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    last_login = db.Column(db.DateTime)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def is_active(self):
        return self.active

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def can_edit(self):
        return self.role in ("admin", "gestionnaire")

    @property
    def role_label(self):
        return ROLES.get(self.role, self.role)


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


class Driver(TimestampMixin, db.Model):
    """Chauffeur (n'a pas de compte; reçoit des courriels)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), index=True)
    phone = db.Column(db.String(50))
    department = db.Column(db.String(100))
    active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text)

    vehicles = db.relationship("Vehicle", back_populates="driver")


class Garage(TimestampMixin, db.Model):
    """Garage ou atelier de réparation (véhicules ou outils)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(20), default="vehicule")  # vehicule / outil / les_deux
    contact = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(200))
    address = db.Column(db.String(300))
    notes = db.Column(db.Text)
    # Un gestionnaire peut désactiver ; seul un administrateur peut réactiver.
    active = db.Column(db.Boolean, default=True, nullable=False, server_default="1")


class Vehicle(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unit_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    beneva_number = db.Column(db.String(20))
    status = db.Column(db.String(20), default="Actif", nullable=False)
    year = db.Column(db.Integer)
    make = db.Column(db.String(60))
    model = db.Column(db.String(120))
    plate = db.Column(db.String(30), index=True)
    vin = db.Column(db.String(40))
    department = db.Column(db.String(100))
    driver_id = db.Column(db.Integer, db.ForeignKey("driver.id"))
    driver = db.relationship("Driver", back_populates="vehicles")

    km_current = db.Column(db.Integer)
    km_annual = db.Column(db.Integer)
    ownership = db.Column(db.String(200))       # ACHAT / LOCATION / financement ...
    financing = db.Column(db.String(200))
    lease_end = db.Column(db.Date)
    monthly_payment = db.Column(db.Float)
    balance_due = db.Column(db.Float)
    warranty_end = db.Column(db.Date)
    registration_expiry = db.Column(db.Date)    # SAAQ
    mechanical_inspection = db.Column(db.Date)  # SAAQ vérification mécanique
    mechanical_status = db.Column(db.String(60))
    weight = db.Column(db.Integer)
    gvwr = db.Column(db.Integer)                # PNBV
    remarks = db.Column(db.Text)
    extra_json = db.Column(db.Text, default="{}")  # toutes les autres colonnes Excel

    workorders = db.relationship("WorkOrder", back_populates="vehicle")
    tools = db.relationship("Tool", back_populates="vehicle")

    @property
    def extra(self):
        try:
            return json.loads(self.extra_json or "{}")
        except ValueError:
            return {}

    @extra.setter
    def extra(self, value):
        self.extra_json = json.dumps(value, ensure_ascii=False, default=str)

    @property
    def label(self):
        parts = [f"#{self.unit_number}", str(self.year or ""), self.make or "", self.model or ""]
        return " ".join(p for p in parts if p).strip()

    @property
    def open_workorders(self):
        return [w for w in self.workorders if w.status not in WO_CLOSED]


class ToolCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class Tool(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_tag = db.Column(db.String(50), unique=True, index=True)  # # inventaire / étiquette
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("tool_category.id"))
    category = db.relationship("ToolCategory")
    brand = db.Column(db.String(100))
    model = db.Column(db.String(100))
    serial = db.Column(db.String(100))
    status = db.Column(db.String(20), default="en_service", nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"))  # None = entrepôt
    vehicle = db.relationship("Vehicle", back_populates="tools")
    location_note = db.Column(db.String(200))
    purchase_date = db.Column(db.Date)
    value = db.Column(db.Float)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    notes = db.Column(db.Text)

    workorders = db.relationship("WorkOrder", back_populates="tool")
    moves = db.relationship("ToolMove", back_populates="tool", order_by="ToolMove.created_at.desc()")

    @property
    def status_label(self):
        return TOOL_STATUS_LABELS.get(self.status, self.status)

    @property
    def location_label(self):
        if self.vehicle:
            return f"Camion #{self.vehicle.unit_number}"
        return self.location_note or "Entrepôt"


class ToolMove(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"), nullable=False)
    tool = db.relationship("Tool", back_populates="moves")
    from_label = db.Column(db.String(200))
    to_label = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class InventoryCheck(db.Model):
    """Vérification de l'inventaire d'outils d'un camion."""
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"), nullable=False)
    vehicle = db.relationship("Vehicle")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    result_json = db.Column(db.Text)  # {tool_id: "present"|"manquant"|"brise"}
    missing_count = db.Column(db.Integer, default=0)
    broken_count = db.Column(db.Integer, default=0)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class WorkOrder(TimestampMixin, db.Model):
    """Bon de travail : réparation d'un véhicule ou d'un outil."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), default="vehicule", nullable=False)  # vehicule / outil
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicle.id"))
    vehicle = db.relationship("Vehicle", back_populates="workorders")
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"))
    tool = db.relationship("Tool", back_populates="workorders")

    title = db.Column(db.String(250), nullable=False)
    description = db.Column(db.Text)
    source = db.Column(db.String(20), default="manuel")
    priority = db.Column(db.String(20), default="normale")
    status = db.Column(db.String(20), default="ouvert", nullable=False, index=True)

    reporter_name = db.Column(db.String(200))
    reporter_email = db.Column(db.String(200))
    reporter_phone = db.Column(db.String(50))

    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    garage_id = db.Column(db.Integer, db.ForeignKey("garage.id"))
    garage = db.relationship("Garage")

    due_date = db.Column(db.Date)            # date approximative de retour / résolution
    closed_at = db.Column(db.DateTime)
    resolution = db.Column(db.Text)
    cost = db.Column(db.Float)
    invoice_number = db.Column(db.String(100))

    last_activity_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    last_overdue_notice = db.Column(db.DateTime)
    last_stale_notice = db.Column(db.DateTime)
    overdue_notice_count = db.Column(db.Integer, default=0, nullable=False)

    events = db.relationship("WorkOrderEvent", back_populates="workorder",
                             order_by="WorkOrderEvent.created_at.desc()", cascade="all, delete-orphan")
    appointments = db.relationship("Appointment", back_populates="workorder",
                                   order_by="Appointment.start_at", cascade="all, delete-orphan")
    attachments = db.relationship("Attachment", back_populates="workorder", cascade="all, delete-orphan")

    @property
    def number(self):
        return f"BT-{self.created_at.year}-{self.id:05d}"

    @property
    def status_label(self):
        return WO_STATUS_LABELS.get(self.status, self.status)

    @property
    def is_closed(self):
        return self.status in WO_CLOSED

    @property
    def is_overdue(self):
        return not self.is_closed and self.due_date is not None and self.due_date < date.today()

    @property
    def subject_label(self):
        if self.kind == "outil" and self.tool:
            return f"Outil {self.tool.asset_tag or ''} {self.tool.name}".strip()
        if self.vehicle:
            return f"Unité {self.vehicle.label}"
        return "Non identifié"

    @property
    def next_appointment(self):
        upcoming = [a for a in self.appointments if a.start_at >= datetime.now() and not a.cancelled]
        return upcoming[0] if upcoming else None


class WorkOrderEvent(db.Model):
    """Historique / commentaires d'un bon de travail."""
    id = db.Column(db.Integer, primary_key=True)
    workorder_id = db.Column(db.Integer, db.ForeignKey("work_order.id"), nullable=False)
    workorder = db.relationship("WorkOrder", back_populates="events")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    kind = db.Column(db.String(30), default="note")  # note / statut / courriel / rdv / systeme
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class Appointment(TimestampMixin, db.Model):
    """Rendez-vous au garage pour un bon de travail."""
    id = db.Column(db.Integer, primary_key=True)
    workorder_id = db.Column(db.Integer, db.ForeignKey("work_order.id"), nullable=False)
    workorder = db.relationship("WorkOrder", back_populates="appointments")
    start_at = db.Column(db.DateTime, nullable=False)
    garage_id = db.Column(db.Integer, db.ForeignKey("garage.id"))
    garage = db.relationship("Garage")
    # Qui amène le camion : "chauffeur" ou "utilisateur" (responsable)
    attendee_type = db.Column(db.String(20), default="chauffeur")
    attendee_driver_id = db.Column(db.Integer, db.ForeignKey("driver.id"))
    attendee_driver = db.relationship("Driver")
    attendee_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    attendee_user = db.relationship("User")
    notes = db.Column(db.Text)
    notified_at = db.Column(db.DateTime)
    reminder_sent_at = db.Column(db.DateTime)
    cancelled = db.Column(db.Boolean, default=False, nullable=False)

    @property
    def attendee_name(self):
        if self.attendee_type == "utilisateur" and self.attendee_user:
            return self.attendee_user.name
        if self.attendee_driver:
            return self.attendee_driver.name
        return "—"

    @property
    def attendee_email(self):
        if self.attendee_type == "utilisateur" and self.attendee_user:
            return self.attendee_user.email
        if self.attendee_driver:
            return self.attendee_driver.email
        return None


class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    workorder_id = db.Column(db.Integer, db.ForeignKey("work_order.id"), nullable=False)
    workorder = db.relationship("WorkOrder", back_populates="attachments")
    filename = db.Column(db.String(300), nullable=False)
    stored_name = db.Column(db.String(300), nullable=False)
    content_type = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class InboundEmail(db.Model):
    """Courriels lus dans la boîte de signalement (évite les doublons)."""
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(500), unique=True, nullable=False)
    sender = db.Column(db.String(300))
    subject = db.Column(db.String(500))
    received_at = db.Column(db.DateTime, default=datetime.now)
    workorder_id = db.Column(db.Integer, db.ForeignKey("work_order.id"))
    workorder = db.relationship("WorkOrder")


class OutboundEmail(db.Model):
    """Journal des courriels envoyés (succès ou échec)."""
    id = db.Column(db.Integer, primary_key=True)
    to = db.Column(db.String(500))
    subject = db.Column(db.String(500))
    ok = db.Column(db.Boolean, default=True)
    error = db.Column(db.Text)
    workorder_id = db.Column(db.Integer, db.ForeignKey("work_order.id"))
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    user_label = db.Column(db.String(200))
    action = db.Column(db.String(50), nullable=False, index=True)
    entity = db.Column(db.String(50), index=True)
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip = db.Column(db.String(60))


class Setting(db.Model):
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)


DEFAULT_SETTINGS = {
    "company_name": "Kubios",
    "base_url": "http://localhost:8080",
    # Courriel : "aucun", "smtp_imap" ou "graph" (Microsoft 365)
    "mail_backend": "aucun",
    "mail_from": "",
    "smtp_host": "", "smtp_port": "587", "smtp_tls": "1", "smtp_user": "", "smtp_password": "",
    "imap_host": "", "imap_port": "993", "imap_user": "", "imap_password": "", "imap_folder": "INBOX",
    "graph_tenant_id": "", "graph_client_id": "", "graph_client_secret": "", "graph_mailbox": "",
    "inbox_enabled": "0",
    "inbox_poll_minutes": "5",
    "inbox_ack_sender": "1",
    # Responsables par défaut
    "default_fleet_manager_id": "",
    "default_tools_manager_id": "",
    "escalation_email": "",
    # Rappels
    "default_due_days": "7",
    "stale_days": "3",
    "overdue_repeat_days": "1",
    "escalate_after_notices": "3",
    "expiry_warning_days": "45",
    "digest_hour": "7",
    # Mises à jour
    "github_repo": "",
    "github_token": "",
    "update_check_hours": "6",
    # Installation automatique : "non", "nuit" (à l'heure choisie) ou "immediat" (dès la vérification)
    "update_auto_mode": "nuit",
    "update_install_hour": "3",
    # Formulaire public
    "public_form_enabled": "0",
    # Avertissement affiché sur la page de connexion (1re ligne = titre, paragraphes séparés par une ligne vide)
    "login_banner": (
        "ACCÈS AUTORISÉ SEULEMENT\n"
        "Vous êtes sur le point d'accéder à un système informatique privé de l'entreprise.\n\n"
        "L'accès à ce système est strictement réservé aux personnes autorisées. Les logiciels, programmes, "
        "fichiers et données présents sur ce système sont la propriété de l'entreprise ou sont utilisés sous licence.\n\n"
        "Toute utilisation, copie, modification, divulgation ou distribution non autorisée est interdite.\n\n"
        "Les activités effectuées sur ce système peuvent être surveillées, enregistrées et auditées conformément "
        "aux politiques de l'entreprise et aux lois applicables.\n\n"
        "En poursuivant l'ouverture de session, vous confirmez être autorisé à utiliser ce système et acceptez "
        "les présentes conditions."
    ),
}


def get_setting(key, default=None):
    s = db.session.get(Setting, key)
    if s is None or s.value is None:
        return DEFAULT_SETTINGS.get(key, default) if default is None else default
    return s.value


def set_setting(key, value):
    s = db.session.get(Setting, key)
    if s is None:
        s = Setting(key=key)
        db.session.add(s)
    s.value = "" if value is None else str(value)


def seed_defaults():
    for k, v in DEFAULT_SETTINGS.items():
        if db.session.get(Setting, k) is None:
            db.session.add(Setting(key=k, value=v))
    if ToolCategory.query.count() == 0:
        for name in ["Outillage électrique", "Outillage manuel", "Mesure / détection", "Soudure / brasage",
                     "Échelle / accès", "Sécurité / EPI", "Équipement frigorifique", "Autre"]:
            db.session.add(ToolCategory(name=name))
    db.session.commit()
