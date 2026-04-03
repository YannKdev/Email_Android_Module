"""
cleanup_apks.py
Iterates over folders in PACKAGES_BASE_PATH and deletes those whose package
is marked frida_analyze = TRUE in the database.

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
    """rmtree handler: forces write permissions before retrying."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except Exception as e:
        logger.error(f"Unable to delete {path} even after chmod: {e}")


def is_frida_analyzed(package_id: str) -> bool:
    """Returns True if frida_analyze = TRUE for this package in the database."""
    try:
        with Database.get_cursor() as cur:
            cur.execute(
                "SELECT 1 FROM packages_full_pipeline WHERE package_id = %s AND frida_analyze = TRUE;",
                (package_id,)
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"DB error for {package_id}: {e}")
        return False


def cleanup(apks_base_path: str, dry_run: bool = False):
    if not os.path.isdir(apks_base_path):
        logger.error(f"Folder not found: {apks_base_path}")
        return

    folders = [
        entry.name for entry in os.scandir(apks_base_path)
        if entry.is_dir()
    ]

    if not folders:
        logger.info("No folder found.")
        return

    logger.info(f"{len(folders)} folder(s) found in {apks_base_path}")
    deleted = 0
    kept = 0
    errors = 0

    for package_id in folders:
        if not is_frida_analyzed(package_id):
            kept += 1
            continue

        folder = os.path.join(apks_base_path, package_id)
        if dry_run:
            logger.info(f"[DRY-RUN] Would delete: {folder}")
            deleted += 1
            continue

        try:
            shutil.rmtree(folder, onerror=_force_remove)
            logger.info(f"Deleted: {folder}")
            deleted += 1
        except Exception as e:
            logger.error(f"Deletion error {folder}: {e}")
            errors += 1

    logger.info(
        f"Done — deleted: {deleted}, kept: {kept}, errors: {errors}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cleans up APKs of analysed packages.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulates deletion without actually removing anything"
    )
    parser.add_argument(
        "--path", default=None,
        help=f"Root path of APKs (default: {PACKAGES_BASE_PATH})"
    )
    args = parser.parse_args()

    base_path = args.path or PACKAGES_BASE_PATH
    logger.info(f"Dossier APKs : {base_path}")
    if args.dry_run:
        logger.info("DRY-RUN mode enabled")

    cleanup(base_path, dry_run=args.dry_run)
