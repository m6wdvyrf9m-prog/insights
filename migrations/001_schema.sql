PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'facilitator')),
  learning_experience_id INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TEXT,
  FOREIGN KEY (learning_experience_id) REFERENCES learning_experiences(id)
);

CREATE TABLE IF NOT EXISTS learning_experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  facilitator_user_id INTEGER,
  created_by_user_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (facilitator_user_id) REFERENCES users(id),
  FOREIGN KEY (created_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS participants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name TEXT NOT NULL,
  email TEXT,
  token TEXT NOT NULL UNIQUE,
  token_hash TEXT NOT NULL UNIQUE,
  profile_json TEXT NOT NULL,
  raw_excerpts_json TEXT NOT NULL,
  parse_confidence_json TEXT NOT NULL,
  parse_issues_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER,
  learning_experience_id INTEGER,
  original_filename TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  file_sha256 TEXT NOT NULL,
  parse_status TEXT NOT NULL CHECK (parse_status IN ('parsed', 'failed')),
  parse_confidence_json TEXT NOT NULL,
  parse_issues_json TEXT NOT NULL,
  created_by_user_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (participant_id) REFERENCES participants(id),
  FOREIGN KEY (learning_experience_id) REFERENCES learning_experiences(id),
  FOREIGN KEY (created_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS learning_experience_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  learning_experience_id INTEGER NOT NULL,
  participant_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (learning_experience_id, participant_id),
  FOREIGN KEY (learning_experience_id) REFERENCES learning_experiences(id) ON DELETE CASCADE,
  FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  learning_experience_id INTEGER NOT NULL,
  activity_id INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('deployed', 'closed')) DEFAULT 'deployed',
  idempotency_key TEXT NOT NULL UNIQUE,
  config_json TEXT NOT NULL,
  audit_json TEXT NOT NULL,
  deployed_by_user_id INTEGER NOT NULL,
  deployed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (learning_experience_id) REFERENCES learning_experiences(id) ON DELETE CASCADE,
  FOREIGN KEY (activity_id) REFERENCES activities(id),
  FOREIGN KEY (deployed_by_user_id) REFERENCES users(id),
  UNIQUE (learning_experience_id, activity_id)
);

CREATE TABLE IF NOT EXISTS deployment_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deployment_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  actor_user_id INTEGER,
  details_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (deployment_id) REFERENCES activity_deployments(id) ON DELETE CASCADE,
  FOREIGN KEY (actor_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS card_bank (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  colour TEXT NOT NULL CHECK (colour IN ('Red', 'Yellow', 'Green', 'Blue')),
  ordinal INTEGER NOT NULL,
  content TEXT NOT NULL,
  replaceable_seed INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (colour, ordinal)
);

CREATE TABLE IF NOT EXISTS card_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deployment_id INTEGER NOT NULL,
  participant_id INTEGER NOT NULL,
  card_id INTEGER NOT NULL,
  origin_participant_id INTEGER NOT NULL,
  current_owner_participant_id INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('assigned', 'kept', 'given')) DEFAULT 'assigned',
  decided_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (deployment_id) REFERENCES activity_deployments(id) ON DELETE CASCADE,
  FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
  FOREIGN KEY (card_id) REFERENCES card_bank(id),
  FOREIGN KEY (origin_participant_id) REFERENCES participants(id),
  FOREIGN KEY (current_owner_participant_id) REFERENCES participants(id),
  UNIQUE (deployment_id, participant_id, card_id)
);

CREATE TABLE IF NOT EXISTS card_transfers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id INTEGER NOT NULL,
  deployment_id INTEGER NOT NULL,
  from_participant_id INTEGER NOT NULL,
  to_participant_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (assignment_id) REFERENCES card_assignments(id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id) REFERENCES activity_deployments(id) ON DELETE CASCADE,
  FOREIGN KEY (from_participant_id) REFERENCES participants(id),
  FOREIGN KEY (to_participant_id) REFERENCES participants(id),
  CHECK (from_participant_id <> to_participant_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  csrf_token TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'facilitator')),
  ip_hash TEXT,
  user_agent_hash TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rate_limits (
  bucket TEXT NOT NULL,
  identity_hash TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, identity_hash, window_start)
);

CREATE INDEX IF NOT EXISTS idx_uploads_created_at ON uploads(created_at);
CREATE INDEX IF NOT EXISTS idx_participants_token_hash ON participants(token_hash);
CREATE INDEX IF NOT EXISTS idx_members_experience ON learning_experience_members(learning_experience_id);
CREATE INDEX IF NOT EXISTS idx_assignments_owner ON card_assignments(current_owner_participant_id);
CREATE INDEX IF NOT EXISTS idx_assignments_participant ON card_assignments(participant_id);
CREATE INDEX IF NOT EXISTS idx_transfers_deployment ON card_transfers(deployment_id);
