"""Version-one SQLite schema. No schema changes occur on daemon startup."""

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE maintenance_log (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    details_json TEXT NOT NULL
);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    archived_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('human','agent','tool','unknown')),
    role TEXT,
    metadata_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    metadata_json TEXT NOT NULL,
    UNIQUE(id, agent_id)
);
CREATE TABLE claims (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    author_agent_id TEXT NOT NULL REFERENCES agents(id),
    author_session_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    statement TEXT NOT NULL,
    rationale TEXT,
    confidence TEXT CHECK(confidence IN ('tentative','likely','strong')),
    status TEXT NOT NULL DEFAULT 'open'
      CHECK(status IN ('open','supported','disputed','rejected','accepted','superseded')),
    priority TEXT NOT NULL DEFAULT 'normal'
      CHECK(priority IN ('low','normal','high','critical')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    superseded_by_claim_id INTEGER,
    CHECK((status='superseded') = (superseded_by_claim_id IS NOT NULL)),
    UNIQUE(project_id,id),
    FOREIGN KEY(author_session_id, author_agent_id) REFERENCES sessions(id,agent_id),
    FOREIGN KEY(project_id,superseded_by_claim_id) REFERENCES claims(project_id,id)
);
CREATE TABLE evidence (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    claim_id INTEGER NOT NULL,
    author_agent_id TEXT NOT NULL REFERENCES agents(id),
    author_session_id TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK(polarity IN ('supports','contradicts','neutral')),
    method TEXT NOT NULL,
    summary TEXT NOT NULL,
    procedure TEXT,
    observation TEXT,
    reproducibility TEXT NOT NULL DEFAULT 'unreplicated'
      CHECK(reproducibility IN ('unreplicated','replicated','failed-replication','not-applicable')),
    replicates_evidence_id INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(project_id,id),
    FOREIGN KEY(project_id,claim_id) REFERENCES claims(project_id,id),
    FOREIGN KEY(project_id,replicates_evidence_id) REFERENCES evidence(project_id,id),
    FOREIGN KEY(author_session_id,author_agent_id) REFERENCES sessions(id,agent_id)
);
CREATE TABLE requests (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    created_by_agent_id TEXT NOT NULL REFERENCES agents(id),
    created_by_session_id TEXT NOT NULL,
    claim_id INTEGER,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal'
      CHECK(priority IN ('low','normal','high','critical')),
    desired_redundancy INTEGER NOT NULL DEFAULT 1 CHECK(desired_redundancy BETWEEN 1 AND 50),
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    cancelled_at INTEGER,
    cancellation_note TEXT,
    CHECK(completed_at IS NULL OR cancelled_at IS NULL),
    UNIQUE(project_id,id),
    FOREIGN KEY(project_id,claim_id) REFERENCES claims(project_id,id),
    FOREIGN KEY(created_by_session_id,created_by_agent_id) REFERENCES sessions(id,agent_id)
);
CREATE TABLE leases (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    request_id INTEGER NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    session_id TEXT NOT NULL,
    leased_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    released_at INTEGER,
    release_note TEXT,
    completed_at INTEGER,
    completion_note TEXT,
    CHECK(released_at IS NULL OR completed_at IS NULL),
    UNIQUE(project_id,id),
    FOREIGN KEY(project_id,request_id) REFERENCES requests(project_id,id),
    FOREIGN KEY(session_id,agent_id) REFERENCES sessions(id,agent_id)
);
CREATE INDEX leases_request_idx ON leases(request_id,expires_at);
CREATE UNIQUE INDEX leases_completed_agent_idx ON leases(request_id,agent_id)
    WHERE completed_at IS NOT NULL;
CREATE TABLE lease_evidence (
    project_id INTEGER NOT NULL,
    lease_id INTEGER NOT NULL,
    evidence_id INTEGER NOT NULL,
    PRIMARY KEY(lease_id,evidence_id),
    FOREIGN KEY(project_id,lease_id) REFERENCES leases(project_id,id),
    FOREIGN KEY(project_id,evidence_id) REFERENCES evidence(project_id,id)
);
CREATE TABLE lease_claims (
    project_id INTEGER NOT NULL,
    lease_id INTEGER NOT NULL,
    claim_id INTEGER NOT NULL,
    PRIMARY KEY(lease_id,claim_id),
    FOREIGN KEY(project_id,lease_id) REFERENCES leases(project_id,id),
    FOREIGN KEY(project_id,claim_id) REFERENCES claims(project_id,id)
);
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    from_claim_id INTEGER NOT NULL,
    to_claim_id INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(type IN
      ('supports','contradicts','refines','depends-on','duplicates','related','supersedes')),
    author_agent_id TEXT NOT NULL REFERENCES agents(id),
    author_session_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    note TEXT,
    CHECK(from_claim_id != to_claim_id),
    UNIQUE(project_id,from_claim_id,to_claim_id,type),
    FOREIGN KEY(project_id,from_claim_id) REFERENCES claims(project_id,id),
    FOREIGN KEY(project_id,to_claim_id) REFERENCES claims(project_id,id),
    FOREIGN KEY(author_session_id,author_agent_id) REFERENCES sessions(id,agent_id)
);
CREATE TABLE refs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    label TEXT,
    uri TEXT,
    value TEXT,
    metadata_json TEXT NOT NULL,
    created_by_agent_id TEXT NOT NULL REFERENCES agents(id),
    created_by_session_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    CHECK(uri IS NOT NULL OR value IS NOT NULL),
    UNIQUE(project_id,id),
    FOREIGN KEY(created_by_session_id,created_by_agent_id) REFERENCES sessions(id,agent_id)
);
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    UNIQUE(project_id,name),
    UNIQUE(project_id,id)
);
CREATE TABLE claim_refs (
    project_id INTEGER NOT NULL,
    claim_id INTEGER NOT NULL,
    ref_id INTEGER NOT NULL,
    PRIMARY KEY(claim_id,ref_id),
    FOREIGN KEY(project_id,claim_id) REFERENCES claims(project_id,id),
    FOREIGN KEY(project_id,ref_id) REFERENCES refs(project_id,id)
);
CREATE TABLE evidence_refs (
    project_id INTEGER NOT NULL,
    evidence_id INTEGER NOT NULL,
    ref_id INTEGER NOT NULL,
    PRIMARY KEY(evidence_id,ref_id),
    FOREIGN KEY(project_id,evidence_id) REFERENCES evidence(project_id,id),
    FOREIGN KEY(project_id,ref_id) REFERENCES refs(project_id,id)
);
CREATE TABLE request_refs (
    project_id INTEGER NOT NULL,
    request_id INTEGER NOT NULL,
    ref_id INTEGER NOT NULL,
    PRIMARY KEY(request_id,ref_id),
    FOREIGN KEY(project_id,request_id) REFERENCES requests(project_id,id),
    FOREIGN KEY(project_id,ref_id) REFERENCES refs(project_id,id)
);
CREATE TABLE claim_tags (
    project_id INTEGER NOT NULL,
    claim_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY(claim_id,tag_id),
    FOREIGN KEY(project_id,claim_id) REFERENCES claims(project_id,id),
    FOREIGN KEY(project_id,tag_id) REFERENCES tags(project_id,id)
);
CREATE TABLE evidence_tags (
    project_id INTEGER NOT NULL,
    evidence_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY(evidence_id,tag_id),
    FOREIGN KEY(project_id,evidence_id) REFERENCES evidence(project_id,id),
    FOREIGN KEY(project_id,tag_id) REFERENCES tags(project_id,id)
);
CREATE TABLE request_tags (
    project_id INTEGER NOT NULL,
    request_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY(request_id,tag_id),
    FOREIGN KEY(project_id,request_id) REFERENCES requests(project_id,id),
    FOREIGN KEY(project_id,tag_id) REFERENCES tags(project_id,id)
);
CREATE TABLE activity (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    timestamp INTEGER NOT NULL,
    actor_agent_id TEXT NOT NULL REFERENCES agents(id),
    actor_session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT,
    FOREIGN KEY(actor_session_id,actor_agent_id) REFERENCES sessions(id,agent_id)
);
CREATE INDEX activity_project_seq_idx ON activity(project_id,seq);
CREATE TABLE operation_results (
    generation TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(generation,agent_id,session_id,operation_id)
);
CREATE TABLE backup_jobs (
    id TEXT PRIMARY KEY,
    generation TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    input_json TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(state IN ('reserved','complete')),
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    receipt_json TEXT,
    UNIQUE(generation,agent_id,session_id,operation_id)
);
CREATE TABLE search_docs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    entity_type TEXT NOT NULL CHECK(entity_type IN ('claim','evidence','request')),
    entity_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    UNIQUE(project_id,entity_type,entity_id)
);
CREATE VIRTUAL TABLE search_fts USING fts5(text, content='search_docs', content_rowid='id');
CREATE TABLE search_tokens (
    doc_id INTEGER NOT NULL REFERENCES search_docs(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    PRIMARY KEY(doc_id,token)
);
CREATE INDEX search_tokens_token_idx ON search_tokens(token);
"""
