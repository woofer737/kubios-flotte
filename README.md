# Kubios — Gestion flotte véhiculaire et inventaire / réparation outils

Logiciel web interne qui permet de :

- **Flotte** : consulter les véhicules importés de l'Excel (FLOTTE, SAAQ, HORS SERVICE), les chauffeurs et les garages, et suivre les échéances (immatriculation, vérification mécanique, fin de bail, garantie).
- **Bons de travail véhicules** : un chauffeur signale un bris par **téléphone** (le responsable ouvre le bon) ou par **courriel** (le bon s'ouvre tout seul). Le responsable est avisé par courriel. On fixe une date prévue de résolution, avec des rappels automatiques si elle est dépassée ou si le bon n'est pas mis à jour.
- **Rendez-vous garage** : on choisit qui amène le camion (chauffeur ou responsable). Cette personne reçoit la date, le garage, l'adresse, le téléphone et une invitation calendrier (.ics). Un rappel part la veille.
- **Dispatch** : vue des rendez-vous à venir et tableau des bons par étape.
- **Outils** : inventaire par camion ou entrepôt, déplacements, vérification d'inventaire d'un camion, et bons de réparation d'outils avec les mêmes rappels.
- **Sécurité** : connexion par courriel et mot de passe, verrouillage après 5 essais, 3 rôles (Administrateur, Gestionnaire, Lecture seule) et **journal d'audit** de toutes les actions.
- **Rapports** : listing de la flotte, coûts de réparation par camion, détail des réparations, inventaire des outils par camion. Vous pouvez les télécharger en PDF ou en Excel, ou les envoyer par courriel.
- **Garages / ateliers** : un gestionnaire peut désactiver un garage, mais seul un administrateur peut le réactiver.
- **Mises à jour** via les *Releases* GitHub, depuis l'écran Administration > Mises à jour.
- **Sauvegardes complètes** automatiques chaque nuit (30 conservées) et **restauration** en quelques clics.

---

## 1. Installation sur l'ordinateur serveur

Prérequis : Windows 10/11 et **Python 3.12+** (https://www.python.org/downloads/ — cocher « Add python.exe to PATH »).

1. Copier le dossier du logiciel, par exemple dans `C:\Logiciel\Kubios GAuto`.
2. Double-cliquer **`installer.bat`** (crée l'environnement Python et installe les composantes).
3. Clic droit sur **`installer-service.bat`** > **Exécuter en tant qu'administrateur**.
   - Le logiciel démarre maintenant **automatiquement avec l'ordinateur**, même si personne n'est connecté. Il se relance seul en cas de panne.
   - Le port 8080 est ouvert dans le pare-feu pour les autres postes.
4. Ouvrir **http://localhost:8080** et créer le compte administrateur (premier démarrage).
5. Depuis les autres postes : `http://NOM-DE-L-ORDINATEUR:8080`.

> L'ordinateur doit rester allumé (désactiver la mise en veille dans Windows : Paramètres > Système > Alimentation).

Pour tester sans service : `demarrer.bat`. Pour retirer le service : `desinstaller-service.bat` (en administrateur). Les données sont conservées.

### Où sont les données ?
Tout est dans le dossier **`data\`** : base de données `kubios.db`, pièces jointes, sauvegardes et journal `kubios.log`. **Copiez régulièrement `data\backups` ailleurs** (OneDrive, NAS).

### Sauvegarde et restauration
Chaque sauvegarde est un fichier `.zip` dans `data\backups`. Elle contient **toutes** les données, **toute** la configuration (utilisateurs, paramètres de courriel Microsoft 365, rappels, GitHub, avertissement de connexion) et les photos et pièces jointes.

- Automatique chaque nuit à 2 h, et avant chaque import, mise à jour ou restauration.
- **Administration > Sauvegardes > Restaurer** : remet le logiciel dans l'état choisi. Il faut taper `RESTAURER` pour confirmer. L'état actuel est sauvegardé juste avant, ce qui permet de revenir en arrière. Le logiciel redémarre ensuite.
- **Déménager sur un nouvel ordinateur** : installer Kubios sur le nouveau poste, créer un compte temporaire, puis *Restaurer depuis un fichier* avec le `.zip` de l'ancien poste. Ce sont ensuite les comptes de l'ancien poste qui s'appliquent.

## 2. Premiers pas

1. **Administration > Importer l'Excel** : choisir `LISTE CAMIONS POUR ASSURANCES.xlsx`. On peut réimporter plus tard : les unités sont mises à jour par numéro, et les bons de travail ne sont jamais touchés.
2. **Administration > Utilisateurs** : créer les responsables. Leur courriel sert à la connexion **et** aux rappels.
3. **Administration > Paramètres** : responsables par défaut (véhicules et outils), délais de rappel, adresse du logiciel (`http://NOM-ORDI:8080`, pour les liens dans les courriels) et courriel (voir section 3).
4. **Flotte > Garages** : ajouter vos garages (adresse et téléphone envoyés aux chauffeurs).
5. **Flotte > Chauffeurs** : vérifier les courriels importés. L'Excel contient des entrées comme « A vendre », « Chantier » ou « SPARE GARAGE » qui ne sont pas de vraies personnes : désactivez-les ou corrigez-les.
6. **Outils** : saisir l'inventaire et l'associer aux camions.

## 3. Connexion Microsoft 365 (courriel)

Le logiciel utilise l'API officielle **Microsoft Graph**. Elle sert à lire une boîte dédiée (ex. `bris@lysair.ca`) et à envoyer les avis. À faire **une fois** par un administrateur Microsoft 365 :

1. Créer (ou choisir) une boîte aux lettres partagée, ex. **`flotte@lysair.ca`**. Les chauffeurs y enverront leurs bris, et elle enverra les avis.
2. Aller sur https://entra.microsoft.com > **Applications > Inscriptions d'applications > Nouvelle inscription**.
   - Nom : `Kubios Flotte`, type de compte : *Ce répertoire organisationnel uniquement*, puis **Inscrire**.
3. Noter l'**ID d'application (client)** et l'**ID de l'annuaire (locataire)**.
4. **Certificats et secrets > Nouveau secret client** (24 mois). Copier la **Valeur** tout de suite, car elle ne sera plus affichée.
5. **Autorisations d'API > Ajouter > Microsoft Graph > Autorisations d'application** : cocher
   - `Mail.ReadWrite` (lire les bris et les marquer comme lus)
   - `Mail.Send` (envoyer les avis)
   
   puis **Accorder le consentement administrateur**.
6. *(Recommandé — sécurité)* Limiter l'application à la seule boîte `flotte@lysair.ca`, sinon elle peut accéder à toutes les boîtes. Commande à exécuter dans PowerShell Exchange Online :
   ```powershell
   Connect-ExchangeOnline
   New-DistributionGroup -Name "Kubios-Acces" -Type Security -Members flotte@lysair.ca
   New-ApplicationAccessPolicy -AppId <ID-APPLICATION> -PolicyScopeGroupId Kubios-Acces -AccessRight RestrictAccess -Description "Kubios: boite flotte seulement"
   ```
7. Dans Kubios > **Administration > Paramètres > Courriel** :
   - Système : **Microsoft 365**
   - Tenant ID, Client ID, Secret client : valeurs des étapes 3 et 4
   - Boîte aux lettres utilisée et Adresse d'expédition : `flotte@lysair.ca`
   - Cocher « Lire automatiquement la boîte… »
   - Cliquer **Enregistrer et tester** : vous recevez un courriel de test.

> Rappel : le secret client expire (24 mois). Notez la date et renouvelez-le à l'étape 4.

**Pour les chauffeurs** : écrire le **numéro d'unité dans l'objet**, ex. *« Unité 105 — pneu crevé »*. Le logiciel reconnaît aussi la plaque, ou le camion attitré à l'expéditeur. Un objet contenant « outil » ouvre une réparation d'outil. Si rien n'est reconnu, le bon est classé **« À trier »** sur le tableau de bord, et on le rattache en un clic.

Autre fournisseur (Gmail, Videotron…) : choisir **SMTP / IMAP** et entrer serveurs, ports et mot de passe d'application.

## 4. Rappels automatiques

| Événement | Destinataire | Fréquence |
|---|---|---|
| Nouveau bon (téléphone, courriel, inventaire) | Responsable assigné (+ accusé de réception à l'expéditeur d'un courriel) | immédiat |
| Réassignation | Nouveau responsable | immédiat |
| Date prévue dépassée | Responsable ; copie au courriel d'escalade après N avis | chaque jour (paramétrable) |
| Aucune mise à jour depuis X jours | Responsable | aux X jours (défaut 3) |
| Convocation rendez-vous garage + .ics | Chauffeur ou responsable désigné (copie au responsable du bon) | au moment de planifier |
| Rappel de rendez-vous | Personne désignée | la veille |
| Résumé quotidien (bons ouverts, échéances flotte) | Gestionnaires et administrateurs | lun-ven, 7 h |

## 5. Mises à jour via GitHub

**Mise en place (une fois)** :
1. Créer un dépôt **privé** sur GitHub, ex. `groupesrb/kubios-flotte`, et y pousser ce dossier (`data/` et `.venv/` sont exclus par `.gitignore`).
2. Créer un jeton : GitHub > Settings > Developer settings > *Fine-grained tokens*, accès **Contents : Read-only** sur ce dépôt seulement.
3. Kubios > Paramètres > **Mises à jour GitHub** : dépôt `groupesrb/kubios-flotte` et jeton.

**Publier une mise à jour** :
1. Modifier le code et augmenter le numéro dans le fichier **`VERSION`** (ex. `1.1.0`).
2. Pousser sur GitHub, puis créer une **Release** avec le tag `v1.1.0` et des notes de version.
3. C'est tout : **Kubios s'installe la mise à jour lui-même.**
   - Il vérifie GitHub toutes les 6 heures (réglable, 0 = jamais) et l'installe **la nuit à 3 h** (après la sauvegarde de 2 h).
   - Autres modes dans Paramètres : *dès qu'une version est détectée*, ou *non* (avertir seulement, avec un badge jaune sur le menu Administration).
   - Pour aller plus vite : **Administration > Mises à jour > Vérifier maintenant**, puis **Installer**.

**Filet de sécurité** : avant chaque installation, Kubios fait une sauvegarde complète et une copie du programme (`data\backups\code-*.zip`, 5 conservées). Si la nouvelle version **ne démarre pas** (2 essais), il **revient tout seul à l'ancienne**. Il note cette version comme « à ne pas réinstaller automatiquement » et envoie un courriel aux administrateurs. Si elle démarre et fonctionne pendant 60 secondes, la mise à jour est confirmée et les administrateurs reçoivent un courriel « Mise à jour installée ».

> Le filet de sécurité est actif à partir de la version 1.3.0 : la mise à jour *vers* la 1.3.0 se fait encore sans filet.
   - Sauvegarde automatique de la base, remplacement des fichiers (jamais `data/`), installation des dépendances, puis redémarrage (environ 15 s).

## 6. Commandes utiles

```bat
.venv\Scripts\python.exe manage.py import-excel "C:\chemin\fichier.xlsx"
.venv\Scripts\python.exe manage.py create-admin courriel@lysair.ca "Nom"
.venv\Scripts\python.exe manage.py reminders
.venv\Scripts\python.exe manage.py backup
```

Mot de passe administrateur perdu : `manage.py create-admin` crée un nouvel administrateur avec un mot de passe temporaire.

## 7. Architecture

- Python / Flask, base SQLite (`data\kubios.db`), serveur Waitress, tâches planifiées APScheduler.
- `app/` : `models.py` (données), `services.py` (bons de travail, rappels, courriels entrants), `mailer.py` (Microsoft 365 / SMTP / IMAP), `importer.py` (Excel), `updater.py` (GitHub), `scheduler.py`, écrans dans `templates/`.
- `run.py` : superviseur qui relance le serveur après une mise à jour (code 3) ou une panne.
