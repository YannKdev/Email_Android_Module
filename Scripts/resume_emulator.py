"""
Script pour reprendre un émulateur en pause après inspection humaine.

Usage:
    python resume_emulator.py emulator-5554
"""

import sys
import Database

def resume_emulator(serial):
    """
    Reprend un émulateur en pause en le remettant en état RUNNING.

    Args:
        serial (str): Serial de l'émulateur (ex: "emulator-5554")

    Returns:
        bool: True si succès, False sinon
    """
    # Récupérer l'état actuel
    emulators = Database.get_record("emulators", limit=1000)
    emulator = next((e for e in emulators if e['nom'] == serial), None)

    if not emulator:
        print(f"❌ Émulateur {serial} non trouvé dans la base de données")
        return False

    current_status = emulator['status']

    if not current_status.startswith('PAUSED_'):
        print(f"⚠️ Émulateur {serial} n'est pas en pause (statut actuel: {current_status})")
        return False

    print(f"📋 Émulateur: {serial}")
    print(f"   Statut actuel: {current_status}")
    print(f"\n🔍 Raisons de la pause:")

    if current_status == "PAUSED_UNKNOWN_STATE":
        print("   └─ État du Play Store non reconnu")
        print("      💡 Vérifiez l'UI du Play Store et ajoutez la signature dans STATE_SIGNATURES si nécessaire")
    elif current_status == "PAUSED_ACTION_FAILED":
        print("   └─ Action échouée lors de la gestion du Play Store")
        print("      💡 Vérifiez que l'élément UI est toujours accessible ou que les coordonnées sont correctes")
    elif current_status == "PAUSED_MAX_ATTEMPTS":
        print("   └─ Limite de tentatives atteinte")
        print("      💡 Vérifiez manuellement l'état du Play Store et corrigez le problème")

    print(f"\n❓ Voulez-vous reprendre l'émulateur {serial} ? (o/n): ", end="")
    response = input().strip().lower()

    if response != 'o':
        print("❌ Opération annulée")
        return False

    # Reprendre l'émulateur
    success = Database.update_emulator_status(serial, "RUNNING")

    if success:
        print(f"✅ Émulateur {serial} repris avec succès")
        print(f"   Le worker va reprendre son travail au prochain cycle")
        return True
    else:
        print(f"❌ Erreur lors de la reprise de l'émulateur {serial}")
        return False


def list_paused_emulators():
    """Liste tous les émulateurs en pause"""
    emulators = Database.get_record("emulators", limit=1000)
    paused = [e for e in emulators if e['status'].startswith('PAUSED_')]

    if not paused:
        print("✅ Aucun émulateur en pause")
        return

    print(f"\n⏸️ Émulateurs en pause ({len(paused)}):\n")
    for emu in paused:
        print(f"  📱 {emu['nom']}")
        print(f"     Statut: {emu['status']}")
        print(f"     Type: {emu['type']}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("📋 Liste des émulateurs en pause:")
        list_paused_emulators()
        print("\n💡 Usage: python resume_emulator.py <serial>")
        print("   Exemple: python resume_emulator.py emulator-5554")
        sys.exit(0)

    serial = sys.argv[1]
    resume_emulator(serial)
