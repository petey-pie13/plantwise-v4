-- PlantWise V4 Migration 001 (corrected FK column names)
-- Safe: IF NOT EXISTS on all tables

CREATE TABLE IF NOT EXISTS users (
    id               SERIAL PRIMARY KEY,
    full_name        VARCHAR(120) NOT NULL,
    email            VARCHAR(255) NOT NULL UNIQUE,
    phone            VARCHAR(30),
    password_hash    VARCHAR(255),
    role             VARCHAR(20) NOT NULL DEFAULT 'farmer'
                         CHECK (role IN ('farmer','buyer','seller','cooperative','staff')),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    email_verified   BOOLEAN NOT NULL DEFAULT FALSE,
    profile_photo    VARCHAR(512),
    county_id        INTEGER REFERENCES kenya_counties(county_id) ON DELETE SET NULL,
    sub_county_id    INTEGER REFERENCES sub_counties(sub_county_id) ON DELETE SET NULL,
    reset_token      VARCHAR(128),
    reset_token_expiry TIMESTAMPTZ,
    google_sub       VARCHAR(128) UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_email  ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role   ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_county ON users(county_id);

CREATE TABLE IF NOT EXISTS farms (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          VARCHAR(120) NOT NULL,
    county_id     INTEGER REFERENCES kenya_counties(county_id) ON DELETE SET NULL,
    sub_county_id INTEGER REFERENCES sub_counties(sub_county_id) ON DELETE SET NULL,
    total_area_ha NUMERIC(10,4),
    gps_lat       NUMERIC(10,7),
    gps_lon       NUMERIC(10,7),
    soil_ph       NUMERIC(4,2),
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_farms_user ON farms(user_id);

CREATE TABLE IF NOT EXISTS crop_cycles (
    id              SERIAL PRIMARY KEY,
    farm_id         INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    crop_id         INTEGER REFERENCES crops(crop_id) ON DELETE SET NULL,
    variety_name    VARCHAR(120),
    area_ha         NUMERIC(10,4),
    planting_date   DATE,
    expected_harvest DATE,
    status          VARCHAR(20) NOT NULL DEFAULT 'planned'
                        CHECK (status IN ('planned','active','harvested','cancelled')),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_crop_cycles_farm ON crop_cycles(farm_id);
CREATE INDEX IF NOT EXISTS idx_crop_cycles_user ON crop_cycles(user_id);

CREATE TABLE IF NOT EXISTS produce_listings (
    id               SERIAL PRIMARY KEY,
    seller_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    crop_id          INTEGER REFERENCES crops(crop_id) ON DELETE SET NULL,
    crop_name        VARCHAR(120) NOT NULL,
    variety_name     VARCHAR(120),
    quantity_kg      NUMERIC(12,2) NOT NULL CHECK (quantity_kg > 0),
    price_per_kg     NUMERIC(10,2) NOT NULL CHECK (price_per_kg > 0),
    currency         VARCHAR(5) NOT NULL DEFAULT 'KES',
    grade            VARCHAR(50),
    harvest_date     DATE,
    available_from   DATE,
    fulfilment       VARCHAR(30) NOT NULL DEFAULT 'pickup'
                         CHECK (fulfilment IN ('pickup','delivery','both')),
    county_id        INTEGER REFERENCES kenya_counties(county_id) ON DELETE SET NULL,
    sub_county_id    INTEGER REFERENCES sub_counties(sub_county_id) ON DELETE SET NULL,
    location_text    VARCHAR(255),
    description      TEXT,
    status           VARCHAR(20) NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','paused','sold','cancelled')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_listings_seller ON produce_listings(seller_id);
CREATE INDEX IF NOT EXISTS idx_listings_crop   ON produce_listings(crop_id);
CREATE INDEX IF NOT EXISTS idx_listings_status ON produce_listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_county ON produce_listings(county_id);

CREATE TABLE IF NOT EXISTS listing_images (
    id          SERIAL PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES produce_listings(id) ON DELETE CASCADE,
    filename    VARCHAR(512) NOT NULL,
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_listing_images_listing ON listing_images(listing_id);

CREATE TABLE IF NOT EXISTS orders (
    id               SERIAL PRIMARY KEY,
    listing_id       INTEGER NOT NULL REFERENCES produce_listings(id) ON DELETE RESTRICT,
    buyer_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    seller_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    quantity_kg      NUMERIC(12,2) NOT NULL CHECK (quantity_kg > 0),
    price_per_kg     NUMERIC(10,2) NOT NULL,
    total_amount     NUMERIC(14,2) NOT NULL,
    currency         VARCHAR(5)  NOT NULL DEFAULT 'KES',
    fulfilment       VARCHAR(30) NOT NULL DEFAULT 'pickup',
    delivery_address TEXT,
    status           VARCHAR(30) NOT NULL DEFAULT 'pending'
                         CHECK (status IN (
                             'pending','accepted','rejected','cancelled',
                             'payment_pending','payment_confirmed','dispatched',
                             'delivered','completed','disputed'
                         )),
    notes            TEXT,
    idempotency_key  VARCHAR(128) UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_orders_buyer   ON orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_orders_seller  ON orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_orders_listing ON orders(listing_id);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders(status);

CREATE TABLE IF NOT EXISTS order_events (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event_type  VARCHAR(50) NOT NULL,
    description TEXT,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      VARCHAR(255),
    context    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_conv_user ON ai_conversations(user_id);

CREATE TABLE IF NOT EXISTS ai_messages (
    id              SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    sources         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conv ON ai_messages(conversation_id);

CREATE TABLE IF NOT EXISTS user_sessions (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_token VARCHAR(128) NOT NULL UNIQUE,
    ip_address    VARCHAR(45),
    user_agent    TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user  ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_token ON user_sessions(session_token);
