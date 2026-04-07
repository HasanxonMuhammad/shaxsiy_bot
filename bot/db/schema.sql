CREATE TABLE IF NOT EXISTS messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER,
    username TEXT,
    first_name TEXT,
    text TEXT,
    reply_to_message_id INTEGER,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    join_date TEXT DEFAULT (datetime('now')),
    last_message_date TEXT,
    message_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'member',
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS strikes (
    user_id INTEGER PRIMARY KEY,
    count INTEGER DEFAULT 0,
    last_strike TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    trigger_at TEXT NOT NULL,
    completed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS muted_chats (
    chat_id INTEGER PRIMARY KEY,
    muted_until TEXT,
    reason TEXT
);

-- O'quvchilar ro'yxati va darajasi
CREATE TABLE IF NOT EXISTS students (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    level TEXT DEFAULT 'boshlang''ich',       -- boshlang'ich / o'rta / yuqori
    current_sura TEXT,                         -- hozir o'qiyotgan sura
    completed_suras TEXT DEFAULT '[]',         -- JSON: tugatgan suralar ro'yxati
    total_lessons INTEGER DEFAULT 0,           -- jami topshirgan darslar soni
    last_lesson_date TEXT,                     -- oxirgi dars sanasi
    avg_score REAL DEFAULT 0,                  -- o'rtacha baho (1-10)
    notes TEXT DEFAULT '',                     -- ustoz yoki bot eslatmalari
    joined_at TEXT DEFAULT (datetime('now'))
);

-- Har bir dars topshirish yozuvi
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    sura TEXT,                                 -- sura nomi yoki raqami
    ayah_range TEXT,                           -- oyat diapazoni, masalan "1-10"
    score INTEGER,                             -- baho 1-10
    feedback TEXT,                             -- bot/ustoz izohi
    submitted_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES students(user_id)
);

-- O'quvchi haqida eslatmalar (Nodira-style memory, lekin DB da)
CREATE TABLE IF NOT EXISTS student_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES students(user_id)
);

-- AI suhbat tarixi (session persistence)
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_chat ON chat_sessions(chat_id, created_at);

-- Bot-to-bot xabarlar
CREATE TABLE IF NOT EXISTS bot_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_bot TEXT NOT NULL,
    to_bot TEXT,
    message TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bot_messages ON bot_messages(to_bot, read);
-- Full-text search uchun virtual jadval
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    content='messages',
    content_rowid='rowid'
);

-- FTS triggerlari (yangi xabar kelganda avtomatik indekslanadi)
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages(chat_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_reminders_trigger ON reminders(completed, trigger_at);
CREATE INDEX IF NOT EXISTS idx_lessons_user ON lessons(user_id, submitted_at);
CREATE INDEX IF NOT EXISTS idx_student_notes_user ON student_notes(user_id);
