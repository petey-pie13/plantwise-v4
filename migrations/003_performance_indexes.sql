-- Migration: 003_performance_indexes.sql
-- Description: Indexes on session_ref, foreign keys, and timestamp ordering to speed up 3G mobile access
-- Up Migration:
CREATE INDEX IF NOT EXISTS idx_diagnosis_sessions_ref ON diagnosis_sessions(session_ref);
CREATE INDEX IF NOT EXISTS idx_diagnosis_sessions_created ON diagnosis_sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_counties_county ON sub_counties(county_id);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conv ON ai_messages(conversation_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_diagnosis_feedback_ref ON diagnosis_feedback(session_ref);
CREATE INDEX IF NOT EXISTS idx_advisory_sessions_county ON advisory_sessions(county);
CREATE INDEX IF NOT EXISTS idx_advisory_sessions_created ON advisory_sessions(created_at DESC);

-- Down Migration (for rollback):
-- DROP INDEX IF EXISTS idx_diagnosis_sessions_ref;
-- DROP INDEX IF EXISTS idx_diagnosis_sessions_created;
-- DROP INDEX IF EXISTS idx_sub_counties_county;
-- DROP INDEX IF EXISTS idx_ai_messages_conv;
-- DROP INDEX IF EXISTS idx_diagnosis_feedback_ref;
-- DROP INDEX IF EXISTS idx_advisory_sessions_county;
-- DROP INDEX IF EXISTS idx_advisory_sessions_created;
