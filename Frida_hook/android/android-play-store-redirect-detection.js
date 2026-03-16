/**
 * Détecte les redirections vers le Play Store avant qu'elles n'ouvrent l'app.
 * Émet [FRIDA_PLAY_STORE_REQUIRED] pour signaler le pipeline Python.
 *
 * Points d'accroche :
 *   1. Uri.parse()       — capture les URLs market:// et play.google.com
 *   2. Intent.setPackage — capture les intents ciblant com.android.vending
 *   3. startActivity()   — capture le lancement effectif vers le Play Store
 */
Java.perform(() => {

    // -------------------------------------------------------------------------
    // Hook 1 : Uri.parse — patterns market:// et play.google.com
    // -------------------------------------------------------------------------
    try {
        const Uri = Java.use("android.net.Uri");
        const uriParse = Uri.parse.overload("java.lang.String");
        uriParse.implementation = function(str) {
            if (str && (str.startsWith("market://") || str.includes("play.google.com"))) {
                console.log("[FRIDA_PLAY_STORE_REQUIRED] Uri.parse: " + str);
            }
            return uriParse.call(this, str);
        };
    } catch (e) {
        console.log("[PlayStoreDetect] Uri.parse hook failed: " + e);
    }

    // -------------------------------------------------------------------------
    // Hook 2 : Intent.setPackage — ciblage explicite de com.android.vending
    // -------------------------------------------------------------------------
    try {
        const Intent = Java.use("android.content.Intent");
        const setPackage = Intent.setPackage.overload("java.lang.String");
        setPackage.implementation = function(pkg) {
            if (pkg === "com.android.vending") {
                console.log("[FRIDA_PLAY_STORE_REQUIRED] Intent.setPackage: com.android.vending");
            }
            return setPackage.call(this, pkg);
        };
    } catch (e) {
        console.log("[PlayStoreDetect] Intent.setPackage hook failed: " + e);
    }

    // -------------------------------------------------------------------------
    // Hook 3 : Activity.startActivity — lancement d'intent vers le Play Store
    // -------------------------------------------------------------------------
    try {
        const Activity = Java.use("android.app.Activity");
        const startActivity = Activity.startActivity.overload("android.content.Intent");
        startActivity.implementation = function(intent) {
            try {
                const data = intent.getData();
                if (data) {
                    const uriStr = data.toString();
                    if (uriStr.startsWith("market://") || uriStr.includes("play.google.com")) {
                        console.log("[FRIDA_PLAY_STORE_REQUIRED] startActivity uri: " + uriStr);
                    }
                }
                const pkg = intent.getPackage();
                if (pkg === "com.android.vending") {
                    console.log("[FRIDA_PLAY_STORE_REQUIRED] startActivity pkg: com.android.vending");
                }
            } catch (e2) { /* intent sans data ou package, ignoré */ }
            return startActivity.call(this, intent);
        };
    } catch (e) {
        console.log("[PlayStoreDetect] Activity.startActivity hook failed: " + e);
    }

    // -------------------------------------------------------------------------
    // Hook 4 : ContextWrapper.startActivity — couche supérieure (Services, etc.)
    // -------------------------------------------------------------------------
    try {
        const ContextWrapper = Java.use("android.content.ContextWrapper");
        const startActivityCtx = ContextWrapper.startActivity.overload("android.content.Intent");
        startActivityCtx.implementation = function(intent) {
            try {
                const data = intent.getData();
                if (data) {
                    const uriStr = data.toString();
                    if (uriStr.startsWith("market://") || uriStr.includes("play.google.com")) {
                        console.log("[FRIDA_PLAY_STORE_REQUIRED] ContextWrapper.startActivity uri: " + uriStr);
                    }
                }
                const pkg = intent.getPackage();
                if (pkg === "com.android.vending") {
                    console.log("[FRIDA_PLAY_STORE_REQUIRED] ContextWrapper.startActivity pkg: com.android.vending");
                }
            } catch (e2) { /* ignoré */ }
            return startActivityCtx.call(this, intent);
        };
    } catch (e) {
        console.log("[PlayStoreDetect] ContextWrapper.startActivity hook failed: " + e);
    }

});
