-- Migration 002: Add Crop Calendar Farm Tasks, Weather Alerts, and Notifications
-- PlantWise V4 Stage 5 Foundation

BEGIN;

CREATE TABLE IF NOT EXISTS farm_tasks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    farm_id INTEGER REFERENCES farms(id) ON DELETE SET NULL,
    crop_cycle_id INTEGER REFERENCES crop_cycles(id) ON DELETE SET NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    task_category VARCHAR(50) DEFAULT 'general',
    due_date DATE NOT NULL,
    completed_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'postponed', 'cancelled')),
    priority VARCHAR(20) DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
    postponement_reason TEXT,
    suggested_by VARCHAR(30) DEFAULT 'farmer' CHECK (suggested_by IN ('farmer', 'system', 'ai')),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_farm_tasks_user_status ON farm_tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_farm_tasks_due_date ON farm_tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_farm_tasks_cycle ON farm_tasks(crop_cycle_id);

CREATE TABLE IF NOT EXISTS weather_alerts (
    id SERIAL PRIMARY KEY,
    county_id INTEGER REFERENCES kenya_counties(county_id) ON DELETE CASCADE,
    sub_county_id INTEGER REFERENCES sub_counties(sub_county_id) ON DELETE SET NULL,
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'advisory' CHECK (severity IN ('info', 'advisory', 'warning', 'critical')),
    headline VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    recommended_actions TEXT,
    valid_from TIMESTAMP DEFAULT NOW(),
    valid_to TIMESTAMP,
    source VARCHAR(100) DEFAULT 'Open-Meteo Agro Forecast',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_weather_alerts_county ON weather_alerts(county_id, valid_to);

CREATE TABLE IF NOT EXISTS user_notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    link_url VARCHAR(255),
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_notifications_user_unread ON user_notifications(user_id, is_read);

-- Seed initial representative weather advisories for major agricultural counties
INSERT INTO weather_alerts (county_id, alert_type, severity, headline, description, recommended_actions, valid_from, valid_to, source)
SELECT 
    c.county_id,
    'advisory',
    'advisory',
    'Short Rains Onset Observation — ' || c.county_name,
    'Sub-seasonal rainfall models indicate consistent moisture over the next 10-14 days suitable for land preparation and early planting in well-drained soils.',
    'Complete top-dressing preparation, inspect drainage channels, and verify certified seed germination tests before planting.',
    NOW(),
    NOW() + INTERVAL '14 days',
    'Open-Meteo Agro Forecast & KMD Regional Alignment'
FROM kenya_counties c
WHERE c.county_name IN ('Nakuru', 'Uasin Gishu', 'Trans Nzoia', 'Kiambu', 'Meru', 'Machakos', 'Kisumu')
ON CONFLICT DO NOTHING;

COMMIT;
