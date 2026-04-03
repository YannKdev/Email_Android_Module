-- Email Android Module - PostgreSQL Setup
-- Run: psql -U <user> -d <dbname> -f setup.sql

-- ==========================================================
-- APPS
-- Stores application names to process (e.g. "spotify")
-- ==========================================================
CREATE TABLE IF NOT EXISTS Apps (
    Name    VARCHAR(255) PRIMARY KEY,
    Status  VARCHAR(50)  NOT NULL DEFAULT 'unprocessed'
    -- Status values: unprocessed | processing | processed | error
);

-- ==========================================================
-- PACKAGES
-- Stores Play Store package IDs associated to an App
-- ==========================================================
CREATE TABLE IF NOT EXISTS Packages (
    PackageName  VARCHAR(255) PRIMARY KEY,
    AppName      VARCHAR(255),
    OriginalApp  VARCHAR(255) REFERENCES Apps(Name) ON DELETE SET NULL,
    Status       VARCHAR(50)  NOT NULL DEFAULT 'unprocessed',
    Pipeline     VARCHAR(50)  NOT NULL DEFAULT 'OK',
    tokens       BIGINT       NOT NULL DEFAULT 0,
    processed_at TIMESTAMPTZ
    -- Status values: unprocessed | processing | processed | error | error_country | error_version
);

-- ==========================================================
-- RESULTS
-- Stores captured HAR (network traffic) per package
-- ==========================================================
CREATE TABLE IF NOT EXISTS Results (
    id          SERIAL PRIMARY KEY,
    HAR         JSONB        NOT NULL,
    PackageName VARCHAR(255) REFERENCES Packages(PackageName) ON DELETE CASCADE,
    date_scan   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ==========================================================
-- EMULATORS
-- Tracks active Android emulator instances
-- ==========================================================
CREATE TABLE IF NOT EXISTS emulators (
    nom           VARCHAR(50) PRIMARY KEY,
    type          VARCHAR(10) NOT NULL CHECK (type IN ('Root', 'PS')),
    status        VARCHAR(50) NOT NULL DEFAULT 'STARTING',
    apps_finished INTEGER     NOT NULL DEFAULT 0,
    apps_error    INTEGER     NOT NULL DEFAULT 0
    -- type: PS (Play Store) | Root
    -- status: STARTING | RUNNING | OFFLINE | PAUSED
);

-- ==========================================================
-- ACCOUNTS
-- Pool of Google accounts used to sign in on emulators
-- ==========================================================
CREATE TABLE IF NOT EXISTS accounts (
    email           VARCHAR(255) PRIMARY KEY,
    mdp             VARCHAR(255) NOT NULL,
    blacklisted     BOOLEAN      NOT NULL DEFAULT FALSE,
    in_use          BOOLEAN      NOT NULL DEFAULT FALSE,
    linked_emulator VARCHAR(50)  REFERENCES emulators(nom) ON DELETE SET NULL
);

-- ==========================================================
-- APPLICATIONS_PROCESS
-- Queue of apps to process (from external data source)
-- ==========================================================
CREATE TABLE IF NOT EXISTS applications_process (
    package_id VARCHAR(255) PRIMARY KEY,
    name       VARCHAR(255),
    status     VARCHAR(50)  NOT NULL DEFAULT 'unprocessed',
    installs   BIGINT
    -- Status values: unprocessed | processing | processed | error
);

-- ==========================================================
-- APPLICATIONS_PROCESS_DATA_MANAGMENT
-- Data flags per application (e.g. email_use eligibility)
-- ==========================================================
CREATE TABLE IF NOT EXISTS applications_process_data_managment (
    package_id VARCHAR(255) PRIMARY KEY REFERENCES applications_process(package_id) ON DELETE CASCADE,
    email_use  BOOLEAN NOT NULL DEFAULT FALSE
);

-- ==========================================================
-- INDEXES
-- ==========================================================
CREATE INDEX IF NOT EXISTS idx_packages_status       ON Packages(Status);
CREATE INDEX IF NOT EXISTS idx_apps_status           ON Apps(Status);
CREATE INDEX IF NOT EXISTS idx_results_package       ON Results(PackageName);
CREATE INDEX IF NOT EXISTS idx_accounts_available    ON accounts(blacklisted, in_use);
CREATE INDEX IF NOT EXISTS idx_appprocess_status     ON applications_process(status);

-- ==========================================================
-- PACKAGES_FULL_PIPELINE
-- Main queue for the Frida pipeline (no download step)
-- APKs available locally in {PACKAGES_BASE_PATH}/{package_id}/
-- ==========================================================
CREATE TABLE IF NOT EXISTS packages_full_pipeline (
    package_id            VARCHAR(255) PRIMARY KEY,
    downloaded            BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE si les APKs sont disponibles localement
    frida_analyze         BOOLEAN,      -- NULL=en attente, FALSE=en cours, TRUE=terminé
    frida_error           TEXT,         -- Message d'erreur si l'analyse a échoué
    result                JSONB,        -- HAR capturé (si des entrées réseau ont été trouvées)
    frida_analyze_at      TIMESTAMPTZ,  -- Date/heure de fin d'analyse
    error_download        TEXT,         -- Message d'erreur si le téléchargement a échoué
    explicit_frida_result VARCHAR(100)  -- Libellé lisible du résultat (pour Grafana)
);

CREATE INDEX IF NOT EXISTS idx_pfp_pending ON packages_full_pipeline(frida_analyze) WHERE frida_analyze IS NULL;

-- Migrations : ajouter les colonnes si elles n'existent pas déjà (DBs existantes)
ALTER TABLE packages_full_pipeline ADD COLUMN IF NOT EXISTS frida_analyze_at      TIMESTAMPTZ;
ALTER TABLE packages_full_pipeline ADD COLUMN IF NOT EXISTS error_download        TEXT;
ALTER TABLE packages_full_pipeline ADD COLUMN IF NOT EXISTS explicit_frida_result VARCHAR(100);

-- ==========================================================
-- MIGRATION : colonnes repack_analyze (pipeline repack_analyze)
-- ==========================================================
ALTER TABLE packages_full_pipeline ADD COLUMN IF NOT EXISTS repack_analyze     BOOLEAN;       -- NULL=en attente, FALSE=en cours, TRUE=terminé
ALTER TABLE packages_full_pipeline ADD COLUMN IF NOT EXISTS repack_result      JSONB;         -- HAR capturé par le pipeline repack
ALTER TABLE packages_full_pipeline ADD COLUMN IF NOT EXISTS repack_explicit    VARCHAR(100);  -- Libellé lisible du résultat repack (pour Grafana)
ALTER TABLE packages_full_pipeline ADD COLUMN IF NOT EXISTS repack_analyze_at  TIMESTAMPTZ;   -- Date/heure de fin d'analyse repack

CREATE INDEX IF NOT EXISTS idx_pfp_repack_pending ON packages_full_pipeline(repack_analyze) WHERE repack_analyze IS NULL;
