-- Harbor Lantern 스키마 (DSN-05 · 설계서 DOC-HKG-02 §5.2 그대로).
--
-- 멱등이다 — 전부 `CREATE ... IF NOT EXISTS` 이므로 기동마다 그대로 적용한다
-- (별도 마이그레이션 도구 없음, 설계서 §3.3 · §9).
--
-- PRAGMA 는 **연결마다** 걸어야 한다. 특히 `foreign_keys` 는 SQLite 기본이 OFF 라,
-- 켜지 않아도 테스트가 전부 통과하면서 CASCADE 만 조용히 안 돈다(설계서 §12 F5).
-- 그래서 아래 PRAGMA 블록은 `db.py` 가 연결 생성 직후 실행한다 — 이 파일에도 남겨
-- 두는 이유는 §5.2 의 원문을 한 곳에서 읽을 수 있게 하기 위함이다.
--
-- 저장 규약 (§5.1): `*_at` = UTC ISO-8601 문자열 · `date` = HKT 달력일 'YYYY-MM-DD'
--                  `*_local` = HKT 'HH:MM' · 금액 = 정수 minor unit (HKD cent)
--                  좌표만 REAL (거리 계산 입력이며 금액이 아니다)

PRAGMA journal_mode = WAL;      -- O7: 다중 리더 + 단일 라이터
PRAGMA synchronous  = NORMAL;   -- WAL 에서 안전·성능 균형
PRAGMA foreign_keys = ON;       -- SQLite 는 기본이 OFF 다 (연결마다 켜야 한다)
PRAGMA busy_timeout = 5000;     -- 라이터 경합 시 즉시 SQLITE_BUSY 로 죽지 않게

CREATE TABLE IF NOT EXISTS trip (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  start_date    TEXT NOT NULL,                       -- 'YYYY-MM-DD' (HKT)
  base_currency TEXT NOT NULL DEFAULT 'HKD',
  invite_code   TEXT NOT NULL UNIQUE,                -- 정규형 12자
  revision      INTEGER NOT NULL DEFAULT 1,          -- 동기화용 단조 증가 (DSN-21)
  created_at    TEXT NOT NULL                        -- UTC
);

CREATE TABLE IF NOT EXISTS participant (
  id           TEXT PRIMARY KEY,
  trip_id      TEXT NOT NULL REFERENCES trip(id) ON DELETE CASCADE,
  display_name TEXT NOT NULL,
  token_hash   TEXT NOT NULL UNIQUE,                 -- sha256(원문). 원문은 저장하지 않는다
  is_organizer INTEGER NOT NULL DEFAULT 0,
  joined_at    TEXT NOT NULL,
  UNIQUE (trip_id, display_name)
);
CREATE INDEX IF NOT EXISTS ix_participant_trip ON participant(trip_id);

CREATE TABLE IF NOT EXISTS day (
  id          TEXT PRIMARY KEY,
  trip_id     TEXT NOT NULL REFERENCES trip(id) ON DELETE CASCADE,
  day_index   INTEGER NOT NULL,                      -- 1..4
  date        TEXT NOT NULL,                         -- 'YYYY-MM-DD'
  title       TEXT NOT NULL,
  area        TEXT NOT NULL,
  color       TEXT NOT NULL,                         -- '#22d3ee'
  start_local TEXT NOT NULL,                         -- 'HH:MM' (HKT)
  UNIQUE (trip_id, day_index)
);

CREATE TABLE IF NOT EXISTS spot (
  id                TEXT PRIMARY KEY,
  trip_id           TEXT NOT NULL REFERENCES trip(id) ON DELETE CASCADE,
  day_id            TEXT NOT NULL REFERENCES day(id) ON DELETE CASCADE,
  position          INTEGER NOT NULL,                -- 0-based, 일자 내 연속
  time_label        TEXT NOT NULL,                   -- '오전' | '20:00' ...
  fixed_start_local TEXT,                            -- 'HH:MM' | NULL (고정시각 일정)
  name              TEXT NOT NULL,
  name_original     TEXT NOT NULL DEFAULT '',
  tip               TEXT NOT NULL DEFAULT '',
  hours_text        TEXT NOT NULL DEFAULT '',        -- 자유 문자열 원문 (파서 입력)
  closed_text       TEXT NOT NULL DEFAULT '',
  description       TEXT NOT NULL DEFAULT '',
  recommendation    TEXT NOT NULL DEFAULT '',
  lat               REAL NOT NULL,
  lng               REAL NOT NULL,
  dwell_minutes     INTEGER,                         -- NULL → 시간대 기본값 (O6)
  version           INTEGER NOT NULL DEFAULT 1,      -- 낙관적 잠금 (O7)
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  UNIQUE (day_id, position),
  CHECK (lat >= -90.0  AND lat <= 90.0),
  CHECK (lng >= -180.0 AND lng <= 180.0),
  CHECK (dwell_minutes IS NULL OR dwell_minutes >= 0)
);
CREATE INDEX IF NOT EXISTS ix_spot_trip ON spot(trip_id);
CREATE INDEX IF NOT EXISTS ix_spot_day  ON spot(day_id, position);

CREATE TABLE IF NOT EXISTS visit (
  spot_id TEXT PRIMARY KEY REFERENCES spot(id) ON DELETE CASCADE,
  trip_id TEXT NOT NULL REFERENCES trip(id) ON DELETE CASCADE,
  done_by TEXT NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
  done_at TEXT NOT NULL                              -- UTC
);
CREATE INDEX IF NOT EXISTS ix_visit_trip ON visit(trip_id);

CREATE TABLE IF NOT EXISTS expense (
  id           TEXT PRIMARY KEY,
  trip_id      TEXT NOT NULL REFERENCES trip(id) ON DELETE CASCADE,
  payer_id     TEXT NOT NULL REFERENCES participant(id),
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  currency     TEXT NOT NULL DEFAULT 'HKD' CHECK (currency = 'HKD'),
  note         TEXT NOT NULL DEFAULT '',
  spot_id      TEXT REFERENCES spot(id) ON DELETE SET NULL,
  spent_at     TEXT NOT NULL,                        -- UTC
  version      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_expense_trip ON expense(trip_id);

CREATE TABLE IF NOT EXISTS expense_share (
  expense_id     TEXT NOT NULL REFERENCES expense(id) ON DELETE CASCADE,
  participant_id TEXT NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
  share_minor    INTEGER NOT NULL CHECK (share_minor >= 0),
  PRIMARY KEY (expense_id, participant_id)
);

CREATE TABLE IF NOT EXISTS external_cache (
  key        TEXT PRIMARY KEY,                       -- 'weather:hk' | 'fx:HKD-KRW'
  payload    TEXT NOT NULL,                          -- 정규화된 JSON (공급자 원문 아님)
  fetched_at TEXT NOT NULL                           -- UTC — 마지막 '성공' 시각
);

CREATE TABLE IF NOT EXISTS join_attempt (
  ip           TEXT NOT NULL,
  attempted_at TEXT NOT NULL                         -- UTC
);
CREATE INDEX IF NOT EXISTS ix_join_attempt ON join_attempt(ip, attempted_at);
