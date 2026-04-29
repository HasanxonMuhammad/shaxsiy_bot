-- Tabir (iboralar / gap yasash) bazasi
-- Manba: tabiir.pdf — har sahifada bir nechta "ibora qutisi" bo'ladi.
-- Har bir qutida: arabcha kalit ibora + o'zbek/turkcha hint + 3-5 ta misol gap.

CREATE TABLE IF NOT EXISTS tabir_topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mavzu_ar    TEXT,
    mavzu_uz    TEXT,
    pdf_page    INTEGER,
    bob_raqami  INTEGER,
    UNIQUE(mavzu_ar, mavzu_uz)
);

CREATE TABLE IF NOT EXISTS tabir_expressions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id    INTEGER NOT NULL,
    head_ar     TEXT NOT NULL,        -- arabcha kalit ibora: "أَحْيَانًا.. وأَحْيَانًا"
    hint_uz     TEXT,                  -- o'zbekcha hint: "Bazan... bazan..."
    hint_tr     TEXT,                  -- turkcha hint: "Bir kere... bir kere..."
    izoh        TEXT,                  -- qisqa izoh (qoida, qachon ishlatiladi)
    pdf_page    INTEGER,
    FOREIGN KEY (topic_id) REFERENCES tabir_topics(id)
);

CREATE TABLE IF NOT EXISTS tabir_examples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    expression_id   INTEGER NOT NULL,
    arabcha         TEXT NOT NULL,     -- to'liq arabcha gap
    tarjima_uz      TEXT,              -- o'zbekcha tarjima (Gemini bilan to'ldiriladi)
    pdf_page        INTEGER,
    FOREIGN KEY (expression_id) REFERENCES tabir_expressions(id)
);

CREATE INDEX IF NOT EXISTS idx_tabir_expr_topic ON tabir_expressions(topic_id);
CREATE INDEX IF NOT EXISTS idx_tabir_ex_expr ON tabir_examples(expression_id);

-- FTS5 indeks: mavzu + ibora boshi + misol gaplar bo'yicha qidirish
CREATE VIRTUAL TABLE IF NOT EXISTS tabir_fts USING fts5(
    mavzu_uz,
    head_ar,
    hint_uz,
    arabcha,
    tarjima_uz,
    expr_id UNINDEXED,
    topic_id UNINDEXED
);
