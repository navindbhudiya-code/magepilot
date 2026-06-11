"""Graph DDL (docs/architecture/05). One generic nodes+edges pair — the query API is
dominated by "neighbors of X by edge kind", which two indexed tables answer in 1–2
lookups — plus `files` (the unit of incremental invalidation), `modules` (the
ownership/enablement dimension every query filters on), and FTS5 symbol search.

`area` is a first-class edge column: the same plugin can be global in etc/di.xml and
disabled in etc/frontend/di.xml — scope is exactly what grep can never answer."""

SCHEMA_VERSION = 1

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
  id          INTEGER PRIMARY KEY,
  path        TEXT NOT NULL UNIQUE,
  sha1        TEXT NOT NULL,
  mtime_ns    INTEGER NOT NULL,
  size        INTEGER NOT NULL,
  ftype       TEXT NOT NULL,            -- php|di|events|module|registration
  area        TEXT,
  module_id   INTEGER REFERENCES modules(id),
  in_vendor   INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending',   -- pending|done|error (resume checkpoint)
  error       TEXT,
  indexed_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status) WHERE status != 'done';
CREATE INDEX IF NOT EXISTS idx_files_module ON files(module_id);
CREATE INDEX IF NOT EXISTS idx_files_ftype  ON files(ftype);

CREATE TABLE IF NOT EXISTS modules (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,     -- 'Magento_Checkout', 'Vendor_Faq'
  path        TEXT NOT NULL,
  in_vendor   INTEGER NOT NULL DEFAULT 0,
  composer    TEXT,
  enabled     INTEGER,                  -- from app/etc/config.php when readable, else NULL
  setup_ver   TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,            -- module|class|interface|trait|virtual_type|method|event|table
  name        TEXT NOT NULL,
  qname       TEXT NOT NULL,            -- FQCN | 'Class::method' | event name | 'table:name'
  module_id   INTEGER REFERENCES modules(id),
  file_id     INTEGER REFERENCES files(id) ON DELETE CASCADE,
  line_start  INTEGER, line_end INTEGER,
  signature   TEXT,
  attrs       TEXT,                     -- JSON
  UNIQUE(kind, qname)
);
CREATE INDEX IF NOT EXISTS idx_nodes_qname  ON nodes(qname);
CREATE INDEX IF NOT EXISTS idx_nodes_name   ON nodes(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_nodes_file   ON nodes(file_id);
CREATE INDEX IF NOT EXISTS idx_nodes_module ON nodes(module_id, kind);

CREATE TABLE IF NOT EXISTS edges (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,            -- EXTENDS|IMPLEMENTS|USES_TRAIT|INJECTS|PREFERS|
                                        -- PLUGS_INTO|PLUGS_METHOD|OBSERVES|DISPATCHES|
                                        -- DEPENDS_ON_MODULE|VIRTUAL_TYPE_OF|DI_ARGUMENT|USES_TABLE
  src_id      INTEGER REFERENCES nodes(id) ON DELETE CASCADE,
  src_qname   TEXT NOT NULL,
  dst_id      INTEGER REFERENCES nodes(id) ON DELETE SET NULL,
  dst_qname   TEXT NOT NULL,            -- raw target — survives unresolved targets
  file_id     INTEGER REFERENCES files(id) ON DELETE CASCADE,   -- the DECLARING file
  line        INTEGER,
  area        TEXT NOT NULL DEFAULT 'global',
  attrs       TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges(kind, src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges(kind, dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_srcq ON edges(kind, src_qname);
CREATE INDEX IF NOT EXISTS idx_edges_dstq ON edges(kind, dst_qname);
CREATE INDEX IF NOT EXISTS idx_edges_file ON edges(file_id);

-- regular (contentful) FTS5: contentless tables don't support DELETE, and the indexed
-- text (symbol names) is tiny anyway
CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
  name, qname, words,
  tokenize='unicode61', prefix='2 3 4'
);

CREATE TABLE IF NOT EXISTS meta ( key TEXT PRIMARY KEY, value TEXT );
"""
