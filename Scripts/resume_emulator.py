"""
Script to resume a paused emulator after human inspection.

Usage:
    python resume_emulator.py emulator-5554
"""

import sys
import Database

def resume_emulator(serial):
    """
    Resumes a paused emulator by putting it back into RUNNING state.

    Args:
        serial (str): Emulator serial (e.g. "emulator-5554")

    Returns:
        bool: True if successful, False otherwise
    """
    # Retrieve the current state
    emulators = Database.get_record("emulators", limit=1000)
    emulator = next((e for e in emulators if e['nom'] == serial), None)

    if not emulator:
        print(f"❌ Emulator {serial} not found in the database")
        return False

    current_status = emulator['status']

    if not current_status.startswith('PAUSED_'):
        print(f"⚠️ Emulator {serial} is not paused (current status: {current_status})")
        return False

    print(f"📋 Emulator: {serial}")
    print(f"   Current status: {current_status}")
    print(f"\n🔍 Pause reasons:")

    if current_status == "PAUSED_UNKNOWN_STATE":
        print("   └─ Play Store state not recognised")
        print("      💡 Check the Play Store UI and add the signature to STATE_SIGNATURES if needed")
    elif current_status == "PAUSED_ACTION_FAILED":
        print("   └─ Action failed during Play Store management")
        print("      💡 Check that the UI element is still accessible or that the coordinates are correct")
    elif current_status == "PAUSED_MAX_ATTEMPTS":
        print("   └─ Attempt limit reached")
        print("      💡 Manually check the Play Store state and fix the problem")

    print(f"\n❓ Do you want to resume emulator {serial}? (y/n): ", end="")
    response = input().strip().lower()

    if response != 'y':
        print("❌ Operation cancelled")
        return False

    # Resume the emulator
    success = Database.update_emulator_status(serial, "RUNNING")

    if success:
        print(f"✅ Emulator {serial} resumed successfully")
        print(f"   The worker will resume its work on the next cycle")
        return True
    else:
        print(f"❌ Error resuming emulator {serial}")
        return False


def list_paused_emulators():
    """Lists all paused emulators"""
    emulators = Database.get_record("emulators", limit=1000)
    paused = [e for e in emulators if e['status'].startswith('PAUSED_')]

    if not paused:
        print("✅ No paused emulators")
        return

    print(f"\n⏸️ Paused emulators ({len(paused)}):\n")
    for emu in paused:
        print(f"  📱 {emu['nom']}")
        print(f"     Statut: {emu['status']}")
        print(f"     Type: {emu['type']}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("📋 List of paused emulators:")
        list_paused_emulators()
        print("\n💡 Usage: python resume_emulator.py <serial>")
        print("   Exemple: python resume_emulator.py emulator-5554")
        sys.exit(0)

    serial = sys.argv[1]
    resume_emulator(serial)
