# accounts_manager.py
import subprocess
import time
import logging
import Database
import adb_utils

logger = logging.getLogger(__name__)


def wipe_emulator(device_id):
    """
    Efface toutes les données de l'émulateur et le redémarre.

    Args:
        device_id (str): ID de l'émulateur (ex: "emulator-5554")

    Returns:
        bool: True si succès, False sinon
    """
    try:
        logger.info(f"[AccountsManager] Démarrage du wipe pour {device_id}")

        # Mettre à jour le statut dans la base de données
        Database.update_emulator_status(device_id, "WIPING_DATA")

        # Exécuter la commande adb shell wipe data
        logger.info(f"[AccountsManager] Effacement des données de {device_id}...")
        result = subprocess.run(
            ["adb", "-s", device_id, "shell", "recovery", "--wipe_data"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.warning(f"[AccountsManager] Erreur lors du wipe: {result.stderr}")
            # Tentative alternative avec reboot recovery
            logger.info(f"[AccountsManager] Tentative alternative avec reboot recovery...")
            subprocess.run(
                ["adb", "-s", device_id, "reboot", "recovery"],
                timeout=10
            )

        # Attendre que le wipe soit effectué
        logger.info(f"[AccountsManager] Attente de la fin du wipe (30 secondes)...")
        time.sleep(30)

        # Redémarrer l'émulateur
        logger.info(f"[AccountsManager] Redémarrage de {device_id}...")
        subprocess.run(
            ["adb", "-s", device_id, "reboot"],
            timeout=10
        )

        # Attendre le redémarrage
        logger.info(f"[AccountsManager] Attente du redémarrage (60 secondes)...")
        time.sleep(60)

        # Mettre à jour le statut
        Database.update_emulator_status(device_id, "STARTING")
        logger.info(f"[AccountsManager] Wipe et redémarrage terminés pour {device_id}")

        return True

    except subprocess.TimeoutExpired:
        logger.info(f"[AccountsManager] Timeout lors du wipe de {device_id}")
        Database.update_emulator_status(device_id, "ERROR_WIPE_TIMEOUT")
        return False
    except Exception as e:
        logger.warning(f"[AccountsManager] Erreur lors du wipe de {device_id}: {e}")
        Database.update_emulator_status(device_id, "ERROR_WIPE")
        return False


def assign_account_to_emulator(device_id):
    """
    Attribue un compte Google disponible à un émulateur et crée le lien.
    Si un compte est déjà lié, le retourne directement.

    Args:
        device_id (str): ID de l'émulateur (ex: "emulator-5554")

    Returns:
        dict: {"email": str, "mdp": str} si un compte est disponible
        None: Si aucun compte disponible
    """
    try:
        logger.info(f"[AccountsManager] Attribution d'un compte à {device_id}")

        # 1. Vérifier si un compte est déjà lié
        account = Database.get_linked_account(device_id)
        if account:
            logger.info(f"[AccountsManager] Compte déjà lié: {account['email']}")
            return account

        # 2. Sinon, récupérer un compte disponible
        account = Database.get_available_account()

        if account:
            # 3. Créer le lien avec l'émulateur
            if Database.link_account_to_emulator(account['email'], device_id):
                logger.info(f"[AccountsManager] Compte {account['email']} attribué et lié à {device_id}")
                return account
            else:
                logger.warning(f"[AccountsManager] Erreur lors du lien du compte à {device_id}")
                Database.release_account(account['email'])
                return None
        else:
            logger.warning(f"[AccountsManager] Aucun compte disponible pour {device_id}")
            return None

    except Exception as e:
        logger.warning(f"[AccountsManager] Erreur lors de l'attribution du compte à {device_id}: {e}")
        return None


def get_emulator_account(device_id):
    """
    Récupère le compte actuellement lié à un émulateur.

    Args:
        device_id (str): ID de l'émulateur (ex: "emulator-5554")

    Returns:
        dict: {"email": str, "mdp": str} si un compte est lié
        None: Si aucun compte lié
    """
    try:
        return Database.get_linked_account(device_id)
    except Exception as e:
        logger.warning(f"[AccountsManager] Erreur lors de la récupération du compte pour {device_id}: {e}")
        return None


def release_emulator_account(email):
    """
    Libère le compte utilisé par un émulateur.

    Args:
        email (str): Email du compte à libérer

    Returns:
        bool: True si succès, False sinon
    """
    try:
        logger.info(f"[AccountsManager] Libération du compte {email}")
        return Database.release_account(email)
    except Exception as e:
        logger.warning(f"[AccountsManager] Erreur lors de la libération du compte {email}: {e}")
        return False


def blacklist_emulator_account(email):
    """
    Blackliste un compte (généralement après un échec ou ban).

    Args:
        email (str): Email du compte à blacklister

    Returns:
        bool: True si succès, False sinon
    """
    try:
        logger.info(f"[AccountsManager] Blacklistage du compte {email}")
        return Database.blacklist_account(email)
    except Exception as e:
        logger.warning(f"[AccountsManager] Erreur lors du blacklistage du compte {email}: {e}")
        return False