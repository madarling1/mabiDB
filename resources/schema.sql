PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(type, name)
);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
    UNIQUE(entry_id, alias)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (entry_id, tag_id),
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attributes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rune_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL UNIQUE,
    source_sheet TEXT NOT NULL,
    rune_kind TEXT NOT NULL,
    class_name TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT '',
    skill_slot INTEGER,
    raw_tags TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS search_synonyms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    expansion TEXT NOT NULL,
    UNIQUE(keyword, expansion)
);

CREATE INDEX IF NOT EXISTS idx_entries_type ON entries(type);
CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_attributes_key ON attributes(key);
CREATE INDEX IF NOT EXISTS idx_attributes_value ON attributes(value);
CREATE INDEX IF NOT EXISTS idx_rune_details_source_sheet ON rune_details(source_sheet);
CREATE INDEX IF NOT EXISTS idx_rune_details_rune_kind ON rune_details(rune_kind);
CREATE INDEX IF NOT EXISTS idx_rune_details_class_name ON rune_details(class_name);
CREATE INDEX IF NOT EXISTS idx_rune_details_tier ON rune_details(tier);
CREATE INDEX IF NOT EXISTS idx_search_synonyms_keyword ON search_synonyms(keyword);

INSERT OR IGNORE INTO search_synonyms(keyword, expansion) VALUES
    ('브레이크', '무방비'),
    ('무방비', '브레이크');
