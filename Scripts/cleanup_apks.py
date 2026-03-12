"""
cleanup_apks.py
Parcourt les dossiers dans PACKAGES_BASE_PATH et supprime ceux dont le package
est marqué frida_analyze = TRUE en base.

Usage:
    python Scripts/cleanup_apks.py
    python Scripts/cleanup_apks.py --dry-run
    python Scripts/cleanup_apks.py --path /custom/apks/path
"""
import os
import sys
import stat
import shutil
import argparse
import logging

sys.path.insert(0, os.path.dirname(__file__))

import Database
from config import PACKAGES_BASE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _force_remove(func, path, _):
    """Handler rmtree : force les permissions en écriture avant de réessayer."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except Exception as e:
        logger.error(f"Impossible de supprimer {path} même après chmod: {e}")


def is_frida_analyzed(package_id: str) -> bool:
    """Retourne True si frida_analyze = TRUE pour ce package en base."""
    try:
        with Database.get_cursor() as cur:
            cur.execute(
                "SELECT 1 FROM packages_full_pipeline WHERE package_id = %s AND frida_analyze = TRUE;",
                (package_id,)
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Erreur DB pour {package_id}: {e}")
        return False


def cleanup(apks_base_path: str, dry_run: bool = False):
    if not os.path.isdir(apks_base_path):
        logger.error(f"Dossier introuvable : {apks_base_path}")
        return

    folders = [
        entry.name for entry in os.scandir(apks_base_path)
        if entry.is_dir()
    ]

    if not folders:
        logger.info("Aucun dossier trouvé.")
        return

    logger.info(f"{len(folders)} dossier(s) trouvé(s) dans {apks_base_path}")
    deleted = 0
    kept = 0
    errors = 0

    for package_id in folders:
        if not is_frida_analyzed(package_id):
            kept += 1
            continue

        folder = os.path.join(apks_base_path, package_id)
        if dry_run:
            logger.info(f"[DRY-RUN] Supprimerait : {folder}")
            deleted += 1
            continue

        try:
            shutil.rmtree(folder, onerror=_force_remove)
            logger.info(f"Supprimé : {folder}")
            deleted += 1
        except Exception as e:
            logger.error(f"Erreur suppression {folder}: {e}")
            errors += 1

    logger.info(
        f"Terminé — supprimés: {deleted}, conservés: {kept}, erreurs: {errors}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nettoie les APKs des packages analysés.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simule la suppression sans rien effacer"
    )
    parser.add_argument(
        "--path", default=None,
        help=f"Chemin racine des APKs (défaut: {PACKAGES_BASE_PATH})"
    )
    args = parser.parse_args()

    base_path = args.path or PACKAGES_BASE_PATH
    logger.info(f"Dossier APKs : {base_path}")
    if args.dry_run:
        logger.info("Mode DRY-RUN activé")

    cleanup(base_path, dry_run=args.dry_run)
