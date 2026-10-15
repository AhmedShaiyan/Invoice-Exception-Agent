-- ============================================================
-- Mock SAP purchasing tables (simplified S/4HANA structures)
-- ============================================================
CREATE SCHEMA IF NOT EXISTS sap;

CREATE TABLE IF NOT EXISTS sap.lfa1 (          -- vendor master
    lifnr  TEXT PRIMARY KEY,                    -- vendor number
    name1  TEXT NOT NULL,                       -- vendor name
    stcd1  TEXT NOT NULL,                       -- GST registration number
    ort01  TEXT,                                -- city
    land1  TEXT                                 -- country
);

CREATE TABLE IF NOT EXISTS sap.ekko (          -- purchase order header
    ebeln  TEXT PRIMARY KEY,                    -- PO number
    lifnr  TEXT NOT NULL REFERENCES sap.lfa1,
    bedat  DATE NOT NULL,                       -- PO date
    waers  TEXT NOT NULL,                       -- currency
    ekorg  TEXT,                                -- purchasing org
    bukrs  TEXT                                 -- company code
);

CREATE TABLE IF NOT EXISTS sap.ekpo (          -- purchase order items
    ebeln  TEXT REFERENCES sap.ekko,
    ebelp  TEXT,                                -- PO item
    matnr  TEXT,                                -- material
    txz01  TEXT,                                -- short text
    menge  NUMERIC NOT NULL,                    -- ordered quantity
    meins  TEXT,                                -- unit of measure
    netpr  NUMERIC(14,2) NOT NULL,              -- net unit price
    PRIMARY KEY (ebeln, ebelp)
);

CREATE TABLE IF NOT EXISTS sap.mseg (          -- goods receipts
    mblnr  TEXT,                                -- material document
    zeile  TEXT,
    bwart  TEXT,                                -- movement type (101 = GR)
    ebeln  TEXT,
    ebelp  TEXT,
    matnr  TEXT,
    menge  NUMERIC NOT NULL,                    -- received quantity
    meins  TEXT,
    budat  DATE,                                -- posting date
    PRIMARY KEY (mblnr, zeile),
    FOREIGN KEY (ebeln, ebelp) REFERENCES sap.ekpo
);

-- ============================================================
-- Agent working tables
-- ============================================================
CREATE SCHEMA IF NOT EXISTS ap;

CREATE TABLE IF NOT EXISTS ap.invoices (
    id                 SERIAL PRIMARY KEY,
    source_file        TEXT NOT NULL,
    vendor_name        TEXT,
    vendor_gst_reg_no  TEXT,
    lifnr              TEXT,                    -- matched vendor (null if unmatched)
    invoice_number     TEXT,
    invoice_date       DATE,
    po_number          TEXT,
    currency           TEXT,
    subtotal           NUMERIC(14,2),
    gst_rate           NUMERIC(5,4),
    gst_amount         NUMERIC(14,2),
    total              NUMERIC(14,2),
    extracted_json     JSONB,                   -- raw LLM extraction
    decision           TEXT CHECK (decision IN ('AUTO_APPROVE','EXCEPTION','APPROVED','REJECTED')),
    created_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ap.exceptions (
    id           SERIAL PRIMARY KEY,
    invoice_id   INT REFERENCES ap.invoices ON DELETE CASCADE,
    code         TEXT NOT NULL,                 -- e.g. PRICE_VARIANCE
    severity     TEXT CHECK (severity IN ('LOW','MEDIUM','HIGH')),
    detail       JSONB,                         -- the numbers behind the flag
    explanation  TEXT,                          -- agent's plain-English reasoning
    suggested_action TEXT
);

-- Append-only audit trail: every agent step and human decision (SOX-style)
CREATE TABLE IF NOT EXISTS ap.audit_log (
    id          BIGSERIAL PRIMARY KEY,
    invoice_id  INT REFERENCES ap.invoices ON DELETE CASCADE,
    actor       TEXT NOT NULL,                  -- 'agent:extractor', 'agent:matcher', 'user:<name>'
    action      TEXT NOT NULL,
    payload     JSONB,
    model       TEXT,                           -- LLM model id, when an LLM was involved
    created_at  TIMESTAMPTZ DEFAULT now()
);
