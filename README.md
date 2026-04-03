# Email Android Module

Automated pipeline to analyze Android applications on a rooted emulator.
The system loads APKs, intercepts network traffic via Frida + mitmproxy (SSL/pinning bypass), and uses OpenAI vision to detect login forms — storing results in PostgreSQL.

> Local APK retrieval is not published in this project.

---

## Architecture

A single rooted emulator is used:

```text
main.py
  │
  ├─ [Root Emulator]
  │     APK install + network traffic capture (SSL/pinning bypass)
  │     → Scripts/Analyze_proxy.py + Frida_hook/
  │
  ├─ AI UI analysis (login/signup form detection)
  │     → Scripts/utils_openai.py
  │
  └─ PostgreSQL storage
        → Scripts/Database.py
```

---

## Prerequisites

- Python 3.11+
- Android SDK (ADB + Emulator)
- A rooted AVD: `Root`
- PostgreSQL
- OpenAI API key
- [Frida](https://frida.re/) + [mitmproxy](https://mitmproxy.org/)

> The mitmproxy certificate must be manually installed as a system certificate on the Root emulator before use.

---

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env
psql -U <user> -d <dbname> -f setup.sql
```

`.env` variables:

```env
DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=
OPENAI_API_KEY=
```

```bash
python main.py           # Dev (Windows, with window)
python main.py --prod    # Prod (Linux headless)
```

---

## Dependencies

- [Frida](https://frida.re/) — dynamic instrumentation for SSL/pinning bypass
- [httptoolkit/frida-interception-and-unpinning](https://github.com/httptoolkit/frida-interception-and-unpinning) — Frida hooks for SSL and certificate pinning bypass
- [mitmproxy](https://mitmproxy.org/) — network traffic interception proxy
- [YannKdev/PlayStore_Crawler_BackEnd](https://github.com/YannKdev/PlayStore_Crawler_BackEnd) — crawler to build the list of targeted Play Store apps

---

## Limitations

- Validated on a specific configuration — other AVD versions may break the pipeline.
- Depends on the OpenAI API (replaceable with any other LLM).
- API extraction rate remains low (see results section)

---

## PlayStore + Repack Project

A project under development targeting two cases not covered by this pipeline:

- Apps that crash with Frida (x86 emulator, native incompatibilities)
- Apps that require the Play Store to function

Approach: direct APK repack, without Frida.

---

## Results

Scope: apps with 1M+ downloads that declare using an email for the user account.

| Step | Value |
| --- | --- |
| Targeted apps with 1M+ downloads | ~30,000 |
| Eligible apps (email required) | ~10,000 |
| Request capture rate | 10 – 15% |
| Actionable email info rate | ~30% of captured requests |

**Estimate** (tests ongoing):

```
10,000 × 12.5% × 30% ≈ 375 apps potentially with email info and 1M+ downloads
```