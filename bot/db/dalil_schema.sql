-- Dalil (savollar) bazasi
-- Manba: dalil.pdf — turli mavzularda arabcha savollar.
-- Daraja (A1-C2) Gemini bilan klassifikatsiya qilinadi (savol tuzilishi va lug'at murakkabligi bo'yicha).

CREATE TABLE IF NOT EXISTS dalil_topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mavzu_ar    TEXT,
    mavzu_uz    TEXT,
    bob_raqami  INTEGER,
    UNIQUE(mavzu_ar, mavzu_uz)
);

CREATE TABLE IF NOT EXISTS dalil_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id    INTEGER NOT NULL,
    savol_ar    TEXT NOT NULL,         -- arabcha savol matni
    savol_uz    TEXT,                   -- o'zbekcha tarjima (ixtiyoriy, Gemini)
    level       TEXT,                   -- A1 / A2 / B1 / B2 / C1 / C2
    izoh        TEXT,                   -- qisqa izoh (savol turi, kalit so'zlar)
    pdf_page    INTEGER,
    FOREIGN KEY (topic_id) REFERENCES dalil_topics(id)
);

CREATE INDEX IF NOT EXISTS idx_dalil_q_topic ON dalil_questions(topic_id);
CREATE INDEX IF NOT EXISTS idx_dalil_q_level ON dalil_questions(level);

CREATE VIRTUAL TABLE IF NOT EXISTS dalil_fts USING fts5(
    mavzu_uz,
    savol_ar,
    savol_uz,
    level UNINDEXED,
    q_id UNINDEXED,
    topic_id UNINDEXED
);
