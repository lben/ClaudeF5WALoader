CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE apps (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'created',
    current_version INTEGER,
    port INTEGER,
    caddy_route TEXT,
    user_mgmt_enabled INTEGER NOT NULL DEFAULT 0,
    last_deploy_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    archive_path TEXT,
    purge_after TEXT
);

CREATE TABLE app_versions (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    bundle_path TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE (app_id, version_number)
);

CREATE TABLE deployments (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    version_id INTEGER REFERENCES app_versions(id),
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress',
    error_summary TEXT,
    log_path TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE app_runtime (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    pid INTEGER,
    pid_create_time REAL,
    started_at TEXT,
    last_check_at TEXT,
    last_healthy_at TEXT,
    last_failed_at TEXT,
    last_failure_reason TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    deployed_healthy INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE dataset_concepts (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (app_id, name)
);

CREATE TABLE dataset_files (
    id INTEGER PRIMARY KEY,
    concept_id INTEGER NOT NULL REFERENCES dataset_concepts(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    sheet_name TEXT,
    schema_json TEXT NOT NULL DEFAULT '{}',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    is_current INTEGER NOT NULL DEFAULT 1,
    uploaded_by INTEGER REFERENCES users(id),
    uploaded_at TEXT NOT NULL
);

CREATE TABLE app_users (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    username TEXT NOT NULL COLLATE NOCASE,
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    observations TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (app_id, username)
);

CREATE TABLE app_user_attachments (
    id INTEGER PRIMARY KEY,
    app_user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE notifications_sent (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    UNIQUE (app_id, event_key)
);

CREATE TABLE dependency_approvals (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    requirement TEXT NOT NULL,
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT NOT NULL,
    UNIQUE (app_id, requirement)
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    actor TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_apps_owner ON apps(owner_id);
CREATE INDEX idx_apps_state ON apps(state);
CREATE INDEX idx_versions_app ON app_versions(app_id);
CREATE INDEX idx_deployments_app ON deployments(app_id);
CREATE INDEX idx_dataset_files_concept ON dataset_files(concept_id);
CREATE INDEX idx_app_users_app ON app_users(app_id);
CREATE INDEX idx_audit_created ON audit_log(created_at)
