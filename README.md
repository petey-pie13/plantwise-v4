# PlantWise – AI-Powered Crop Advisory & Disease Diagnosis Expert System

A production-deployed web application providing intelligent agricultural decision support for Kenyan smallholder farmers. It combines fuzzy-logic crop suitability scoring with a hybrid AI diagnosis engine that fuses OpenRouter vision models with a curated local knowledge base.

**Live deployment**: Railway (via Gunicorn)  
**Repository**: https://github.com/petey-pie13/plantwise-v4

---

## 🌟 Features

### Crop Suitability Advisory
- **4-Step Wizard**: Guided input flow — Location → Ecological Conditions → Soil pH → Review
- **GPS + Open-Meteo Integration**: Auto-detects user location and fetches real weather data (30-day rainfall & temperature) via the Open-Meteo API; falls back to manual input
- **Fuzzy Logic Scoring**: Calculates a 0–100% suitability score per crop using optimal/acceptable ranges for soil pH, rainfall, and temperature
- **County-Specific Modifiers**: Suitability scores are adjusted by per-county growing data (`crop_county_suitability` table)
- **Sub-County Resolution**: Supports all 47 Kenyan counties and their sub-counties from the database
- **Fallback Recommendations**: Relaxed-constraint search kicks in when no exact matches are found

### Disease Diagnosis
- **Hybrid AI Analysis** (only method):
  - Upload 1–3 plant photos (JPG/PNG, up to 5 MB each)
  - AI performs **guided analysis**: cross-checks uploaded image against diseases from the local knowledge base for the selected crop
  - AI performs **independent analysis**: identifies additional diseases not in the KB based on KALRO and FAO East Africa literature
  - Results are fused into a single ranked list with confidence scores (0–100%)
- **Multi-Model Fallback**: Tries 6 OpenRouter vision models in sequence (primary → fallback):
  1. `qwen/qwen2.5-vl-72b-instruct:free`
  2. `qwen/qwen2.5-vl-32b-instruct:free`
  3. `moonshotai/kimi-vl-a3b-thinking:free`
  4. `mistralai/mistral-small-3.1-24b-instruct:free`
  5. `google/gemma-3-27b-it:free`
  6. `openrouter/auto`
- **Tabbed Treatment Panels**: Chemical, Biological, Cultural, and Prevention treatment groups per disease
- **User Feedback**: Per-diagnosis thumbs up/down feedback stored in the database
- **Session Logging**: Every diagnosis is recorded in `diagnosis_sessions` for analytics

### Admin Panel
A full-featured admin interface at `/admin` with role-based access:

| Page | Function |
|------|----------|
| **Dashboard** | Live metrics: total diagnoses, advisory sessions, crop count, system uptime; diagnosis trend chart (30 days); top diseases bar chart; real-time activity log |
| **Crops** | Paginated crop table with search/filter; add, edit, delete crops; crop image support |
| **Diseases** | Knowledge base management; add/edit disease records with treatment groups |
| **Users** | Admin user management; activate/deactivate accounts; role assignment |
| **Analytics** | Extended analytics and breakdown by diagnosis method |
| **Feedback** | View user feedback submitted on diagnosis results |
| **Settings** | System configuration |

---

## 🎨 Design System

### Color Palette
| Token | Hex |
|-------|-----|
| Primary Green | `#2d5f4f` |
| Accent Green | `#5fb88c` |
| Light Green | `#8dcdb0` |
| Background | `#f5f7f5` |

### Typography
- **Font Family**: Inter (Google Fonts), with system font fallback
- **Base size**: 16px, responsive scale via CSS custom properties

### Icon Library
- **Lucide Icons** (CDN: `unpkg.com/lucide@latest`) — used throughout all pages via `data-lucide` attributes and `lucide.createIcons()`

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- MySQL 8.0+
- Modern web browser (Chrome, Firefox, Safari, Edge)

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/petey-pie13/plantwise-v4.git
cd plantwise-v4
```

2. **Set up virtual environment**
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**

Create a `.env` file in the root directory:
```env
# Database
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=crop_advisory
DB_PORT=3306

# Flask
SECRET_KEY=your-strong-secret-key-here

# OpenRouter AI Vision API (free tier available at openrouter.ai)
OPENROUTER_API_KEY=your-openrouter-api-key-here
```

> ⚠️ `SECRET_KEY` **must** be set. The app will refuse to start if it is missing or left at the default value. Generate one with:
> ```bash
> python -c "import secrets; print(secrets.token_hex(32))"
> ```

5. **Set up the MySQL database**

Import your existing `crop_advisory` database. The app expects these tables:
```
kenya_counties, sub_counties, crops, crop_suitability,
crop_county_suitability, diseases, symptoms, disease_symptoms,
treatments, admin_users, admin_activity_log,
diagnosis_sessions, advisory_sessions
```

6. **Create the initial admin user**
```bash
python seed_admin.py
```

7. **Run the application (development)**
```bash
python app.py
```

8. **Access the application**
```
http://localhost:5000        # Public interface
http://localhost:5000/admin  # Admin panel
```

---

## 📁 Project Structure

```
plantwise-v4/
├── app.py                          # Flask backend — all routes, AI, and DB logic
├── seed_admin.py                   # One-time script to create the first admin user
├── fix_db_schema.py                # Database schema migration helper
├── update_ai_labels.py             # Utility to update AI label data in the DB
├── Procfile                        # Gunicorn startup command for Railway/Heroku
├── requirements.txt                # Python dependencies
├── .env                            # Environment variables (not committed)
├── .gitignore
├── static/
│   ├── css/
│   │   ├── main.css                # Global design system and public pages
│   │   ├── admin.css               # Admin panel styles
│   │   └── advisory_enhanced.css  # Advisory wizard styles
│   ├── js/
│   │   ├── main.js                 # Common utilities (header, back-link, etc.)
│   │   ├── advisory.js             # Crop advisory wizard logic + Open-Meteo fetch
│   │   ├── diagnosis.js            # Diagnosis method selection and form submission
│   │   └── imageUpload.js          # Drag-and-drop upload, preview, and validation
│   └── Images/
│       ├── icon.jpg                # PlantWise logo/icon
│       ├── image.png               # Crop advisory module background
│       └── lens.png                # Disease diagnosis module background
└── templates/
    ├── index.html                  # Homepage (features + module selection)
    ├── advisory.html               # 4-step crop advisory wizard
    ├── advisory_results.html       # Ranked crop recommendation cards
    ├── results.html                # Legacy results template
    ├── diagnosis.html              # Hybrid AI diagnosis form
    ├── diagnosis_results.html      # Disease tile results with treatment tabs
    ├── admin_login.html            # Admin login page
    ├── admin_dashboard.html        # Admin dashboard with charts
    ├── admin_crops.html            # Crop management table
    ├── admin_diseases.html         # Disease knowledge base management
    ├── admin_users.html            # Admin user management
    ├── admin_user_form.html        # Add/edit admin user form
    ├── admin_crop_form.html        # Add/edit crop form
    ├── admin_analytics.html        # Analytics page
    ├── admin_feedback.html         # User feedback viewer
    └── admin_settings.html         # System settings
```

---

## 🔧 Configuration

### OpenRouter API Setup
1. Sign up at [openrouter.ai](https://openrouter.ai/)
2. Create a free API key from the dashboard
3. Add to `.env`:
```env
OPENROUTER_API_KEY=sk-or-v1-...
```
The app automatically falls back through 6 free vision models if any one fails.

### Open-Meteo (Weather)
No API key required. The advisory wizard fetches live 30-day rainfall and temperature from:
- **Forecast API**: `https://api.open-meteo.com/v1/forecast`
- **Historical Archive API**: `https://archive-api.open-meteo.com/v1/archive` (fallback)

### Database Schema (Key Tables)
| Table | Purpose |
|-------|---------|
| `kenya_counties` | 47 Kenyan counties with coordinates |
| `sub_counties` | Sub-counties linked to counties |
| `crops` | Crop details (name, scientific name, season, yield, image URL) |
| `crop_suitability` | pH, rainfall, temperature ranges (min/optimal/max) |
| `crop_county_suitability` | Per-county suitability modifiers and growing notes |
| `diseases` | Disease knowledge base (name, description, severity) |
| `treatments` | Treatment data (chemical, biological, cultural, prevention) |
| `admin_users` | Admin accounts with bcrypt-hashed passwords and roles |
| `admin_activity_log` | Audit trail for all admin actions |
| `diagnosis_sessions` | Log of every diagnosis (method, crop, IP, timestamp) |
| `advisory_sessions` | Log of every advisory request |

---

## 🌐 API Endpoints

### Public — Crop Advisory
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/advisory` | Advisory wizard page |
| `POST` | `/advisory` | Submit advisory (returns results page) |
| `GET` | `/api/counties` | List all counties |
| `GET` | `/api/sub-counties/<county>` | Sub-counties for a county |
| `POST` | `/api/recommendations` | Get ranked crop recommendations (JSON) |

### Public — Disease Diagnosis
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/diagnosis` | Diagnosis page |
| `POST` | `/api/diagnose` | Submit images for AI diagnosis (JSON) |
| `GET` | `/diagnosis/results` | Results page (reads from session) |
| `POST` | `/api/diagnosis/feedback` | Submit thumbs up/down feedback |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET/POST` | `/admin/login` | Admin login (rate-limited: 10/min) |
| `GET` | `/admin/logout` | Admin logout |
| `GET` | `/admin/dashboard` | Dashboard |
| `GET` | `/admin/crops` | Crop list (search, pagination) |
| `GET/POST` | `/admin/crops/add` | Add crop |
| `GET/POST` | `/admin/crops/<id>/edit` | Edit crop |
| `POST` | `/admin/crops/<id>/delete` | Delete crop |
| `GET` | `/admin/diseases` | Disease list |
| `GET` | `/admin/users` | User list |
| `GET/POST` | `/admin/users/add` | Add admin user |
| `GET/POST` | `/admin/users/<id>/edit` | Edit admin user |
| `POST` | `/admin/users/<id>/toggle` | Activate/deactivate user |
| `GET` | `/admin/analytics` | Analytics page |
| `GET` | `/admin/feedback` | Feedback viewer |
| `GET` | `/admin/api/diagnosis-trend` | Chart data: 30-day trend (JSON) |
| `GET` | `/admin/api/top-diseases` | Chart data: top diagnosed diseases (JSON) |

---

## 🔒 Security

| Feature | Implementation |
|---------|---------------|
| CSRF Protection | Flask-WTF `CSRFProtect` on all state-changing forms |
| Rate Limiting | Flask-Limiter: 200 req/min global; 10 req/min on admin login |
| Password Hashing | Werkzeug `generate_password_hash` / `check_password_hash` (bcrypt) |
| Session Security | `HTTPOnly`, `SameSite=Lax` cookies; 2-hour session lifetime |
| File Upload Limits | Max 10 MB per request; JPG/PNG only; minimum 50 KB per image |
| SQL Injection | Parameterised queries via `mysql-connector-python` |
| Admin Auth | `@login_required` decorator on all admin routes |
| Audit Logging | All admin actions written to `admin_activity_log` |
| CORS | Restricted to `/api/*` routes only |

---

## 📦 Dependencies

```
Flask==3.1.3
Flask-CORS==6.0.2
Flask-WTF==1.2.2
Flask-Limiter==4.1.1
gunicorn==25.3.0
mysql-connector-python==9.6.0
python-dotenv==1.2.2
Werkzeug==3.1.6
requests==2.32.5
Pillow==12.1.1
```

---

## 🚢 Production Deployment (Railway)

The app is deployed on [Railway](https://railway.app) using Gunicorn:

```
web: gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT --timeout 120
```

**Required Railway environment variables** (set in the Railway dashboard):
```
DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT
SECRET_KEY
OPENROUTER_API_KEY
```

To deploy an update:
```bash
git add .
git commit -m "your message"
git push  # Railway auto-deploys on push to main
```

---

## 🧪 Testing

### Manual Smoke Tests
1. **Advisory**: Select any county → auto-detect conditions → adjust pH slider → review → get recommendations
2. **Diagnosis**: Upload a clear leaf/stem photo of a crop → verify disease tiles appear with treatment tabs
3. **Admin**: Login at `/admin` → check dashboard metrics and charts load → add/edit a crop → confirm activity log updates
4. **Fallback**: Disable GPS in browser → verify manual rainfall/temperature inputs appear correctly

### Browser Compatibility
- Chrome / Edge (latest 2 versions)
- Firefox (latest 2 versions)
- Safari (latest 2 versions)
- Mobile: iOS Safari, Chrome for Android

---

## 🐛 Troubleshooting

### App Won't Start
```
RuntimeError: SECRET_KEY is not set or is still the default value.
```
→ Generate and set a proper `SECRET_KEY` in your `.env` file.

### Database Connection Errors
```bash
mysql -u root -p
SHOW DATABASES;
USE crop_advisory;
SHOW TABLES;
```

### OpenRouter AI Errors
- Verify `OPENROUTER_API_KEY` is set and valid at [openrouter.ai/keys](https://openrouter.ai/keys)
- Check that uploaded images are ≥ 50 KB (too-small images are rejected before the API call)
- All 6 models failing simultaneously is rare but possible during heavy load — retry after a few seconds

### Image Upload Issues
- Max **5 MB per image**, max **3 images** per submission
- Accepted formats: **JPG and PNG only**
- Images below 50 KB are rejected with a validation message before upload

---

## 📈 Changelog / Recent Updates

- ✅ Replaced Plant.id API with **OpenRouter AI Vision** (6-model fallback chain)
- ✅ Added **Open-Meteo** live weather integration with historical archive fallback
- ✅ Consolidated diagnosis to a single **Hybrid AI Analysis** method
- ✅ Implemented full **Admin Panel** with user management, crop/disease CRUD, analytics, and feedback
- ✅ Added **CSRF protection**, rate limiting, and audit logging
- ✅ Replaced all emoji icons with **Lucide SVG icons** across all pages
- ✅ Deployed to **Railway** with Gunicorn

---

## 👨‍💻 Developer Information

| | |
|-|-|
| **Developer** | Audi Peter Ojwang' |
| **Student ID** | 23/04956 |
| **Institution** | KCA University |
| **School** | School of Technology |
| **Project** | Final Year Project — PlantWise Agricultural Expert System |

---

## 📝 License

This project is developed as part of an academic final year project at KCA University.

## 🙏 Acknowledgments

- [OpenRouter](https://openrouter.ai/) — AI vision model routing (Qwen 2.5-VL, Kimi-VL, Mistral, Gemma)
- [Open-Meteo](https://open-meteo.com/) — Free weather API used for ecological conditions
- [Lucide Icons](https://lucide.dev/) — Open-source SVG icon library
- [Flask](https://flask.palletsprojects.com/) — Python web framework
- [MySQL](https://www.mysql.com/) — Database engine
- [Railway](https://railway.app/) — Cloud deployment platform
- KALRO and FAO East Africa — Agronomic knowledge base sources
- Agricultural extension officers — Domain expertise validation
