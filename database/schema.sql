-- 오병훈: 설계서 §5-4의 영속 테이블. 상태/복구 지표 정책은 팀 합의 후 확장.
CREATE TABLE IF NOT EXISTS jobs (
 id text PRIMARY KEY, text text NOT NULL, status text NOT NULL,
 state jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS timeline_events (
 id bigserial PRIMARY KEY, job_id text NOT NULL REFERENCES jobs(id), agent text NOT NULL,
 event_type text NOT NULL, attempt integer NOT NULL CHECK (attempt >= 1),
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS incidents (
 id text PRIMARY KEY, job_id text REFERENCES jobs(id), target text NOT NULL, fault text NOT NULL,
 injected_at timestamptz, detected_at timestamptz, recovered_at timestamptz,
 human_intervention boolean NOT NULL DEFAULT false, result text, run_id text
);
CREATE TABLE IF NOT EXISTS user_inputs (
 id bigserial PRIMARY KEY, job_id text NOT NULL REFERENCES jobs(id), question_id text NOT NULL,
 answer text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS channels (
 channel_id text PRIMARY KEY, sido text, sigungu text, category text NOT NULL,
 name text NOT NULL, org text NOT NULL, method text NOT NULL CHECK (method IN ('web','app','phone')),
 url text NOT NULL, jurisdiction text NOT NULL, verified_at date NOT NULL, source_url text NOT NULL,
 active boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS timeline_job_idx ON timeline_events(job_id, id);
CREATE INDEX IF NOT EXISTS inputs_job_idx ON user_inputs(job_id, id);

-- 오병훈: RAG용 공식 안내 문서·청크. 기존 표와 독립(추가만, 기존 데이터 변경 없음). pgvector 확장이 설치된 이미지 필요.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS knowledge_documents (
 id text PRIMARY KEY, title text NOT NULL, source_url text NOT NULL, org text NOT NULL,
 sido text, sigungu text, jurisdiction text NOT NULL, category text NOT NULL,
 published_at date, verified_at date NOT NULL, active boolean NOT NULL DEFAULT true,
 content_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
-- embedding: 임베딩 모델 미확정이라 차원 미지정 vector. 모델 확정 전 적재를 위해 NULL 허용.
CREATE TABLE IF NOT EXISTS knowledge_chunks (
 id bigserial PRIMARY KEY, document_id text NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
 chunk_index integer NOT NULL CHECK (chunk_index >= 0), content text NOT NULL, embedding vector,
 metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS knowledge_docs_active_idx ON knowledge_documents(category, sido, sigungu) WHERE active;
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_docs_source_url_key ON knowledge_documents(source_url);
CREATE INDEX IF NOT EXISTS knowledge_chunks_document_idx ON knowledge_chunks(document_id);
-- ============================================================================
-- [주석 예시 · 그대로 실행 금지] 임베딩 모델 확정 후 수동 실행.
-- 차원 미지정 vector 컬럼에는 HNSW/IVFFlat 인덱스를 만들 수 없어 여기서는 만들지 않는다.
-- <EMBEDDING_DIMENSION> 은 자리표시자(정수로 바꿔야 함). 기존 embedding이 모두 같은 차원(또는 NULL)이어야 ALTER가 성공한다.
--
--   ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(<EMBEDDING_DIMENSION>);
--   CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw
--     ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
-- ============================================================================
