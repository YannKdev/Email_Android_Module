# accounts_manager.py
import subprocess
import time
import logging
import Database
import adb_utils

logger = logging.getLogger(__name__)


def wipe_emulator(device_id):
    """
    Wipes all emulator data and restarts it.

    Args:
        device_id (str): Emulator ID (e.g. "emulator-5554")

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        logger.info(f"[AccountsManager] Starting wipe for {device_id}")

        # Update the status in the database
        Database.update_emulator_status(device_id, "WIPING_DATA")

        # Execute the adb shell wipe data command
        logger.info(f"[AccountsManager] Wiping data for {device_id}...")
        result = subprocess.run(
            ["adb", "-s", device_id, "shell", "recovery", "--wipe_data"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.warning(f"[AccountsManager] Error during wipe: {result.stderr}")
            # Alternative attempt with reboot recovery
            logger.info(f"[AccountsManager] Alternative attempt with reboot recovery...")
            subprocess.run(
                ["adb", "-s", device_id, "reboot", "recovery"],
                timeout=10
            )

        # Wait for the wipe to complete
        logger.info(f"[AccountsManager] Waiting for wipe to finish (30 seconds)...")
        time.sleep(30)

        # Restart the emulator
        logger.info(f"[AccountsManager] Restarting {device_id}...")
        subprocess.run(
            ["adb", "-s", device_id, "reboot"],
            timeout=10
        )

        # Wait for the restart
        logger.info(f"[AccountsManager] Waiting for restart (60 seconds)...")
        time.sleep(60)

        # Update the status
        Database.update_emulator_status(device_id, "STARTING")
        logger.info(f"[AccountsManager] Wipe and restart complete for {device_id}")

        return True

    except subprocess.TimeoutExpired:
        logger.info(f"[AccountsManager] Timeout during wipe of {device_id}")
        Database.update_emulator_status(device_id, "ERROR_WIPE_TIMEOUT")
        return False
    except Exception as e:
        logger.warning(f"[AccountsManager] Error during wipe of {device_id}: {e}")
        Database.update_emulator_status(device_id, "ERROR_WIPE")
        return False


def assign_account_to_emulator(device_id):
    """
    Assigns an available Google account to an emulator and creates the link.
    If an account is already linked, returns it directly.

    Args:
        device_id (str): Emulator ID (e.g. "emulator-5554")

    Returns:
        dict: {"email": str, "mdp": str} if an account is available
        None: If no account is available
    """
    try:
        logger.info(f"[AccountsManager] Assigning an account to {device_id}")

        # 1. Check whether an account is already linked
        account = Database.get_linked_account(device_id)
        if account:
            logger.info(f"[AccountsManager] Account already linked: {account['email']}")
            return account

        # 2. Otherwise, retrieve an available account
        account = Database.get_available_account()

        if account:
            # 3. Create the link with the emulator
            if Database.link_account_to_emulator(account['email'], device_id):
                logger.info(f"[AccountsManager] Account {account['email']} assigned and linked to {device_id}")
                return account
            else:
                logger.warning(f"[AccountsManager] Error linking account to {device_id}")
                Database.release_account(account['email'])
                return None
        else:
            logger.warning(f"[AccountsManager] No account available for {device_id}")
            return None

    except Exception as e:
        logger.warning(f"[AccountsManager] Error assigning account to {device_id}: {e}")
        return None


def get_emulator_account(device_id):
    """
    Retrieves the account currently linked to an emulator.

    Args:
        device_id (str): Emulator ID (e.g. "emulator-5554")

    Returns:
        dict: {"email": str, "mdp": str} if an account is linked
        None: If no account is linked
    """
    try:
        return Database.get_linked_account(device_id)
    except Exception as e:
        logger.warning(f"[AccountsManager] Error retrieving account for {device_id}: {e}")
        return None


def release_emulator_account(email):
    """
    Releases the account used by an emulator.

    Args:
        email (str): Email of the account to release

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        logger.info(f"[AccountsManager] Releasing account {email}")
        return Database.release_account(email)
    except Exception as e:
        logger.warning(f"[AccountsManager] Error releasing account {email}: {e}")
        return False


def blacklist_emulator_account(email):
    """
    Blacklists an account (typically after a failure or ban).

    Args:
        email (str): Email of the account to blacklist

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        logger.info(f"[AccountsManager] Blacklisting account {email}")
        return Database.blacklist_account(email)
    except Exception as e:
        logger.warning(f"[AccountsManager] Error blacklisting account {email}: {e}")
        return False