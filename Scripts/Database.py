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

# Connection credentials
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
# Global variable to store the connection
_conn = None  # Global connection

def get_connection(retries=3, delay=2):
    """
    Returns the PostgreSQL connection.
    Automatically reconnects if the connection is closed or failed.
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
            logger.warning(f"Connection lost, attempt {attempt + 1}...")
            _conn = None
            time.sleep(delay)

    raise ConnectionError("Failed to reconnect to PostgreSQL after multiple attempts.")

def close_connection():
    """Closes the connection if it is open."""
    global _conn
    if _conn and not _conn.closed:
        _conn.close()
        _conn = None

@contextmanager
def get_cursor(dictionary=False):
    """
    Context manager to obtain a PostgreSQL cursor.
    Usage:
        with get_cursor(dictionary=True) as cur:
            cur.execute("SELECT * FROM table;")
    Connection and cursor are managed automatically.
    """
    conn = get_connection()
    cur = None
    try:
        if dictionary:
            cur = conn.cursor(cursor_factory=extras.DictCursor)
        else:
            cur = conn.cursor()
        yield cur
        conn.commit()  # automatic commit after the block
    except Exception as e:
        conn.rollback()  # rollback on error
        raise e
    finally:
        if cur:
            cur.close()



def get_record(table_name, limit=5):
    """
    Retrieves the first records from a table.
    Returns a list of dictionaries.
    """
    try:
        with get_cursor(dictionary=True) as cur:
            query = f"SELECT * FROM {table_name} LIMIT %s;"
            cur.execute(query, (limit,))
            rows = cur.fetchall()
            return rows
    except Exception as e:
        logger.error(f"Error in get_record: {e}")
        return []
def truncate_all_tables():
    """
    Empties the results, packages, apps, accounts and emulators tables respecting dependencies (CASCADE).
    Order matters: results/packages/apps depend on packages/apps, accounts depends on emulators.
    """
    try:
        with get_cursor() as cur:
            # Correct order: first tables with references, then referenced ones
            query = "TRUNCATE TABLE results, packages, apps, accounts, emulators CASCADE;"
            cur.execute(query)
            return True
    except Exception as e:
        print(f"Error in truncate_all_tables: {e}")
        return False


def add_app(name):
    """
    Adds a new App with 'unprocessed' status.
    Returns True if the App was added, False if it already existed.
    """
    try:
        # ASCII normalization (é -> e, ù -> u, etc.)
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
                #print(f"App '{name}' added with status 'unprocessed'.")
                return True  # App added
            else:
                print(f"App '{name}' already exists.")
                return False  # App already existed
    except Exception as e:
        print(f"Error adding app: {e}")
        return None

def get_next_unprocessed_app():
    """
    Retrieves an App with 'unprocessed' status and sets it to 'processing'.
    Returns the App name or None if none is available.
    """
    try:
        with get_cursor() as cur:
            # Retrieve an unprocessed App
            cur.execute(
                "SELECT Name FROM Apps WHERE Status = 'unprocessed' LIMIT 1 FOR UPDATE;"
            )
            row = cur.fetchone()
            if row:
                app_name = row[0]
                # Update status to 'processing'
                cur.execute(
                    "UPDATE Apps SET Status = 'processing' WHERE Name = %s;",
                    (app_name,)
                )
                return app_name
            return None
    except Exception as e:
        print(f"Error retrieving unprocessed app: {e}")
        return None

def mark_app_processed(name):
    """
    Sets an App's status to 'processed'.
    Returns True if the status was changed, False otherwise.
    """
    try:
        name = str.lower(name)
        with get_cursor() as cur:
            cur.execute(
                "UPDATE Apps SET Status = 'processed' WHERE Name = %s AND Status <> 'processed';",
                (name,)
            )
            if cur.rowcount > 0:
                #print(f"App '{name}' marked as 'processed'.")
                return True
            else:
                print(f"No change for App '{name}' (does not exist or already 'processed').")
                return False
    except Exception as e:
        print(f"Error updating app status: {e}")
        return False

def add_package(package_name, app_name, original_app, status='unprocessed'):
    """
    Adds a package to the Packages table.
    Returns True if the package was added, False if the PackageName already existed.
    Raises an error if OriginalApp does not exist.
    """
    try:
        package_name = str.lower(package_name)
        original_app = str.lower(original_app)
        pipeline = "OK"
        with get_cursor() as cur:
            # Check that OriginalApp exists
            cur.execute("SELECT Name FROM Apps WHERE Name = %s;", (original_app,))
            if not cur.fetchone():
                raise ValueError(f"OriginalApp '{original_app}' n'existe pas dans Apps.")

            # Insert package with return
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
                #print(f"Package '{package_name}' added successfully.")
                return True
            else:
                print(f"Package '{package_name}' already exists.")
                return False

    except Exception as e:
        raise RuntimeError(f"Error adding package: {e}")

def package_exists(package_name):
    """
    Checks if a package already exists in the Packages table.

    Args:
        package_name: Package name to check

    Returns:
        bool: True if the package exists, False otherwise
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
        print(f"Error checking package: {e}")
        return False

def update_package_status(package_name, new_status):
    """
    Updates a package's status and records the processing datetime.
    Returns True if the status was changed, False if no matching package.
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            # Update only if status differs + processed_at
            cur.execute(
                "UPDATE Packages SET Status = %s, processed_at = NOW() WHERE PackageName = %s AND Status <> %s;",
                (new_status, package_name, new_status)
            )
            if cur.rowcount > 0:
                print(f"Package '{package_name}' updated with status '{new_status}'.")
                return True
            else:
                print(f"No change for package '{package_name}' (does not exist or already '{new_status}').")
                return False
    except Exception as e:
        print(f"Error updating package: {e}")
        return False
    
def update_app_status(app_name, new_status):
    """
    Updates an app's status in the Apps table.
    Returns True if the status was changed, False if no matching app.
    """
    try:
        app_name = str.lower(app_name)
        with get_cursor() as cur:
            # Update only if status differs
            cur.execute(
                "UPDATE Apps SET Status = %s WHERE Name = %s AND Status <> %s;",
                (new_status, app_name, new_status)
            )
            if cur.rowcount > 0:
                #print(f"App '{app_name}' updated with status '{new_status}'.")
                return True
            else:
                print(f"No change for app '{app_name}' (does not exist or already '{new_status}').")
                return False
    except Exception as e:
        print(f"Error updating app: {e}")
        return False
    
def update_package_pipeline(package_name, new_pipeline):
    """
    Updates the pipeline of a package and records the processing datetime.
    Returns True if the pipeline was updated, False if no matching package found.
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            # Update only if status differs + processed_at
            cur.execute(
                "UPDATE Packages SET pipeline = %s, processed_at = NOW() WHERE PackageName = %s AND pipeline <> %s;",
                (new_pipeline, package_name, new_pipeline)
            )
            if cur.rowcount > 0:
                #print(f"Package '{package_name}' updated with pipeline '{new_pipeline}'.")
                return True
            else:
                print(f"No change for package '{package_name}' (does not exist or already '{new_pipeline}').")
                return False
    except Exception as e:
        print(f"Error updating package: {e}")
        return False


def add_result(har_data, package_name):
    """
    Adds a result (HAR) linked to a package.

    Args:
        har_data: HAR dictionary (will be converted to JSONB) or JSON string
        package_name: Associated package name
    """
    try:
        package_name = str.lower(package_name)
        with get_cursor() as cur:
            # Check that the package exists
            cur.execute("SELECT PackageName FROM Packages WHERE PackageName = %s;", (package_name,))
            if not cur.fetchone():
                raise ValueError(f"Package '{package_name}' does not exist.")

            # Convert to Json for JSONB if it's a dict
            if isinstance(har_data, dict):
                har_json = extras.Json(har_data)
            else:
                har_json = har_data

            # Add HAR with scan date
            cur.execute(
                "INSERT INTO Results (HAR, PackageName, date_scan) VALUES (%s, %s, NOW());",
                (har_json, package_name)
            )
            print(f"HAR added for package '{package_name}'.")
    except Exception as e:
        raise RuntimeError(f"Error adding result: {e}")

def get_hars_for_package(package_name):
    """
    Retrieves all HARs associated with a package.
    Returns a list of HAR texts.
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
        print(f"Error retrieving HARs: {e}")
        return []


# ==========================================================
# ================= EMULATOR MANAGEMENT ====================
# ==========================================================

def reset_emulators():
    """
    Fully empties the emulators table (truncate).
    Used at startup to start from a clean state.

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        with get_cursor() as cur:
            cur.execute("TRUNCATE TABLE emulators;")
            cur.connection.commit()
            print("[Database] emulators table reset")
            return True
    except Exception as e:
        print(f"[Database] Error resetting emulators: {e}")
        return False


def add_emulator(nom, type_emu, status="STARTING"):
    """
    Adds an emulator to the emulators table.

    Args:
        nom (str): Emulator name (e.g. "emulator-5554")
        type_emu (str): Emulator type ("Root" or "PS")
        status (str): Initial status (default "STARTING")

    Returns:
        bool: True if successfully added, False otherwise
    """
    try:
        # Validate type
        if type_emu not in ("Root", "PS"):
            print(f"[Database] Invalid emulator type: {type_emu} (must be 'Root' or 'PS')")
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
            print(f"[Database] Emulator added: {nom} ({type_emu}) - Status: {status}")
            return True
    except Exception as e:
        print(f"[Database] Error adding emulator {nom}: {e}")
        return False


def update_emulator_status(nom, new_status):
    """
    Updates the status of an existing emulator.

    Args:
        nom (str): Emulator name (e.g. "emulator-5554")
        new_status (str): New status (e.g. "RUNNING", "OFFLINE", "STARTING")

    Returns:
        bool: True if update succeeded, False otherwise
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
                print(f"[Database] No emulator found with name: {nom}")
                return False

            cur.connection.commit()
            #print(f"[Database] Emulator {nom} status updated: {new_status}")
            return True
    except Exception as e:
        print(f"[Database] Error updating status for {nom}: {e}")
        return False

def increment_emulator_finished(nom):
    """
    Increments the apps_finished counter by 1 after a successful analysis.

    Args:
        nom (str): Emulator name

    Returns:
        bool: True if increment succeeded, False otherwise
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
                print(f"[Database] No emulator found with name: {nom}")
                return False

            cur.connection.commit()
            return True
    except Exception as e:
        print(f"[Database] Error incrementing apps_finished for {nom}: {e}")
        return False


def increment_emulator_error(nom):
    """
    Increments the apps_error counter by 1 after an error.

    Args:
        nom (str): Emulator name

    Returns:
        bool: True if increment succeeded, False otherwise
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
                print(f"[Database] No emulator found with name: {nom}")
                return False

            cur.connection.commit()
            return True
    except Exception as e:
        print(f"[Database] Error incrementing apps_error for {nom}: {e}")
        return False


# ==========================================================
# ================= ACCOUNT MANAGEMENT ====================
# ==========================================================

def get_available_account():
    """
    Retrieves an available Google account (not blacklisted and not in use).
    Automatically marks the account as in_use = True.

    Returns:
        dict: {"email": str, "mdp": str} if an account is available
        None: If no account is available
    """
    try:
        with get_cursor(dictionary=True) as cur:
            # Select and lock an available account
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
                # Mark the account as in use
                cur.execute(
                    """
                    UPDATE accounts
                    SET in_use = TRUE
                    WHERE email = %s;
                    """,
                    (account['email'],)
                )
                print(f"[Database] Account assigned: {account['email']}")
                return dict(account)
            else:
                print("[Database] No account available")
                return None
    except Exception as e:
        print(f"[Database] Error retrieving account: {e}")
        return None


def get_linked_account(emulator_nom):
    """
    Retrieves the account linked to a specific emulator.

    Args:
        emulator_nom (str): Emulator name (e.g. "emulator-5554")

    Returns:
        dict: {"email": str, "mdp": str} if an account is linked
        None: If no account is linked
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
                #print(f"[Database] Linked account found for {emulator_nom}: {account['email']}")
                return dict(account)
            else:
                #print(f"[Database] No linked account for {emulator_nom}")
                return None
    except Exception as e:
        print(f"[Database] Error retrieving linked account: {e}")
        return None


def link_account_to_emulator(email, emulator_nom):
    """
    Links an account to a specific emulator.

    Args:
        email (str): Account email
        emulator_nom (str): Emulator name (e.g. "emulator-5554")

    Returns:
        bool: True if successful, False otherwise
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
                print(f"[Database] No account found with email: {email}")
                return False

            cur.connection.commit()
            print(f"[Database] Account {email} linked to {emulator_nom}")
            return True
    except Exception as e:
        print(f"[Database] Error linking account to emulator: {e}")
        return False


def unlink_account_from_emulator(emulator_nom):
    """
    Unlinks the account from an emulator (sets linked_emulator to NULL and in_use to FALSE).

    Args:
        emulator_nom (str): Emulator name

    Returns:
        bool: True if successful, False otherwise
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
                print(f"[Database] No account linked to {emulator_nom}")
                return False

            cur.connection.commit()
            print(f"[Database] Account unlinked from {emulator_nom}")
            return True
    except Exception as e:
        print(f"[Database] Error unlinking account from emulator: {e}")
        return False


def release_account(email):
    """
    Releases an account (sets in_use back to False).

    Args:
        email (str): Account email to release

    Returns:
        bool: True if successful, False otherwise
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
                print(f"[Database] No account found with email: {email}")
                return False

            cur.connection.commit()
            print(f"[Database] Account released: {email}")
            return True
    except Exception as e:
        print(f"[Database] Error releasing account {email}: {e}")
        return False


def blacklist_account(email):
    """
    Blacklists an account (sets blacklisted to True and in_use to False).

    Args:
        email (str): Account email to blacklist

    Returns:
        bool: True if successful, False otherwise
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
                print(f"[Database] No account found with email: {email}")
                return False

            cur.connection.commit()
            print(f"[Database] Account blacklisted: {email}")
            return True
    except Exception as e:
        print(f"[Database] Error blacklisting account {email}: {e}")
        return False


def add_account(email, mdp):
    """
    Adds a new Google account to the database.

    Args:
        email (str): Account email
        mdp (str): Account password

    Returns:
        bool: True if added successfully, False if account already existed
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
                print(f"[Database] Account added: {email}")
                return True
            else:
                print(f"[Database] Account {email} already exists")
                return False
    except Exception as e:
        print(f"[Database] Error adding account {email}: {e}")
        return False


def release_all_accounts():
    """
    Releases all accounts currently in use (in_use = True).
    Does NOT modify the blacklisted status of accounts.
    Used at script shutdown to clean up accounts.

    Returns:
        int: Number of accounts released
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
                print(f"[Database] {count} account(s) released")
            return count
    except Exception as e:
        print(f"[Database] Error releasing accounts: {e}")
        return 0


# ==========================================================
# ================= TOKEN MANAGEMENT =======================
# ==========================================================

def add_tokens_to_package(package_name, tokens):
    """
    Adds tokens to a package's counter.

    Args:
        package_name (str): Package name
        tokens (int): Number of tokens to add

    Returns:
        bool: True if successful, False otherwise
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
                print(f"[Database] No package found with name: {package_name}")
                return False

            return True
    except Exception as e:
        print(f"[Database] Error adding tokens for {package_name}: {e}")
        return False


# ==========================================================
# ============= APPLICATIONS_PROCESS MANAGEMENT ============
# ==========================================================

def get_next_unprocessed_application():
    """
    Retrieves an application with status 'unprocessed' and sets it to 'processing'.
    Also checks that email_use = true in applications_process_data_managment.
    Returns a dict with package_id and name, or None if none available.
    """
    try:
        with get_cursor(dictionary=True) as cur:
            # Retrieve an unprocessed application with email_use = true

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
                # Update status to 'processing'
                cur.execute(
                    "UPDATE applications_process SET status = 'processing' WHERE package_id = %s;",
                    (package_id,)
                )
                return dict(row)
            return None
    except Exception as e:
        print(f"Error retrieving unprocessed application: {e}")
        return None


def update_application_status(package_id, new_status):
    """
    Updates the status of an application in the applications_process table.

    Args:
        package_id (str): Application package ID
        new_status (str): New status (unprocessed, processing, processed, error)

    Returns:
        bool: True if status was updated, False otherwise
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
                print(f"No change for application '{package_id}' (not found or already '{new_status}').")
                return False
    except Exception as e:
        print(f"Error updating application status: {e}")
        return False


def add_application_process(package_id, name, status='unprocessed'):
    """
    Adds a new application to the applications_process table.

    Args:
        package_id (str): Application package ID (e.g. "com.whatsapp")
        name (str): Application name
        status (str): Initial status (default 'unprocessed')

    Returns:
        bool: True if application was added, False if it already existed
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
                print(f"Application '{package_id}' already exists.")
                return False
    except Exception as e:
        print(f"Error adding application: {e}")
        return None


# ==========================================================
# ============= PACKAGES_FULL_PIPELINE MANAGEMENT ==========
# ==========================================================

def get_next_package_for_analysis():
    """
    Atomically claims the next package to analyze:
    - downloaded = TRUE (APKs available locally)
    - frida_analyze IS NULL (not yet processed)
    Sets frida_analyze to FALSE (in progress).

    Returns:
        str: package_id if available, None otherwise
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
        logger.error(f"Error get_next_package_for_analysis: {e}")
        return None


def set_explicit_frida_result(package_id, label):
    """
    Stores a human-readable label in explicit_frida_result (for Grafana).

    Args:
        package_id (str): Package identifier
        label (str): Human-readable result label

    Returns:
        bool: True if successful, False otherwise
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
        logger.error(f"Error set_explicit_frida_result ({package_id}): {e}")
        return False


def set_frida_error(package_id, error_msg, explicit_result=None):
    """
    Records an error message in frida_error and marks frida_analyze = TRUE.

    Args:
        package_id (str): Package identifier
        error_msg (str): Concise error message to store
        explicit_result (str|None): Human-readable label for Grafana

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Truncate to 500 characters to avoid excessively long messages
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
        logger.error(f"Error set_frida_error ({package_id}): {e}")
        return False


def complete_package_analysis(package_id, result=None, explicit_result=None):
    """
    Marks a package analysis as complete: frida_analyze = TRUE.
    Stores the HAR as JSONB in result if provided.

    Args:
        package_id (str): Package identifier
        result (dict|None): HAR data to store, or None if no result
        explicit_result (str|None): Human-readable label for Grafana

    Returns:
        bool: True if successful, False otherwise
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
        logger.error(f"Error complete_package_analysis ({package_id}): {e}")
        return False


def touch_frida_analyze_at(package_id):
    """
    Updates frida_analyze_at = NOW() if it is still NULL.
    Defensive call at end of analysis to ensure the timestamp is always set.

    Returns:
        bool: True if successful, False otherwise
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
        logger.error(f"Error touch_frida_analyze_at ({package_id}): {e}")
        return False


def reset_package_to_pending(package_id):
    """
    Resets frida_analyze to NULL so another worker can pick up the package.
    Used in case of crash or connectivity loss before the analysis completes.

    Args:
        package_id (str): Package identifier

    Returns:
        bool: True if successful, False otherwise
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
        logger.error(f"Error reset_package_to_pending ({package_id}): {e}")
        return False


def add_package_direct(package_name, app_name, status='unprocessed'):
    """
    Adds a package to the Packages table without referencing the Apps table.
    Used with the new applications_process table.

    Args:
        package_name (str): Package name (e.g. "com.whatsapp")
        app_name (str): Application name
        status (str): Initial status (default 'unprocessed')

    Returns:
        bool: True if package was added, False if PackageName already existed
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
                print(f"Package '{package_name}' already exists.")
                return False

    except Exception as e:
        raise RuntimeError(f"Error adding package: {e}")
