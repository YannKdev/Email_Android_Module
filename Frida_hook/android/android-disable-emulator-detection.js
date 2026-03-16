/**
 * Emulator Detection Bypass
 *
 * Complements android-disable-root-detection.js (httptoolkit) — no overlap.
 * Does NOT touch: Build.FINGERPRINT, ro.secure, ro.debuggable, ro.build.tags,
 *                 ro.build.type, Runtime.exec, PackageManager, ProcessBuilder
 *                 (all already handled by httptoolkit hooks).
 */

Java.perform(function () {

    // -------------------------------------------------------------------------
    // 1. Emulator-specific files to hide
    // -------------------------------------------------------------------------
    var EMULATOR_FILES = [
        "/dev/qemu_pipe",
        "/dev/socket/qemud",
        "/sys/qemu_trace",
        "/system/bin/qemu-props",
        "/system/lib/libc_malloc_debug_qemu.so",
    ];

    // -------------------------------------------------------------------------
    // 2. Build.* fields — only the ones NOT set by httptoolkit
    //    (FINGERPRINT is intentionally excluded — handled by root-detection hook)
    // -------------------------------------------------------------------------
    try {
        var Build = Java.use("android.os.Build");
        Build.DEVICE.value          = "starlte";
        Build.MANUFACTURER.value    = "samsung";
        Build.BRAND.value           = "samsung";
        Build.MODEL.value           = "SM-G960F";
        Build.HARDWARE.value        = "samsungexynos9810";
        Build.PRODUCT.value         = "starltexx";
        Build.SERIAL.value          = "R28M30ABCDE";
        Build.SUPPORTED_ABIS.value  = ["arm64-v8a", "armeabi-v7a", "armeabi"];
        Build.CPU_ABI.value         = "arm64-v8a";
        Build.CPU_ABI2.value        = "armeabi-v7a";
    } catch (e) {
        console.log("[emulator-bypass] Build fields: " + e);
    }

    // -------------------------------------------------------------------------
    // 3. SystemProperties — emulator-specific keys only
    // -------------------------------------------------------------------------
    try {
        var EMULATOR_PROPS = {
            "ro.kernel.qemu":           "0",
            "ro.product.brand":         "samsung",
            "ro.product.manufacturer":  "samsung",
            "ro.product.model":         "SM-G960F",
            "ro.product.device":        "starlte",
            "ro.hardware":              "samsungexynos9810",
            "ro.product.name":          "starltexx",
            "ro.serialno":              "R28M30ABCDE",
        };

        var SystemProperties = Java.use("android.os.SystemProperties");
        var get = SystemProperties.get.overload("java.lang.String");
        get.implementation = function (name) {
            if (Object.prototype.hasOwnProperty.call(EMULATOR_PROPS, name)) {
                return EMULATOR_PROPS[name];
            }
            return get.call(this, name);
        };
    } catch (e) {
        console.log("[emulator-bypass] SystemProperties: " + e);
    }

    // -------------------------------------------------------------------------
    // 4. File.exists() — hide emulator-specific files
    // -------------------------------------------------------------------------
    try {
        var NativeFile = Java.use("java.io.File");
        var _exists = NativeFile.exists.implementation;
        NativeFile.exists.implementation = function () {
            var path = NativeFile.getAbsolutePath.call(this);
            if (EMULATOR_FILES.indexOf(path) !== -1) {
                return false;
            }
            return this.exists.call(this);
        };
    } catch (e) {
        console.log("[emulator-bypass] File.exists: " + e);
    }

    // -------------------------------------------------------------------------
    // 5. Native fopen — hide emulator-specific files
    // -------------------------------------------------------------------------
    try {
        Interceptor.attach(Module.findExportByName("libc.so", "fopen"), {
            onEnter: function (args) {
                var path = Memory.readCString(args[0]);
                if (EMULATOR_FILES.indexOf(path) !== -1) {
                    Memory.writeUtf8String(args[0], "/notexists");
                }
            }
        });
    } catch (e) {
        console.log("[emulator-bypass] fopen: " + e);
    }

    // -------------------------------------------------------------------------
    // 6. BufferedReader.readLine — strip lines mentioning emulator keywords
    // -------------------------------------------------------------------------
    try {
        var EMULATOR_WORDS = ["goldfish", "ranchu", "qemu", "vbox", "generic_x86"];
        var BufferedReader = Java.use("java.io.BufferedReader");
        BufferedReader.readLine.overload("boolean").implementation = function () {
            var line = this.readLine.overload("boolean").call(this);
            if (line !== null) {
                for (var i = 0; i < EMULATOR_WORDS.length; i++) {
                    if (line.indexOf(EMULATOR_WORDS[i]) !== -1) {
                        return "";
                    }
                }
            }
            return line;
        };
    } catch (e) {
        console.log("[emulator-bypass] BufferedReader: " + e);
    }

    // -------------------------------------------------------------------------
    // 7. TelephonyManager — IMEI / opérateur / SIM
    // -------------------------------------------------------------------------
    try {
        var TM = Java.use("android.telephony.TelephonyManager");

        TM.getDeviceId.overload().implementation = function () {
            return "359872070123456";
        };

        try {
            TM.getImei.overload().implementation = function () {
                return "359872070123456";
            };
        } catch (_) {}

        TM.getSubscriberId.overload().implementation = function () {
            return "310260000000000";
        };

        TM.getNetworkOperatorName.overload().implementation = function () {
            return "T-Mobile";
        };

        TM.getSimOperatorName.overload().implementation = function () {
            return "T-Mobile";
        };

        TM.getPhoneType.overload().implementation = function () {
            return this.PHONE_TYPE_GSM.value;
        };

        TM.getNetworkCountryIso.overload().implementation = function () {
            return "us";
        };

        TM.getSimCountryIso.overload().implementation = function () {
            return "us";
        };
    } catch (e) {
        console.log("[emulator-bypass] TelephonyManager: " + e);
    }

    // -------------------------------------------------------------------------
    // 8. Settings.Secure — ANDROID_ID réaliste
    // -------------------------------------------------------------------------
    try {
        var Secure = Java.use("android.provider.Settings$Secure");
        Secure.getString.overload(
            "android.content.ContentResolver", "java.lang.String"
        ).implementation = function (resolver, name) {
            if (name === Secure.ANDROID_ID.value) {
                return "9774d56d682e549c";
            }
            return this.getString(resolver, name);
        };
    } catch (e) {
        console.log("[emulator-bypass] Settings.Secure: " + e);
    }

});
