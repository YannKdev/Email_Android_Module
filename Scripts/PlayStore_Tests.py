import subprocess
import time
import adb_utils
import re
import xml.etree.ElementTree as ET
import math


def updateUITree(device_id):
      adb_utils.take_ui_xml(device_id, "temp/"+device_id+"/Download/ui.xml")
      #adb_utils.clean_ui_xml("temp/Download/ui.xml", "temp/Download/ui.json")


if __name__ == "__main__":
    device_id = "emulator-5554"
    updateUITree(device_id=device_id)