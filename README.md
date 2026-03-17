# Android Email OSINT Pipeline

Projet ayant pour objectif de récupérer automatiquement les endpoints API d'applications Android de test de présence d'email à faibles coûts : serveur x86 avec virtualisation KVM (15€/mois), tokens IA limités (~20€ pour 10 000 apps analysées).

Plateforme regroupant les endpoints récupérés automatiquement : [osint-email-android.demo-yann.ovh](https://osint-email-android.demo-yann.ovh/)

![Screenshot de la démo](assets/screenshot_demo.png)

---

## Architecture

Plusieurs émulateurs rootés en parallèle :

```text
main.py
  │
  ├─ [Émulateur Root]
  │     Installation APK + capture trafic réseau (bypass SSL/pinning)
  │     → Scripts/Analyze_proxy.py + Frida_hook/
  │
  ├─ Analyse UI par IA (détection formulaires login/inscription)
  │     → Scripts/utils_openai.py
  │
  └─ Stockage PostgreSQL
        → Scripts/Database.py
```

---

## Pipeline

1. Téléchargement des APKs / splits APKs en x86 (pré-capture)
2. Installation de l'APK sur émulateur (ABI : PlayStore, x86, Root - Magisk, Android API 30)
3. Navigation dans l'application via structure XML
4. Capture de la requête contenant `test@gmail.com`
5. Rejeu de la/les requêtes capturées avec différents mails pour observer un élément discriminant (post-capture)

---

## Résultats

Périmètre : apps avec +1M de téléchargements qui déclarent utiliser un email pour le compte utilisateur.

| Étape | Valeur |
| --- | --- |
| Apps +1M de téléchargements ciblées | ~40 000 |
| Apps éligibles (email requis) | ~10 000 |
| Taux de capture de la requête | 10 – 15 % |
| Taux d'info exploitable sur l'email | ~30 % des requêtes capturées |

**Estimation** (tests en cours) :

```
10 000 × 12,5% × 30% ≈ 375 apps potentiellement avec info sur le mail et +1M de téléchargements
```

---

## Limitations

- Validé sur une configuration précise — d'autres versions d'AVD peuvent casser le pipeline.
- Dépendance à l'API OpenAI (remplaçable par tout autre LLM).
- Taux d'extraction d'APIs reste faible (voir partie résultats)
- L'émulation sur x86 implique une détection par le Play Integrity Check

---

## Améliorations

- Installation des apps sur appareil Android (ARM) :
  - Bypass protection play store integrity (30~40% des apps)
  - Fix bugs Frida (~10% des apps)
  - Fix apps non disponibles en x86 (10~30% des apps)
- Analyse complète des requêtes pour récupérer les tokens échangés dans des requêtes précédentes

---

## Prérequis

- **Android Studio** avec un AVD configuré : API 30, image PlayStore, x86, rooté via Magisk
- **Python 3.x**
- **ADB** accessible dans le PATH
- **mitmproxy** — le certificat doit être installé manuellement comme certificat système sur l'émulateur Root avant toute utilisation

---

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env
psql -U <user> -d <dbname> -f setup.sql
```

Variables `.env` :

```env
DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=
OPENAI_API_KEY=
```

```bash
python main.py           # Dev (Windows, avec fenêtre)
python main.py --prod    # Prod (Linux headless)
```

---


## Projets utilisés

- [Frida](https://frida.re/) — instrumentation dynamique pour le bypass SSL/pinning
- [httptoolkit/frida-interception-and-unpinning](https://github.com/httptoolkit/frida-interception-and-unpinning) — hooks Frida pour bypass SSL et certificate pinning
- [mitmproxy](https://mitmproxy.org/) — proxy d'interception du trafic réseau
- [YannKdev/PlayStore_Crawler_BackEnd](https://github.com/YannKdev/PlayStore_Crawler_BackEnd) — crawler pour constituer la liste des apps Play Store ciblées
- [PostgreSQL](https://www.postgresql.org/) — base de données relationnelle pour le stockage des endpoints capturés

---