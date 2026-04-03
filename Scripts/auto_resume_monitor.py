"""
Automatic monitoring script to resume paused emulators
after a certain delay or under certain conditions.

Usage:
    python auto_resume_monitor.py
"""

import time
import Database

# Configuration
CHECK_INTERVAL = 300  # Check every 5 minutes
AUTO_RESUME_AFTER = 3600  # Auto-resume after 1h paused

# Tracking des pauses
pause_timestamps = {}  # serial -> pause timestamp


def check_and_auto_resume():
    """Checks paused emulators and resumes them automatically if conditions are met"""

    emulators = Database.get_record("emulators", limit=1000)
    paused = [e for e in emulators if e['status'].startswith('PAUSED_')]

    current_time = time.time()

    for emu in paused:
        serial = emu['nom']
        status = emu['status']

        # Record the timestamp of the first detection
        if serial not in pause_timestamps:
            pause_timestamps[serial] = current_time
            print(f"📋 [{serial}] Detected as paused: {status}")
            continue

        # Calculate the pause duration
        pause_duration = current_time - pause_timestamps[serial]

        # Automatic resume conditions
        should_resume = False
        reason = ""

        # Example 1: Resume after a certain delay
        if pause_duration > AUTO_RESUME_AFTER:
            should_resume = True
            reason = f"Pause too long ({pause_duration/60:.0f} minutes)"

        # Example 2: Automatically resume certain error types
        # (after human investigation and code fix)
        # if status == "PAUSED_ACTION_FAILED" and pause_duration > 600:
        #     should_resume = True
        #     reason = "Action failed - code fixed"

        if should_resume:
            print(f"\n🔄 [{serial}] Reprise automatique")
            print(f"   Raison: {reason}")
            print(f"   Statut: {status}")

            success = Database.update_emulator_status(serial, "RUNNING")

            if success:
                print(f"   ✅ Resumed successfully")
                # Remove from tracking
                del pause_timestamps[serial]
            else:
                print(f"   ❌ Resume failed")
        else:
            remaining = AUTO_RESUME_AFTER - pause_duration
            print(f"⏸️ [{serial}] Still paused: {status} (auto-resume in {remaining/60:.0f} min)")


def cleanup_tracking():
    """Cleans up tracking for emulators that are no longer paused"""
    emulators = Database.get_record("emulators", limit=1000)
    current_serials = {e['nom'] for e in emulators}

    # Remove emulators that are no longer paused
    to_remove = []
    for serial in pause_timestamps:
        if serial not in current_serials:
            to_remove.append(serial)

    for serial in to_remove:
        del pause_timestamps[serial]


if __name__ == "__main__":
    print("🚀 Starting automatic monitoring of paused emulators")
    print(f"   Check interval: {CHECK_INTERVAL}s")
    print(f"   Auto-resume after: {AUTO_RESUME_AFTER}s\n")

    try:
        while True:
            check_and_auto_resume()
            cleanup_tracking()
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n🛑 Stopping monitoring")
