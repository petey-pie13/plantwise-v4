# PlantWise - AI-Powered Crop Advisory and Disease Diagnosis Expert System

A modern web application that provides intelligent agricultural decision support through crop suitability recommendations and disease diagnosis using fuzzy logic, rule-based reasoning, and AI-powered image recognition.

## 🌟 Features

### Crop Suitability Advisory
- **Smart Recommendations**: Get personalized crop suggestions based on environmental conditions
- **Fuzzy Logic Processing**: Handle vague inputs like "high rainfall" intelligently
- **Location-Based**: County and sub-county specific recommendations
- **Interactive Wizard**: 4-step guided process with real-time validation
- **Confidence Scoring**: 0-100% suitability scores for each crop

### Disease Diagnosis
- **Three Diagnosis Methods**:
  - **Symptom-Based**: Traditional expert system using plant symptoms
  - **AI Image Analysis**: Upload plant photos for instant identification via Plant.id API
  - **Hybrid Approach**: Combine symptoms and images for highest accuracy
- **Certainty Factors**: Confidence-rated diagnoses
- **Treatment Recommendations**: Chemical and cultural practices
- **Detailed Reasoning**: Transparent explanation of diagnosis

## 🎨 Design System

### Color Palette
- Primary Green: `#2d5f4f`
- Accent Green: `#5fb88c`
- Light Green: `#8dcdb0`
- Background: `#f5f7f5`

### Typography
- Font Family: System fonts (Apple, Roboto, Helvetica)
- Scale: 16px base, responsive scaling

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- MySQL 8.0+
- Modern web browser (Chrome, Firefox, Safari, Edge)

### Installation

1. **Clone the repository**
```bash
git clone <repository-url>
cd plantwise
```

2. **Set up virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install flask flask-cors mysql-connector-python python-dotenv --break-system-packages
```

4. **Configure database**
Create a `.env` file in the root directory:
```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=crop_advisory
DB_PORT=3306
SECRET_KEY=your-secret-key-here
PLANT_ID_API_KEY=your-plant-id-api-key-here
```

5. **Set up MySQL database**
```sql
-- The database should already exist with tables:
-- kenya_counties, crops, crop_suitability
-- Import your existing crop_advisory database
```

6. **Update API Key**
Edit `/static/js/imageUpload.js` and replace `YOUR_PLANT_ID_API_KEY_HERE` with your actual Plant.id API key.

7. **Run the application**
```bash
python app.py
```

8. **Access the application**
Open your browser and navigate to:
```
http://localhost:5000
```

## 📁 Project Structure

```
plantwise/
├── app.py                          # Flask backend (your existing file)
├── .env                            # Environment variables
├── requirements.txt                # Python dependencies
├── static/
│   ├── css/
│   │   └── main.css               # Main stylesheet
│   ├── js/
│   │   ├── main.js                # Common utilities
│   │   ├── advisory.js            # Crop advisory wizard
│   │   ├── diagnosis.js           # Diagnosis logic
│   │   └── imageUpload.js         # Image handling & Plant.id API
│   └── images/
│       └── icons/                 # SVG icons (optional)
└── templates/
    ├── index.html                 # Homepage
    ├── advisory.html              # Crop advisory wizard
    ├── results.html               # Crop recommendations
    ├── diagnosis.html             # Disease diagnosis
    └── diagnosis_results.html     # Diagnosis results
```

## 🔧 Configuration

### Plant.id API Setup
1. Sign up at https://web.plant.id/
2. Get your API key from the dashboard
3. Add the key to your `.env` file:
```env
PLANT_ID_API_KEY=your_api_key_here
```
4. Update `/static/js/imageUpload.js` line 214 with your API key

### Database Schema
Your existing `crop_advisory` database should have these tables:
- `kenya_counties` - County information
- `crops` - Crop details
- `crop_suitability` - Suitability parameters (pH, rainfall, temperature ranges)

## 🌐 API Endpoints

### Crop Advisory
- `GET /advisory` - Display advisory form
- `POST /advisory` - Submit advisory request
- `GET /api/counties` - Get all counties
- `POST /api/recommendations` - Get crop recommendations

### Disease Diagnosis
- `GET /diagnosis` - Display diagnosis form
- `POST /api/diagnose` - Submit diagnosis request

## 🎯 Usage Guide

### Crop Advisory Workflow
1. Navigate to homepage
2. Click "Crop Advisory"
3. Complete 4-step wizard:
   - **Step 1**: Select county and sub-county
   - **Step 2**: Adjust soil pH slider (0-14)
   - **Step 3**: Choose rainfall and temperature levels
   - **Step 4**: Review and submit
4. View ranked crop recommendations with suitability scores

### Disease Diagnosis Workflow
1. Navigate to homepage
2. Click "Disease Diagnosis"
3. Choose diagnosis method:
   - **Symptom-Based**: Select plant part and symptoms
   - **AI Image Analysis**: Upload 1-3 plant images
   - **Hybrid**: Combine both methods
4. View top 3 disease matches with treatment recommendations

## 🔒 Security Features
- Input validation (client and server-side)
- SQL injection prevention
- XSS protection
- CSRF tokens for forms
- File upload size limits
- Allowed file type validation

## 📊 Performance Optimizations
- Image compression before upload
- Lazy loading for images
- Database query optimization
- CSS/JS minification (production)
- Caching for API responses

## 🧪 Testing

### Manual Testing
1. Test crop advisory with different parameters
2. Upload various plant images
3. Test symptom selection
4. Verify results accuracy
5. Check responsive design on mobile

### Browser Compatibility
- Chrome/Edge (latest 2 versions)
- Firefox (latest 2 versions)
- Safari (latest 2 versions)
- Mobile browsers (iOS Safari, Chrome Android)

## 🐛 Troubleshooting

### Database Connection Errors
```bash
# Check MySQL is running
mysql -u root -p

# Verify database exists
SHOW DATABASES;
USE crop_advisory;
SHOW TABLES;
```

### Image Upload Issues
- Check file size (max 5MB)
- Verify file format (JPG, PNG only)
- Ensure API key is configured
- Check network connectivity

### Plant.id API Errors
- Verify API key is valid
- Check API rate limits
- Ensure proper base64 encoding
- Review API documentation

## 📈 Future Enhancements
- [ ] User authentication and profiles
- [ ] Diagnosis history tracking
- [ ] PDF report generation
- [ ] Mobile app (React Native)
- [ ] Multi-language support
- [ ] Offline mode (PWA)
- [ ] Admin dashboard for knowledge base management
- [ ] Weather API integration
- [ ] Soil testing recommendations
- [ ] Market price integration

## 👨‍💻 Developer Information

**Developer**: Audi Peter Ojwang'  
**Student ID**: 23/04956  
**Institution**: KCA University  
**School**: School of Technology  
**Project**: Final Year Project - PlantWise Agricultural Expert System

## 📝 License

This project is developed as part of an academic final year project at KCA University.

## 🙏 Acknowledgments

- Plant.id API by Kindwise for disease identification
- Flask framework for backend
- MySQL for database management
- Anthropic's principles for fuzzy logic and expert systems
- Agricultural extension officers for domain expertise

## 📞 Support

For issues, questions, or contributions:
- Email: [your-email@example.com]
- GitHub Issues: [repository-url]/issues

---

**Note**: Remember to replace placeholder values (API keys, email, repository URL) with actual values before deployment.
