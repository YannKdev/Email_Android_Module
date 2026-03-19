# database.py
import os
import logging
import psycopg2
from psycopg2 import extras, OperationalError
import time
from contextlib import contextmanager
import unicodedata
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# Informations de connexion
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

APPS_STATUS_UNPROCESSED = "unprocessed"
APPS_STATUS_PROCESSING = "processing"
APPS_STATUS_PROCESSED = "processed"
APPS_STATUS_ERROR = "error"
APPS_STATUS_ERROR_COUNTRY = "error_country"
APPS_STATUS_ERROR_VERSION = "error_version"

ERROR = "ERROR"
OK = "OK"
# Variable globale pour stocker la connexion
_conn = None  # Connexion globale

def get_connection(retries=3, delay=2):
    """
    Retourne la connexion PostgreSQL.
    Reconnecte automatiquement si la connexion est fermée ou a échoué.
    """
    global _conn

    for attempt in range(retries):
        try:
            if _conn is None or _conn.closed:
                _conn = psycopg2.connect(**DB_CONFIG)
            # Test rapide de la connexion
            _conn.cursor().execute("SELECT 1;")
            return _conn
        except (OperationalError, psycopg2.InterfaceError):
            logger.warning(f"Connexion perdue, tentative {attempt + 1}...")
            _conn = None
            time.sleep(delay)

    raise ConnectionError("Impossible de se reconnecter à PostgreSQL après plusieurs tentatives.")

def close_connection():
    """Ferme la connexion si elle est ouverte"""
    global _conn
    if _conn and not _conn.closed:
        _conn.close()
        _conn = None

@contextmanager
def get_cursor(dictionary=False):
    """
    Context manager pour obtenir un curseur PostgreSQL.
    Utilisation :
        with get_cursor(dictionary=True) as cur:
            cur.execute("SELECT * FROM table;")
    La connexion et le curseur sont gérés automatiquement.
    """
    conn = get_connection()
    cur = None
    try:
        if dictionary:
            cur = conn.cursor(cursor_factory=extras.DictCursor)
        else:
            cur = conn.cursor()
        yield cur
        conn.commit()  # commit automatique après le bloc
    except Exception as e:
        conn.rollback()  # rollback si erreur
        raise e
    finally:
        if cur:
            cur.close()



def get_record(table_name, limit=5):
    """
    Récupère les premiers enregistrements d'une table.
    Renvoie une liste de dictionnaires.
    """
    try:
        with get_cursor(dictionary=True) as cur:
            query = f"SELECT * FROM {table_name} LIMIT %s;"
            cur.execute(query, (limit,))
            rows = cur.fetchall()
            return rows
    except Exception as e:
        logger.error(f"Erreur dans get_record: {e}")
        return []
def truncate_all_tables():
    """
    Vide les tables results, packages, apps, accounts et emulators en respectant les dépendances (CASCADE).
    Ordre important: results/packages/apps dépendent de packages/apps, accounts dépend de emulators.
    """
    try:
        with get_cursor() as cur:
            # Ordre correct: d'abord les tables avec références, puis celles référencées
            query = "TRUNCATE TABLE results, packages, apps, accounts, emulators CASCADE;"
            cur.execute(query)
            return True
    except Exception as e:
        print(f"Erreur dans truncate_all_tables: {e}")
        return False


def add_app(name):
    """
    Ajoute une nouvelle App avec le status 'unprocessed'.
    Retourne True si l'App a été ajoutée, False si elle existait déjà.
    """
    try:
        # Normalisation ASCII (é -> e, ù -> u, etc.)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = str.lower(name)
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO Apps (Name, Status)
                VALUES (%s, %s)
                ON CONFLICT (Name) DO NOTHING
                RETURNING Name;
                """,
                (name, 'unprocessed')
            )
            result = cur.fetchone()
            if result:
                #print(f"App '{name}' ajoutée avec status 'unprocessed'.")
                return True  # App ajoutée
            else:
                print(f"App '{name}' existe déjà.")
                return False  # App existait déjà
    except Exception as e:
        print(f"Erreur lors de l'ajout de l'app: {e}")
        return None

def get_next_unprocessed_app():
    """
    Récupère une App dont le status est 'unprocessed' et la met à 'processing'.
    Renvoie le nom de l'App ou None si aucune n'est disponible.
    """
    try:
        with get_cursor() as cur:
            # Récupérer une App non traitée
            cur.execute(
                "SELECT Name FROM Apps WHERE Status = 'unprocessed' LIMIT 1 FOR UPDATE;"
            )
            row = cur.fetchone()
            if row:
                app_name = row[0]
                # Mettre à jour le statut à 'processing'
                cur.execute(
                    "UPDATE Apps SET Status = 'processing' WHERE Name = %s;",
                    (app_name,)
                )
                return app_name
            return None
    except Exception as e:
        print(f"Erreur lors de la récupération de l'app non traitée: {e}")
        return None

def mark_app_processed(name):
    """
    Change le status d'une App à 'processed'.
    Retourne True si le statut a été modifié, False sinon.
    """
    try:
        name = str.lower(name)
        with get_cursor() as cur:
            cur.execute(
                "UPDATE Apps SET Status = 'processed' WHERE Name = %s AND Status <> 'processed';",
                (name,)
            )
            if cur.rowcount > 0:
                #print(f"App '{name}' marquée comme 'processed'.")
                return True
            else:
                print(f"Aucune modification pour l'App '{name}' (inexistante ou déjà 'processed').")
                return False
    except Exception as e:
        print(f"Erreur lors de la mise à jour du status de l'app: {e}")
        return False

def add_package(package_name, app_name, original_app, status='unprocessed'):
    """
    Ajoute un package dans la table Packages.
    Retourne True si le package a été ajouté, False si le PackageName existait déjà.
    Raise une erreur si OriginalApp n'existe pas.
    """
    try:
        package_name = str.lower(package_name)
        original_app = str.lower(original_app)
        pipeline = "OK"
        with get_cursor() as cur:
            # Vérifier que OriginalApp existe
            cur.execute("SELECT Name FROM Apps WHERE Name = %s;", (original_app,))
            if not cur.fetchone():
                raise ValueError(f"OriginalApp '{original_app}' n'existe pas dans Apps.")

            # Insérer le package avec retour
            cur.execute(
                """
                INSERT INTO Packages (PackageName, AppName, OriginalApp, Status, Pipeline)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (PackageName) DO NOTHING
                RETURNING PackageName;
                """,
                (package_name, app_name, original_app, status, pipeline)
            )
            result = cur.fetchone()
            if result:
                #print(f"Package '{package_name}' ajouté avec succès.")
                return True
            else:
                print(f"Package '{package_name}' existe déjà.")
                return False

    except Exception as e:
        raise RuntimeError(f"Erreur lors de l'ajout du package: {e}")

def package_exists(package_name):
    """
    Vérifie si un package existe déjà dans la table Packages.

    Args:
        package_name: Nom du package à vérifier

    Returns:
        bool: True si le package existe, False sinon
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            cur.execute(
                "SELECT 1 FROM Packages WHERE PackageName = %s LIMIT 1;",
                (package_name,)
            )
            return cur.fetchone() is not None
    except Exception as e:
        print(f"Erreur lors de la vérification du package: {e}")
        return False

def update_package_status(package_name, new_status):
    """
    Modifie le status d'un package et enregistre le datetime de traitement.
    Retourne True si le status a été modifié, False si aucun package correspondant.
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            # Met à jour seulement si le statut est différent + processed_at
            cur.execute(
                "UPDATE Packages SET Status = %s, processed_at = NOW() WHERE PackageName = %s AND Status <> %s;",
                (new_status, package_name, new_status)
            )
            if cur.rowcount > 0:
                print(f"Package '{package_name}' mis à jour avec le status '{new_status}'.")
                return True
            else:
                print(f"Aucune modification pour le package '{package_name}' (inexistant ou déjà '{new_status}').")
                return False
    except Exception as e:
        print(f"Erreur lors de la mise à jour du package: {e}")
        return False
    
def update_app_status(app_name, new_status):
    """
    Modifie le status d'une app dans la table Apps.
    Retourne True si le status a été modifié, False si aucune app correspondante.
    """
    try:
        app_name = str.lower(app_name)
        with get_cursor() as cur:
            # Met à jour seulement si le statut est différent
            cur.execute(
                "UPDATE Apps SET Status = %s WHERE Name = %s AND Status <> %s;",
                (new_status, app_name, new_status)
            )
            if cur.rowcount > 0:
                #print(f"App '{app_name}' mis à jour avec le status '{new_status}'.")
                return True
            else:
                print(f"Aucune modification pour l'app '{app_name}' (inexistante ou déjà '{new_status}').")
                return False
    except Exception as e:
        print(f"Erreur lors de la mise à jour de l'app: {e}")
        return False
    
def update_package_pipeline(package_name, new_pipeline):
    """
    Modifie le pipeline d'un package et enregistre le datetime de traitement.
    Retourne True si le pipeline a été modifié, False si aucun package correspondant.
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            # Met à jour seulement si le statut est différent + processed_at
            cur.execute(
                "UPDATE Packages SET pipeline = %s, processed_at = NOW() WHERE PackageName = %s AND pipeline <> %s;",
                (new_pipeline, package_name, new_pipeline)
            )
            if cur.rowcount > 0:
                #print(f"Package '{package_name}' mis à jour avec le pipeline '{new_pipeline}'.")
                return True
            else:
                print(f"Aucune modification pour le package '{package_name}' (inexistant ou déjà '{new_pipeline}').")
                return False
    except Exception as e:
        print(f"Erreur lors de la mise à jour du package: {e}")
        return False


def add_result(har_data, package_name):
    """
    Ajoute un résultat (HAR) lié à un package.

    Args:
        har_data: Dictionnaire HAR (sera converti en JSONB) ou chaîne JSON
        package_name: Nom du package associé
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            # Vérifier que le package existe
            cur.execute("SELECT PackageName FROM Packages WHERE PackageName = %s;", (package_name,))
            if not cur.fetchone():
                raise ValueError(f"Package '{package_name}' n'existe pas.")

            # Convertir en Json pour JSONB si c'est un dict
            if isinstance(har_data, dict):
                har_json = extras.Json(har_data)
            else:
                har_json = har_data

            # Ajouter le HAR avec la date de scan
            cur.execute(
                "INSERT INTO Results (HAR, PackageName, date_scan) VALUES (%s, %s, NOW());",
                (har_json, package_name)
            )
            print(f"HAR ajouté pour le package '{package_name}'.")
    except Exception as e:
        raise RuntimeError(f"Erreur lors de l'ajout du résultat: {e}")

def get_hars_for_package(package_name):
    """
    Récupère tous les HAR associés à un package.
    Retourne une liste de textes HAR.
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT HAR FROM Results WHERE PackageName = %s;",
                (package_name,)
            )
            rows = cur.fetchall()
            return [row['har'] for row in rows]
    except Exception as e:
        print(f"Erreur lors de la récupération des HAR: {e}")
        return []


# ==========================================================
# ================= GESTION DES ÉMULATEURS =================
# ==========================================================

def reset_emulators():
    """
    Vide complètement la table emulators (truncate).
    Utilisé au démarrage pour repartir sur une base propre.

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute("TRUNCATE TABLE emulators;")
            cur.connection.commit()
            print("[Database] Table emulators réinitialisée")
            return True
    except Exception as e:
        print(f"[Database] Erreur lors du reset des émulateurs: {e}")
        return False


def add_emulator(nom, type_emu, status="STARTING"):
    """
    Ajoute un émulateur dans la table emulators.

    Args:
        nom (str): Nom de l'émulateur (ex: "emulator-5554")
        type_emu (str): Type d'émulateur ("Root" ou "PS")
        status (str): Status initial (par défaut "STARTING")

    Returns:
        bool: True si ajout réussi, False sinon
    """
    try:
        # Validation du type
        if type_emu not in ("Root", "PS"):
            print(f"[Database] Type émulateur invalide: {type_emu} (doit être 'Root' ou 'PS')")
            return False

        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO emulators (nom, type, status)
                VALUES (%s, %s, %s)
                ON CONFLICT (nom) DO UPDATE SET
                    type = EXCLUDED.type,
                    status = EXCLUDED.status;
                """,
                (nom, type_emu, status)
            )
            cur.connection.commit()
            print(f"[Database] Émulateur ajouté: {nom} ({type_emu}) - Status: {status}")
            return True
    except Exception as e:
        print(f"[Database] Erreur lors de l'ajout de l'émulateur {nom}: {e}")
        return False


def update_emulator_status(nom, new_status):
    """
    Met à jour le status d'un émulateur existant.

    Args:
        nom (str): Nom de l'émulateur (ex: "emulator-5554")
        new_status (str): Nouveau status (ex: "RUNNING", "OFFLINE", "STARTING")

    Returns:
        bool: True si mise à jour réussie, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE emulators
                SET status = %s
                WHERE nom = %s;
                """,
                (new_status, nom)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun émulateur trouvé avec le nom: {nom}")
                return False

            cur.connection.commit()
            #print(f"[Database] Status émulateur {nom} mis à jour: {new_status}")
            return True
    except Exception as e:
        print(f"[Database] Erreur lors de la mise à jour du status de {nom}: {e}")
        return False

def increment_emulator_finished(nom):
    """
    Incrémente le compteur apps_finished de +1 après un traitement réussi.

    Args:
        nom (str): Nom de l'émulateur

    Returns:
        bool: True si incrémentation réussie, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE emulators
                SET apps_finished = apps_finished + 1
                WHERE nom = %s;
                """,
                (nom,)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun émulateur trouvé avec le nom: {nom}")
                return False

            cur.connection.commit()
            return True
    except Exception as e:
        print(f"[Database] Erreur lors de l'incrémentation apps_finished pour {nom}: {e}")
        return False


def increment_emulator_error(nom):
    """
    Incrémente le compteur apps_error de +1 après une erreur.

    Args:
        nom (str): Nom de l'émulateur

    Returns:
        bool: True si incrémentation réussie, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE emulators
                SET apps_error = apps_error + 1
                WHERE nom = %s;
                """,
                (nom,)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun émulateur trouvé avec le nom: {nom}")
                return False

            cur.connection.commit()
            return True
    except Exception as e:
        print(f"[Database] Erreur lors de l'incrémentation apps_error pour {nom}: {e}")
        return False


# ==========================================================
# ================= GESTION DES COMPTES ====================
# ==========================================================

def get_available_account():
    """
    Récupère un compte Google disponible (non blacklisté et non utilisé).
    Marque automatiquement le compte comme in_use = True.

    Returns:
        dict: {"email": str, "mdp": str} si un compte est disponible
        None: Si aucun compte disponible
    """
    try:
        with get_cursor(dictionary=True) as cur:
            # Sélectionner et verrouiller un compte disponible
            cur.execute(
                """
                SELECT email, mdp
                FROM accounts
                WHERE blacklisted = FALSE AND in_use = FALSE
                LIMIT 1
                FOR UPDATE;
                """
            )
            account = cur.fetchone()

            if account:
                # Marquer le compte comme utilisé
                cur.execute(
                    """
                    UPDATE accounts
                    SET in_use = TRUE
                    WHERE email = %s;
                    """,
                    (account['email'],)
                )
                print(f"[Database] Compte attribué: {account['email']}")
                return dict(account)
            else:
                print("[Database] Aucun compte disponible")
                return None
    except Exception as e:
        print(f"[Database] Erreur lors de la récupération d'un compte: {e}")
        return None


def get_linked_account(emulator_nom):
    """
    Récupère le compte lié à un émulateur spécifique.

    Args:
        emulator_nom (str): Nom de l'émulateur (ex: "emulator-5554")

    Returns:
        dict: {"email": str, "mdp": str} si un compte est lié
        None: Si aucun compte lié
    """
    try:
        with get_cursor(dictionary=True) as cur:
            cur.execute(
                """
                SELECT email, mdp
                FROM accounts
                WHERE linked_emulator = %s AND blacklisted = FALSE;
                """,
                (emulator_nom,)
            )
            account = cur.fetchone()

            if account:
                #print(f"[Database] Compte lié trouvé pour {emulator_nom}: {account['email']}")
                return dict(account)
            else:
                #print(f"[Database] Aucun compte lié pour {emulator_nom}")
                return None
    except Exception as e:
        print(f"[Database] Erreur lors de la récupération du compte lié: {e}")
        return None


def link_account_to_emulator(email, emulator_nom):
    """
    Lie un compte à un émulateur spécifique.

    Args:
        email (str): Email du compte
        emulator_nom (str): Nom de l'émulateur (ex: "emulator-5554")

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE accounts
                SET linked_emulator = %s, in_use = TRUE
                WHERE email = %s;
                """,
                (emulator_nom, email)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun compte trouvé avec l'email: {email}")
                return False

            cur.connection.commit()
            print(f"[Database] Compte {email} lié à {emulator_nom}")
            return True
    except Exception as e:
        print(f"[Database] Erreur lors du lien compte-émulateur: {e}")
        return False


def unlink_account_from_emulator(emulator_nom):
    """
    Délie le compte d'un émulateur (met linked_emulator à NULL et in_use à FALSE).

    Args:
        emulator_nom (str): Nom de l'émulateur

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE accounts
                SET linked_emulator = NULL, in_use = FALSE
                WHERE linked_emulator = %s;
                """,
                (emulator_nom,)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun compte lié à {emulator_nom}")
                return False

            cur.connection.commit()
            print(f"[Database] Compte délié de {emulator_nom}")
            return True
    except Exception as e:
        print(f"[Database] Erreur lors du délien compte-émulateur: {e}")
        return False


def release_account(email):
    """
    Libère un compte (remet in_use à False).

    Args:
        email (str): Email du compte à libérer

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE accounts
                SET in_use = FALSE
                WHERE email = %s;
                """,
                (email,)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun compte trouvé avec l'email: {email}")
                return False

            cur.connection.commit()
            print(f"[Database] Compte libéré: {email}")
            return True
    except Exception as e:
        print(f"[Database] Erreur lors de la libération du compte {email}: {e}")
        return False


def blacklist_account(email):
    """
    Blackliste un compte (met blacklisted à True et in_use à False).

    Args:
        email (str): Email du compte à blacklister

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE accounts
                SET blacklisted = TRUE, in_use = FALSE
                WHERE email = %s;
                """,
                (email,)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun compte trouvé avec l'email: {email}")
                return False

            cur.connection.commit()
            print(f"[Database] Compte blacklisté: {email}")
            return True
    except Exception as e:
        print(f"[Database] Erreur lors du blacklistage du compte {email}: {e}")
        return False


def add_account(email, mdp):
    """
    Ajoute un nouveau compte Google dans la base.

    Args:
        email (str): Email du compte
        mdp (str): Mot de passe du compte

    Returns:
        bool: True si ajout réussi, False si le compte existait déjà
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO accounts (email, mdp, blacklisted, in_use)
                VALUES (%s, %s, FALSE, FALSE)
                ON CONFLICT (email) DO NOTHING
                RETURNING email;
                """,
                (email, mdp)
            )
            result = cur.fetchone()

            if result:
                cur.connection.commit()
                print(f"[Database] Compte ajouté: {email}")
                return True
            else:
                print(f"[Database] Le compte {email} existe déjà")
                return False
    except Exception as e:
        print(f"[Database] Erreur lors de l'ajout du compte {email}: {e}")
        return False


def release_all_accounts():
    """
    Libère tous les comptes en cours d'utilisation (in_use = True).
    Ne modifie PAS le statut blacklisted des comptes.
    Utilisé lors de l'arrêt du script pour nettoyer les comptes.

    Returns:
        int: Nombre de comptes libérés
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE accounts
                SET in_use = FALSE, linked_emulator = NULL
                WHERE in_use = TRUE AND blacklisted = FALSE;
                """
            )
            count = cur.rowcount
            cur.connection.commit()
            if count > 0:
                print(f"[Database] {count} compte(s) libéré(s)")
            return count
    except Exception as e:
        print(f"[Database] Erreur lors de la libération des comptes: {e}")
        return 0


# ==========================================================
# ================= GESTION DES TOKENS =====================
# ==========================================================

def add_tokens_to_package(package_name, tokens):
    """
    Ajoute des tokens au compteur d'un package.

    Args:
        package_name (str): Nom du package
        tokens (int): Nombre de tokens à ajouter

    Returns:
        bool: True si succès, False sinon
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE Packages
                SET tokens = COALESCE(tokens, 0) + %s
                WHERE PackageName = %s;
                """,
                (tokens, package_name)
            )

            if cur.rowcount == 0:
                print(f"[Database] Aucun package trouvé avec le nom: {package_name}")
                return False

            return True
    except Exception as e:
        print(f"[Database] Erreur lors de l'ajout des tokens pour {package_name}: {e}")
        return False


# ==========================================================
# ============= GESTION DES APPLICATIONS_PROCESS ===========
# ==========================================================

def get_next_unprocessed_application():
    """
    Récupère une application dont le status est 'unprocessed' et la met à 'processing'.
    Vérifie également que email_use = true dans applications_process_data_managment.
    Renvoie un dict avec package_id et name, ou None si aucune n'est disponible.
    """
    try:
        with get_cursor(dictionary=True) as cur:
            # Récupérer une application non traitée avec email_use = true

            cur.execute(
                """
                SELECT p.package_id, p.name
                FROM applications_process p
                JOIN applications_process_data_managment e
                  ON p.package_id = e.package_id
                WHERE p.status = 'unprocessed'
                AND p.installs::BIGINT = 10000000
                AND e.email_use = true
                ORDER BY p.installs::BIGINT DESC
                LIMIT 1
                FOR UPDATE OF p;
                """
            )
            row = cur.fetchone()
            if row:
                package_id = row['package_id']
                # Mettre à jour le statut à 'processing'
                cur.execute(
                    "UPDATE applications_process SET status = 'processing' WHERE package_id = %s;",
                    (package_id,)
                )
                return dict(row)
            return None
    except Exception as e:
        print(f"Erreur lors de la récupération de l'application non traitée: {e}")
        return None


def update_application_status(package_id, new_status):
    """
    Modifie le status d'une application dans la table applications_process.

    Args:
        package_id (str): Package ID de l'application
        new_status (str): Nouveau status (unprocessed, processing, processed, error)

    Returns:
        bool: True si le status a été modifié, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                "UPDATE applications_process SET status = %s WHERE package_id = %s AND status <> %s;",
                (new_status, package_id, new_status)
            )
            if cur.rowcount > 0:
                return True
            else:
                print(f"Aucune modification pour l'application '{package_id}' (inexistante ou déjà '{new_status}').")
                return False
    except Exception as e:
        print(f"Erreur lors de la mise à jour du status de l'application: {e}")
        return False


def add_application_process(package_id, name, status='unprocessed'):
    """
    Ajoute une nouvelle application dans la table applications_process.

    Args:
        package_id (str): Package ID de l'application (ex: "com.whatsapp")
        name (str): Nom de l'application
        status (str): Status initial (par défaut 'unprocessed')

    Returns:
        bool: True si l'application a été ajoutée, False si elle existait déjà
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO applications_process (package_id, name, status)
                VALUES (%s, %s, %s)
                ON CONFLICT (package_id) DO NOTHING
                RETURNING package_id;
                """,
                (package_id, name, status)
            )
            result = cur.fetchone()
            if result:
                return True
            else:
                print(f"Application '{package_id}' existe déjà.")
                return False
    except Exception as e:
        print(f"Erreur lors de l'ajout de l'application: {e}")
        return None


# ==========================================================
# ============= GESTION PACKAGES_FULL_PIPELINE =============
# ==========================================================

def get_next_package_for_analysis():
    """
    Réclame atomiquement le prochain package à analyser :
    - downloaded = TRUE (APKs disponibles localement)
    - frida_analyze IS NULL (pas encore traité)
    Passe frida_analyze à FALSE (en cours).

    Returns:
        str: package_id si disponible, None sinon
    """
    try:
        with get_cursor(dictionary=True) as cur:
            cur.execute(
                """
                UPDATE packages_full_pipeline
                SET frida_analyze = FALSE
                WHERE package_id = (
                    SELECT package_id
                    FROM packages_full_pipeline
                    WHERE downloaded = TRUE
                      AND error_download IS NULL
                      AND frida_analyze IS NULL
                    ORDER BY id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING package_id;
                """
            )
            row = cur.fetchone()
            return row['package_id'] if row else None
    except Exception as e:
        logger.error(f"Erreur get_next_package_for_analysis: {e}")
        return None


def set_explicit_frida_result(package_id, label):
    """
    Enregistre un libellé lisible dans explicit_frida_result (pour Grafana).

    Args:
        package_id (str): Identifiant du package
        label (str): Libellé humain du résultat

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE packages_full_pipeline
                SET explicit_frida_result = %s
                WHERE package_id = %s;
                """,
                (str(label)[:100], package_id)
            )
        return True
    except Exception as e:
        logger.error(f"Erreur set_explicit_frida_result ({package_id}): {e}")
        return False


def set_frida_error(package_id, error_msg, explicit_result=None):
    """
    Enregistre un message d'erreur dans frida_error et marque frida_analyze = TRUE.

    Args:
        package_id (str): Identifiant du package
        error_msg (str): Message d'erreur concis à stocker
        explicit_result (str|None): Libellé lisible pour Grafana

    Returns:
        bool: True si succès, False sinon
    """
    try:
        # Tronquer à 500 caractères pour éviter les messages trop longs
        error_msg = str(error_msg)[:500]
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE packages_full_pipeline
                SET frida_analyze = TRUE, frida_error = %s, frida_analyze_at = NOW()
                WHERE package_id = %s;
                """,
                (error_msg, package_id)
            )
        if explicit_result is not None:
            set_explicit_frida_result(package_id, explicit_result)
        return True
    except Exception as e:
        logger.error(f"Erreur set_frida_error ({package_id}): {e}")
        return False


def complete_package_analysis(package_id, result=None, explicit_result=None):
    """
    Marque l'analyse d'un package comme terminée : frida_analyze = TRUE.
    Stocke le HAR en JSONB dans result si fourni.

    Args:
        package_id (str): Identifiant du package
        result (dict|None): Données HAR à stocker, ou None si aucun résultat
        explicit_result (str|None): Libellé lisible pour Grafana

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            if result is not None:
                result_json = extras.Json(result) if isinstance(result, dict) else result
                cur.execute(
                    """
                    UPDATE packages_full_pipeline
                    SET frida_analyze = TRUE, result = %s, frida_analyze_at = NOW()
                    WHERE package_id = %s;
                    """,
                    (result_json, package_id)
                )
            else:
                cur.execute(
                    """
                    UPDATE packages_full_pipeline
                    SET frida_analyze = TRUE, frida_analyze_at = NOW()
                    WHERE package_id = %s;
                    """,
                    (package_id,)
                )
        if explicit_result is not None:
            set_explicit_frida_result(package_id, explicit_result)
        return True
    except Exception as e:
        logger.error(f"Erreur complete_package_analysis ({package_id}): {e}")
        return False


def set_request_auto(package_id):
    """
    Marque request_auto = TRUE pour un package dont l'API révèle l'existence d'un email.

    Args:
        package_id (str): Identifiant du package

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE packages_full_pipeline
                SET request_auto = TRUE
                WHERE package_id = %s;
                """,
                (package_id,)
            )
        return True
    except Exception as e:
        logger.error(f"Erreur set_request_auto ({package_id}): {e}")
        return False


def touch_frida_analyze_at(package_id):
    """
    Met à jour frida_analyze_at = NOW() si elle est encore NULL.
    Appel défensif en fin d'analyse pour garantir que la date est toujours renseignée.

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE packages_full_pipeline
                SET frida_analyze_at = NOW()
                WHERE package_id = %s AND frida_analyze_at IS NULL;
                """,
                (package_id,)
            )
        return True
    except Exception as e:
        logger.error(f"Erreur touch_frida_analyze_at ({package_id}): {e}")
        return False


def reset_package_to_pending(package_id):
    """
    Remet frida_analyze à NULL pour qu'un autre worker puisse reprendre le package.
    Utilisé en cas de crash ou de perte de connectivité avant la fin de l'analyse.

    Args:
        package_id (str): Identifiant du package

    Returns:
        bool: True si succès, False sinon
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE packages_full_pipeline
                SET frida_analyze = NULL
                WHERE package_id = %s;
                """,
                (package_id,)
            )
        return True
    except Exception as e:
        logger.error(f"Erreur reset_package_to_pending ({package_id}): {e}")
        return False


def add_package_direct(package_name, app_name, status='unprocessed'):
    """
    Ajoute un package dans la table Packages sans référence à la table Apps.
    Utilisé avec la nouvelle table applications_process.

    Args:
        package_name (str): Nom du package (ex: "com.whatsapp")
        app_name (str): Nom de l'application
        status (str): Status initial (par défaut 'unprocessed')

    Returns:
        bool: True si le package a été ajouté, False si le PackageName existait déjà
    """
    try:
        package_name = str.lower(package_name)
        pipeline = "OK"
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO Packages (PackageName, AppName, Status, Pipeline)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (PackageName) DO NOTHING
                RETURNING PackageName;
                """,
                (package_name, app_name, status, pipeline)
            )
            result = cur.fetchone()
            if result:
                return True
            else:
                print(f"Package '{package_name}' existe déjà.")
                return False

    except Exception as e:
        raise RuntimeError(f"Erreur lors de l'ajout du package: {e}")
