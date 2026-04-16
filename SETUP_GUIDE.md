# PlantWise - Complete Setup & Deployment Guide

## 📋 Table of Contents
1. [System Requirements](#system-requirements)
2. [Local Development Setup](#local-development-setup)
3. [Database Configuration](#database-configuration)
4. [Plant.id API Setup](#plantid-api-setup)
5. [Running the Application](#running-the-application)
6. [Testing Guide](#testing-guide)
7. [Deployment to Production](#deployment-to-production)
8. [Troubleshooting](#troubleshooting)

---

## 🖥️ System Requirements

### Minimum Requirements
- **Operating System**: Windows 10+, macOS 10.14+, or Linux (Ubuntu 20.04+)
- **Python**: 3.8 or higher
- **MySQL**: 8.0 or higher
- **RAM**: 4GB minimum
- **Storage**: 500MB free space
- **Browser**: Chrome 90+, Firefox 88+, Safari 14+, or Edge 90+

### Recommended Requirements
- Python 3.10+
- MySQL 8.0+
- 8GB RAM
- SSD storage

---

## 🚀 Local Development Setup

### Step 1: Install Python
```bash
# Check Python version
python --version  # Should be 3.8+

# If not installed, download from python.org
# Windows: https://www.python.org/downloads/windows/
# macOS: brew install python3
# Linux: sudo apt-get install python3 python3-pip
```

### Step 2: Install MySQL
```bash
# Windows: Download from https://dev.mysql.com/downloads/installer/
# macOS: brew install mysql
# Linux: sudo apt-get install mysql-server

# Start MySQL service
# Windows: Services > MySQL > Start
# macOS: brew services start mysql
# Linux: sudo systemctl start mysql
```

### Step 3: Clone/Download Project
```bash
# If using git
git clone <repository-url>
cd plantwise

# Or extract downloaded ZIP file
cd path/to/plantwise
```

### Step 4: Create Virtual Environment
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate

# macOS/Linux:
source venv/bin/activate

# You should see (venv) in your terminal prompt
```

### Step 5: Install Dependencies
```bash
# Install all required packages
pip install -r requirements.txt

# Or install manually:
pip install Flask==3.0.0
pip install Flask-CORS==4.0.0
pip install mysql-connector-python==8.2.0
pip install python-dotenv==1.0.0
```

---

## 🗄️ Database Configuration

### Step 1: Create Database
```sql
-- Log into MySQL
mysql -u root -p

-- Create database
CREATE DATABASE crop_advisory;
USE crop_advisory;
```

### Step 2: Import Existing Schema
```sql
-- If you have a SQL dump file
mysql -u root -p crop_advisory < crop_advisory.sql

-- Or manually create tables if needed
```

### Step 3: Verify Database Structure
```sql
-- Check tables exist
SHOW TABLES;

-- Should show:
-- kenya_counties
-- crops
-- crop_suitability

-- Verify data
SELECT COUNT(*) FROM kenya_counties;
SELECT COUNT(*) FROM crops;
SELECT COUNT(*) FROM crop_suitability;
```

### Step 4: Configure Environment Variables
```bash
# Copy example env file
cp .env.example .env

# Edit .env with your database credentials
# Windows: notepad .env
# macOS/Linux: nano .env
```

Update the following in `.env`:
```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=crop_advisory
DB_PORT=3306
```

---

## 🔑 Plant.id API Setup

### Step 1: Get API Key
1. Visit https://web.plant.id/
2. Sign up for a free account
3. Go to API section in dashboard
4. Copy your API key

### Step 2: Configure API Key
1. Add to `.env` file:
```env
PLANT_ID_API_KEY=your_actual_api_key_here
```

2. Update `static/js/imageUpload.js` (line 214):
```javascript
'Api-Key': 'your_actual_api_key_here'
```

### Step 3: Test API Connection
```bash
# Run a simple test
curl -X POST "https://api.plant.id/v2/health_assessment" \
     -H "Content-Type: application/json" \
     -H "Api-Key: your_api_key" \
     -d '{"images": ["base64_encoded_image"]}'
```

---

## ▶️ Running the Application

### Development Mode

1. **Activate virtual environment** (if not already active)
```bash
# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

2. **Start the application**
```bash
python app.py
```

3. **Access the application**
```
Open browser: http://localhost:5000
```

You should see:
```
✓ Database connection successful!
Starting PlantWise Agricultural Expert System...
Access at: http://localhost:5000

 * Running on http://0.0.0.0:5000
 * Debug mode: on
```

### Production Mode

1. **Update `.env` for production**
```env
FLASK_ENV=production
FLASK_DEBUG=False
```

2. **Run with production server (gunicorn)**
```bash
# Install gunicorn
pip install gunicorn

# Run application
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

---

## 🧪 Testing Guide

### Manual Testing Checklist

#### Homepage
- [ ] Page loads without errors
- [ ] Logo and navigation display correctly
- [ ] Hero section background shows
- [ ] Feature cards are visible
- [ ] Module cards are clickable
- [ ] Responsive on mobile/tablet

#### Crop Advisory
- [ ] County dropdown populates
- [ ] Step progression works (1→2→3→4)
- [ ] pH slider updates value display
- [ ] Environmental cards are selectable
- [ ] Review shows correct data
- [ ] Form submits successfully
- [ ] Results page displays recommendations
- [ ] Suitability scores calculate correctly

#### Disease Diagnosis
- [ ] Three diagnosis options display
- [ ] Crop selection works
- [ ] Image upload accepts JPG/PNG
- [ ] Max 3 images enforced
- [ ] File size limit enforced (5MB)
- [ ] Image preview displays
- [ ] Symptom selection works
- [ ] Confidence sliders function
- [ ] Form submits correctly
- [ ] Results show top 3 diseases

### Browser Testing
Test in:
- [ ] Google Chrome (latest)
- [ ] Mozilla Firefox (latest)
- [ ] Safari (latest)
- [ ] Microsoft Edge (latest)
- [ ] Mobile browsers (iOS Safari, Chrome Android)

### Performance Testing
```bash
# Test page load speed
# Use Chrome DevTools > Network tab
# Target: < 2 seconds for initial load

# Test image upload
# Upload 3 x 5MB images
# Target: < 10 seconds for processing
```

---

## 🌐 Deployment to Production

### Option 1: Deploy to AWS EC2

1. **Launch EC2 Instance**
```bash
# Amazon Linux 2 or Ubuntu 22.04
# t2.micro or larger
```

2. **Install Dependencies**
```bash
sudo yum update -y  # or sudo apt update
sudo yum install python3 python3-pip mysql -y
```

3. **Clone Project**
```bash
git clone <your-repo>
cd plantwise
```

4. **Set up Database**
```bash
# Install MySQL or use RDS
sudo yum install mysql-server -y
sudo systemctl start mysqld
```

5. **Configure Application**
```bash
cp .env.example .env
nano .env  # Update with production values
```

6. **Run with Gunicorn**
```bash
pip3 install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

7. **Set up Nginx (Optional)**
```bash
sudo yum install nginx -y
# Configure reverse proxy
```

### Option 2: Deploy to Heroku

1. **Install Heroku CLI**
```bash
# Download from https://devcenter.heroku.com/articles/heroku-cli
```

2. **Create Heroku App**
```bash
heroku login
heroku create plantwise-app
```

3. **Add MySQL Addon**
```bash
heroku addons:create jawsdb:kitefin
```

4. **Set Environment Variables**
```bash
heroku config:set PLANT_ID_API_KEY=your_key
heroku config:set SECRET_KEY=your_secret
```

5. **Deploy**
```bash
git push heroku main
heroku open
```

### Option 3: Deploy to DigitalOcean

1. **Create Droplet** (Ubuntu 22.04)
2. **SSH into Server**
```bash
ssh root@your_droplet_ip
```

3. **Install Dependencies**
```bash
apt update
apt install python3 python3-pip mysql-server nginx -y
```

4. **Clone and Configure**
```bash
git clone <your-repo>
cd plantwise
pip3 install -r requirements.txt
```

5. **Set up Systemd Service**
```bash
sudo nano /etc/systemd/system/plantwise.service
```

```ini
[Unit]
Description=PlantWise Application
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/plantwise
ExecStart=/usr/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

6. **Start Service**
```bash
sudo systemctl start plantwise
sudo systemctl enable plantwise
```

---

## 🔧 Troubleshooting

### Database Connection Issues

**Problem**: "Error connecting to MySQL Database"
```bash
# Solution 1: Check MySQL is running
# Windows: Services > MySQL > Status
# Linux: sudo systemctl status mysql
# macOS: brew services list

# Solution 2: Verify credentials
mysql -u root -p  # Enter password from .env

# Solution 3: Check port
netstat -an | grep 3306
```

### Import Errors

**Problem**: "ModuleNotFoundError: No module named 'flask'"
```bash
# Solution: Install dependencies
pip install -r requirements.txt

# Or install individually
pip install Flask Flask-CORS mysql-connector-python python-dotenv
```

### Plant.id API Errors

**Problem**: "Plant.id API error: 401 Unauthorized"
```bash
# Solution 1: Verify API key
# Check .env file has correct key
# Check imageUpload.js has correct key

# Solution 2: Check API limits
# Free tier: 20 calls/day
# Check usage at https://web.plant.id/
```

**Problem**: "Plant.id API error: 413 Payload Too Large"
```bash
# Solution: Images too large
# Max size: 5MB per image
# Compress images before upload
```

### Port Already in Use

**Problem**: "Address already in use: Port 5000"
```bash
# Windows:
netstat -ano | findstr :5000
taskkill /PID <PID> /F

# macOS/Linux:
lsof -ti:5000 | xargs kill -9

# Or change port in app.py:
app.run(debug=True, host='0.0.0.0', port=5001)
```

### Static Files Not Loading

**Problem**: CSS/JS files return 404
```bash
# Solution 1: Check folder structure
ls static/css/
ls static/js/

# Solution 2: Clear browser cache
# Ctrl+Shift+R (Chrome/Firefox)
# Cmd+Shift+R (macOS)

# Solution 3: Check Flask static_folder
# In app.py, verify:
# app = Flask(__name__, static_folder='static')
```

### Image Upload Issues

**Problem**: Images not uploading
```bash
# Solution 1: Check file size (max 5MB)
# Solution 2: Check file type (JPG, PNG only)
# Solution 3: Check browser console for errors (F12)
# Solution 4: Verify imageUpload.js is loaded
```

---

## 📊 Monitoring & Logs

### View Application Logs
```bash
# Development mode (console output)
python app.py

# Production mode with logging
tail -f /var/log/plantwise/app.log
```

### Database Performance
```sql
-- Check slow queries
SHOW FULL PROCESSLIST;

-- Optimize tables
OPTIMIZE TABLE crops;
OPTIMIZE TABLE crop_suitability;

-- Add indexes if needed
CREATE INDEX idx_crop_name ON crops(crop_name);
```

### API Usage Monitoring
```bash
# Check Plant.id API usage
# Dashboard: https://web.plant.id/dashboard
# Track daily limits and response times
```

---

## 🆘 Getting Help

If you encounter issues not covered here:

1. **Check Documentation**: Review README.md and SETUP_GUIDE.md
2. **Search Issues**: Look for similar problems in project issues
3. **Console Logs**: Check browser console (F12) for errors
4. **Server Logs**: Check Flask output for error messages
5. **Database Logs**: Check MySQL error logs

**Contact**:
- Developer: Audi Peter Ojwang'
- Email: [your-email@example.com]
- Institution: KCA University

---

**Last Updated**: February 2026  
**Version**: 1.0.0
