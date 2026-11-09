CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- One row per audio file seen on disk, or per Music app track (path 'musicapp://<persistent id>').
-- Rows are never deleted: a file that disappears becomes 'missing', one moved aside becomes 'quarantined',
-- one moved to the Trash becomes 'trashed'.
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    size_bytes INTEGER,
    mtime REAL,
    sha256 TEXT,
    format TEXT,
    first_seen_at TEXT,
    last_scanned_at TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'missing', 'quarantined', 'trashed'))
);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);

CREATE TABLE IF NOT EXISTS tags (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    artist TEXT,
    album_artist TEXT,
    title TEXT,
    album TEXT,
    track_number TEXT,
    disc_number TEXT,
    year TEXT,
    genre TEXT,
    duration_seconds REAL,
    bitrate INTEGER,
    read_error TEXT,
    tags_read_at TEXT
);

CREATE TABLE IF NOT EXISTS fingerprints (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    chromaprint_fp TEXT,
    raw_fp TEXT,
    duration_seconds REAL,
    acoustid_id TEXT,
    mb_recording_id TEXT,
    computed_at TEXT
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    id INTEGER PRIMARY KEY,
    tier TEXT NOT NULL CHECK (tier IN ('exact_hash', 'acoustic', 'text_heuristic')),
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN ('pending_review', 'reviewed', 'ignored')),
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dup_groups_status ON duplicate_groups(status);

CREATE TABLE IF NOT EXISTS duplicate_group_members (
    group_id INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    similarity_score REAL,
    PRIMARY KEY (group_id, file_id)
);
CREATE INDEX IF NOT EXISTS idx_dup_members_file ON duplicate_group_members(file_id);

CREATE TABLE IF NOT EXISTS duplicate_decisions (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
    kept_file_id INTEGER REFERENCES files(id),
    action TEXT NOT NULL CHECK (action IN ('keep_one_quarantine_rest', 'keep_one_trash_rest', 'keep_all', 'not_duplicates')),
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS normalization_log (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    rule_id TEXT NOT NULL,
    field TEXT NOT NULL CHECK (field IN ('artist', 'title', 'album', 'album_artist', 'track_number',
                                         'disc_number', 'year')),
    old_value TEXT,
    new_value TEXT,
    backend TEXT NOT NULL DEFAULT 'file' CHECK (backend IN ('file', 'musicapp')),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'applied', 'rejected', 'reverted')),
    proposed_at TEXT,
    applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_norm_log_status ON normalization_log(status);
CREATE INDEX IF NOT EXISTS idx_norm_log_file ON normalization_log(file_id, rule_id, field);

CREATE TABLE IF NOT EXISTS conversion_jobs (
    id INTEGER PRIMARY KEY,
    source_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    target_path TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    source_disposition TEXT NOT NULL DEFAULT 'keep' CHECK (source_disposition IN ('keep', 'quarantine', 'trash')),
    error_message TEXT,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversion_jobs_status ON conversion_jobs(status);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    root_path TEXT,
    files_seen INTEGER DEFAULT 0,
    files_new INTEGER DEFAULT 0,
    files_updated INTEGER DEFAULT 0,
    files_missing INTEGER DEFAULT 0
);

-- One row per song looked up on MusicBrainz, so each is looked up only once.
CREATE TABLE IF NOT EXISTS musicbrainz_lookups (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    recording_id TEXT,
    status TEXT NOT NULL,
    checked_at TEXT
);

-- Folder sorting: each planned move, and what became of it, so any move can be undone.
CREATE TABLE IF NOT EXISTS organize_moves (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    old_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'applied', 'reverted', 'failed')),
    error_message TEXT,
    planned_at TEXT,
    applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_organize_status ON organize_moves(status);
