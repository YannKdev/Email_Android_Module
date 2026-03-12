# Email Android Module

Pipeline automatisé pour analyser des applications Android sur émulateur rooté.
Le système charge des APKs, intercepte le trafic réseau via Frida + mitmproxy (bypass SSL/pinning), et utilise la vision OpenAI pour détecter les formulaires de connexion — en stockant les résultats dans PostgreSQL.

Démo : [osint-email-android.demo-yann.ovh](https://osint-email-android.demo-yann.ovh/)

> La récupération des APKs en local n'est pas publié dans ce projet.

---

## Architecture

Un seul émulateur rooté est utilisé :

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

## Prérequis

- Python 3.11+
- Android SDK (ADB + Emulator)
- Un AVD rooté : `Root`
- PostgreSQL
- Clé API OpenAI
- [Frida](https://frida.re/) + [mitmproxy](https://mitmproxy.org/)

> Le certificat mitmproxy doit être installé manuellement comme certificat système sur l'émulateur Root avant utilisation.

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

---

## Limitations

- Validé sur une configuration précise — d'autres versions d'AVD peuvent casser le pipeline.
- Dépendance à l'API OpenAI (remplaçable par tout autre LLM).
- Taux d'extraction d'APIs reste faible (voir partie résultats)

---

## Projet PlayStore + Repack

Projet en cours de développement qui cible deux cas non couverts par ce pipeline :

- Les apps qui crashent avec Frida (émulateur x86, incompatibilités natives)
- Les apps qui nécessitent le Play Store pour fonctionner

Approche : repack de l'APK directement, sans Frida.

---

## Résultats

Périmètre : apps avec +1M de téléchargements qui déclarent utiliser un email pour le compte utilisateur.

| Étape | Valeur |
| --- | --- |
| Apps +1M de téléchargements ciblées | ~30 000 |
| Apps éligibles (email requis) | ~10 000 |
| Taux de capture de la requête | 10 – 15 % |
| Taux d'info exploitable sur l'email | ~30 % des requêtes capturées |

**Estimation** (tests en cours) :

```
10 000 × 12,5% × 30% ≈ 375 apps potentiellement avec info sur le mail et +1M de téléchargements
```