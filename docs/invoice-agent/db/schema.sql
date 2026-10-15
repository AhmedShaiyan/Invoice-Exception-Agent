-- ============================================================
-- Mock SAP purchasing tables (simplified S/4HANA structures)
-- ============================================================
CREATE SCHEMA IF NOT EXISTS sap;

CREATE TABLE IF NOT EXISTS sap.lfa1 (          -- vendor master
    lifnr    TEXT PRIMARY KEY,                  -- vendor number
    name1    TEXT NOT NULL,                     -- vendor name
    stcd1    TEXT NOT NULL,                     -- GST registration number
    address  TEXT,                              -- street address (simplified: one line)
    zterm    TEXT,                              -- payment terms (e.g. NT30 = net 30 days)
    sperr    TEXT,                              -- central posting block: 'X' = blocked, else ''
    ort01    TEXT,                              -- city
    land1    TEXT                               -- country
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
    ebeln       TEXT REFERENCES sap.ekko,
    ebelp       TEXT,                           -- PO item
    matnr       TEXT,                           -- material
    txz01       TEXT,                           -- short text
    menge       NUMERIC NOT NULL,                -- ordered quantity
    meins       TEXT,                           -- unit of measure
    netpr       NUMERIC(14,2) NOT NULL,          -- net unit price
    peinh       NUMERIC NOT NULL DEFAULT 1,       -- price unit (netpr is per this many meins)
    uebto       NUMERIC(5,1),                    -- over-delivery tolerance %, at goods receipt
    untto       NUMERIC(5,1),                    -- under-delivery tolerance %, at goods receipt
    webre       TEXT,                           -- GR-based invoice verification: 'X' = required
    mwskz       TEXT,                           -- tax code
    is_service  TEXT,                           -- 'X' = service line, no goods movement expected
    PRIMARY KEY (ebeln, ebelp)
);

CREATE TABLE IF NOT EXISTS sap.mseg (          -- goods movements (MATDOC compatibility view
                                                -- in real S/4HANA -- see docs/data_sources.md A2)
    mblnr  TEXT,                                -- material document
    zeile  TEXT,
    bwart  TEXT,                                -- movement type (101 = GR, 122 = return to vendor)
    ebeln  TEXT,
    ebelp  TEXT,
    matnr  TEXT,
    menge  NUMERIC NOT NULL,                    -- quantity (always positive; see sap.ekbe for signed)
    meins  TEXT,
    budat  DATE,                                -- posting date
    PRIMARY KEY (mblnr, zeile),
    FOREIGN KEY (ebeln, ebelp) REFERENCES sap.ekpo
);

CREATE TABLE IF NOT EXISTS sap.ekbe (          -- PO history: one row per GR or invoice-receipt
                                                -- posting against a PO line (see data_sources.md A4)
    id     SERIAL PRIMARY KEY,
    ebeln  TEXT,
    ebelp  TEXT,
    vgabe  TEXT NOT NULL,                       -- '1' = goods receipt, '2' = invoice receipt
    belnr  TEXT NOT NULL,                       -- referenced MSEG mblnr (vgabe=1) or RBKP belnr (vgabe=2)
    menge  NUMERIC NOT NULL,                    -- signed: negative for a 122 return
    budat  DATE,
    FOREIGN KEY (ebeln, ebelp) REFERENCES sap.ekpo
);

CREATE TABLE IF NOT EXISTS sap.rbkp (          -- incoming invoice header (as posted via MIRO)
    belnr  TEXT PRIMARY KEY,                    -- invoice document number
    lifnr  TEXT NOT NULL REFERENCES sap.lfa1,
    xblnr  TEXT NOT NULL,                       -- vendor's own invoice number (the duplicate-check key)
    bldat  DATE NOT NULL,                       -- invoice date
    waers  TEXT,
    wrbtr  NUMERIC(14,2)                        -- gross amount
);

CREATE TABLE IF NOT EXISTS sap.rseg (          -- incoming invoice line items
    belnr  TEXT REFERENCES sap.rbkp,
    buzei  TEXT,                                -- invoice line number
    ebeln  TEXT,
    ebelp  TEXT,
    menge  NUMERIC,
    wrbtr  NUMERIC(14,2),
    PRIMARY KEY (belnr, buzei),
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
