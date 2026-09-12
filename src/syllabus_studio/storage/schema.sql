-- Portable schema. One file, no services, safe to copy between machines.
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    demo        INTEGER NOT NULL DEFAULT 0,
    origin      TEXT NOT NULL DEFAULT 'local',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    body        TEXT NOT NULL          -- the whole Course as JSON
);

CREATE TABLE IF NOT EXISTS lessons (
    course_id    TEXT NOT NULL,
    lesson_id    TEXT NOT NULL,
    generated_at INTEGER NOT NULL,
    body         TEXT NOT NULL,        -- the whole LessonContent as JSON
    PRIMARY KEY (course_id, lesson_id),
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_courses_updated ON courses(updated_at DESC);
