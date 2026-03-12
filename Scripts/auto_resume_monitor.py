"""
Script de monitoring automatique pour reprendre les émulateurs en pause
après un certain délai ou sous certaines conditions.

Usage:
    python auto_resume_monitor.py
"""

import time
import Database

# Configuration
CHECK_INTERVAL = 300  # Vérifier toutes les 5 minutes
AUTO_RESUME_AFTER = 3600  # Auto-reprendre après 1h en pause

# Tracking des pauses
pause_timestamps = {}  # serial -> timestamp de la pause


def check_and_auto_resume():
    """Vérifie les émulateurs en pause et les reprend automatiquement si conditions remplies"""

    emulators = Database.get_record("emulators", limit=1000)
    paused = [e for e in emulators if e['status'].startswith('PAUSED_')]

    current_time = time.time()

    for emu in paused:
        serial = emu['nom']
        status = emu['status']

        # Enregistrer le timestamp de la première détection
        if serial not in pause_timestamps:
            pause_timestamps[serial] = current_time
            print(f"📋 [{serial}] Détecté en pause: {status}")
            continue

        # Calculer la durée de la pause
        pause_duration = current_time - pause_timestamps[serial]

        # Conditions de reprise automatique
        should_resume = False
        reason = ""

        # Exemple 1: Reprendre après un certain délai
        if pause_duration > AUTO_RESUME_AFTER:
            should_resume = True
            reason = f"Pause trop longue ({pause_duration/60:.0f} minutes)"

        # Exemple 2: Reprendre automatiquement certains types d'erreur
        # (après investigation humaine et correction du code)
        # if status == "PAUSED_ACTION_FAILED" and pause_duration > 600:
        #     should_resume = True
        #     reason = "Action failed - code corrigé"

        if should_resume:
            print(f"\n🔄 [{serial}] Reprise automatique")
            print(f"   Raison: {reason}")
            print(f"   Statut: {status}")

            success = Database.update_emulator_status(serial, "RUNNING")

            if success:
                print(f"   ✅ Repris avec succès")
                # Retirer du tracking
                del pause_timestamps[serial]
            else:
                print(f"   ❌ Échec de la reprise")
        else:
            remaining = AUTO_RESUME_AFTER - pause_duration
            print(f"⏸️ [{serial}] Toujours en pause: {status} (reprise auto dans {remaining/60:.0f} min)")


def cleanup_tracking():
    """Nettoie le tracking des émulateurs qui ne sont plus en pause"""
    emulators = Database.get_record("emulators", limit=1000)
    current_serials = {e['nom'] for e in emulators}

    # Retirer les émulateurs qui ne sont plus en pause
    to_remove = []
    for serial in pause_timestamps:
        if serial not in current_serials:
            to_remove.append(serial)

    for serial in to_remove:
        del pause_timestamps[serial]


if __name__ == "__main__":
    print("🚀 Démarrage du monitoring automatique des émulateurs en pause")
    print(f"   Intervalle de vérification: {CHECK_INTERVAL}s")
    print(f"   Reprise automatique après: {AUTO_RESUME_AFTER}s\n")

    try:
        while True:
            check_and_auto_resume()
            cleanup_tracking()
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n🛑 Arrêt du monitoring")
