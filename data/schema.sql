-- SBA Framework — SQLite スキーマ定義
-- 補足設計書 §2.3 に基づく全テーブル定義
--
-- 使い方:
--   sqlite3 C:\TH_Works\SBA\data\api_usage.db < data\schema.sql
--
-- Note:
--   data/*.db は .gitignore で除外。
--   テーブル構造の変更はこのファイルを更新してコミットすること。

-- ============================================================
-- api_usage.db — API レート制限・使用量管理（設計書A-10）
-- ============================================================

CREATE TABLE IF NOT EXISTS api_calls (
    id          TEXT    PRIMARY KEY,            -- UUID
    called_at   TEXT    NOT NULL,               -- ISO 8601
    api_name    TEXT    NOT NULL                -- gemini / youtube / github / stackoverflow / huggingface
                CHECK(api_name IN ('gemini','youtube','github','stackoverflow','huggingface','newsapi')),
    endpoint    TEXT    NOT NULL,               -- 呼び出したエンドポイント
    tokens_used INTEGER NOT NULL DEFAULT 0,     -- 使用トークン数（gemini 用）
    status      TEXT    NOT NULL DEFAULT 'ok'   -- ok / error / throttled
                CHECK(status IN ('ok','error','throttled')),
    error_msg   TEXT,                           -- エラーメッセージ（status=error 時）
    created_at  TEXT    NOT NULL                -- ISO 8601
);

CREATE INDEX IF NOT EXISTS idx_api_calls_name       ON api_calls(api_name);
CREATE INDEX IF NOT EXISTS idx_api_calls_called_at  ON api_calls(called_at);

CREATE TABLE IF NOT EXISTS api_daily_counts (
    id          TEXT    PRIMARY KEY,            -- UUID
    date        TEXT    NOT NULL,               -- YYYY-MM-DD
    api_name    TEXT    NOT NULL,
    call_count  INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(date, api_name)
);

CREATE TABLE IF NOT EXISTS api_stops (
    id          TEXT    PRIMARY KEY,            -- UUID
    api_name    TEXT    NOT NULL,
    stopped_at  TEXT    NOT NULL,               -- ISO 8601
    reason      TEXT    NOT NULL,               -- WARNING / THROTTLE / STOP
    resume_at   TEXT,                           -- 再開予定時刻（NULL = 翌日リセット待ち）
    resolved    INTEGER NOT NULL DEFAULT 0      -- 0: 停止中, 1: 解除済み
);

-- ============================================================
-- experiment_log.db — 自己実験ログ（補足設計書§2.3）
-- ============================================================

CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT    PRIMARY KEY,
    executed_at TEXT    NOT NULL,
    brain_id    TEXT    NOT NULL,
    subskill    TEXT    NOT NULL,
    exp_type    TEXT    NOT NULL                CHECK(exp_type IN ('A','B','C','D')),
    hypothesis  TEXT    NOT NULL,
    plan        TEXT    NOT NULL,
    input_data  TEXT,
    output_data TEXT,
    result      TEXT    NOT NULL                CHECK(result IN ('SUCCESS','FAILURE','PARTIAL')),
    analysis    TEXT,
    delta_score REAL    NOT NULL DEFAULT 0.0,
    exec_ms     INTEGER,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exp_subskill    ON experiments(subskill);
CREATE INDEX IF NOT EXISTS idx_exp_result      ON experiments(result);
CREATE INDEX IF NOT EXISTS idx_exp_executed_at ON experiments(executed_at);
CREATE INDEX IF NOT EXISTS idx_exp_brain_id    ON experiments(brain_id);

-- ============================================================
-- learning_timeline.db — 学習履歴（補足設計書§2.3）
-- ============================================================

CREATE TABLE IF NOT EXISTS timeline (
    id           TEXT    PRIMARY KEY,
    learned_at   TEXT    NOT NULL,
    brain_id     TEXT    NOT NULL,
    source_type  TEXT    NOT NULL               CHECK(source_type IN ('Web','PDF','Video','API','Experiment')),
    source_url   TEXT,
    subskill     TEXT    NOT NULL,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    freshness    REAL    NOT NULL DEFAULT 1.0,  -- 0.0-1.0（古くなるほど低下）
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tl_brain_id    ON timeline(brain_id);
CREATE INDEX IF NOT EXISTS idx_tl_subskill    ON timeline(subskill);
CREATE INDEX IF NOT EXISTS idx_tl_learned_at  ON timeline(learned_at);
CREATE INDEX IF NOT EXISTS idx_tl_source_type ON timeline(source_type);
