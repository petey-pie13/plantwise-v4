/**
 * PlantWise - Enhanced Crop Advisory Wizard
 * Handles Geolocation, Weather Fetching, and Soil pH Profiling
 */

let currentStep = 1;
const totalSteps = 4;

// State object to hold all detected/selected data
const state = {
    latitude:      null,
    longitude:     null,
    county:        '',
    sub_county:    '',
    weather:       null, // { rainfall_cat, temp_cat, labels... }
    soil_profile:  null, // { typical_ph, type, description... }
    ph_used:       6.0,
    ph_confidence: 'county_default' 
};

document.addEventListener('DOMContentLoaded', () => {
    initGeolocation();
    initEventListeners();
    
    // Initial pH label display
    onPhSliderChange(6.0);
});

/**
 * ── STEP 1: GEOLOCATION ──────────────────────────────────────────────────────
 */
const KENYA_BOUNDS = {
    lat_min: -4.8,
    lat_max:  4.6,
    lon_min: 33.9,
    lon_max: 41.9
};

function isWithinKenya(lat, lon) {
    return (
        lat >= KENYA_BOUNDS.lat_min &&
        lat <= KENYA_BOUNDS.lat_max &&
        lon >= KENYA_BOUNDS.lon_min &&
        lon <= KENYA_BOUNDS.lon_max
    );
}

function initGeolocation() {
    console.log('[GEO] Starting location detection');
    updateLocationBanner('loading', 'Detecting your location...');

    if (!navigator.geolocation) {
        updateLocationBanner('warning', 'Geolocation not supported. Please select manually.');
        showManualConditions();
        return;
    }

    // Set a fail-safe timeout (10 seconds)
    const geoTimeout = setTimeout(() => {
        console.warn('[GEO] Timed out after 10 seconds');
        updateLocationBanner('warning', 'Location detection timed out. Please select manually.');
        showManualConditions();
    }, 10000);

    navigator.geolocation.getCurrentPosition(
        (pos) => {
            clearTimeout(geoTimeout);
            onLocationSuccess(pos);
        },
        (err) => {
            clearTimeout(geoTimeout);
            onLocationError(err);
        },
        { 
            timeout: 9000, 
            enableHighAccuracy: true,
            maximumAge: 60000
        }
    );
}

async function onLocationSuccess(pos) {
    const lat = pos.coords.latitude;
    const lon = pos.coords.longitude;
    
    console.log(`[GEO] Coordinates: ${lat}, ${lon} | Within Kenya: ${isWithinKenya(lat, lon)}`);

    // Guard: Reject if outside Kenya (prevents IP-based mis-detection results like Dublin)
    if (!isWithinKenya(lat, lon)) {
        console.warn('[GEO] Coordinates outside Kenya, rejecting.');
        updateLocationBanner('warning', 'Inaccurate location detected. Please select your county manually.');
        showManualConditions();
        return;
    }

    state.latitude  = lat;
    state.longitude = lon;
    document.getElementById('latitude').value  = lat;
    document.getElementById('longitude').value = lon;

    updateLocationBanner('loading', 'Identifying your area...');

    try {
        // Run lookups in parallel
        await Promise.all([
            reverseGeocode(lat, lon),
            fetchEcologicalConditions(lat, lon)
        ]);
    } catch (err) {
        console.error("Location lookup error:", err);
    }
}

async function reverseGeocode(lat, lon) {
    try {
        console.log(`[GEO] Reverse geocoding via backend: ${lat}, ${lon}`);

        const response = await fetch(`/api/reverse-geocode?lat=${lat}&lon=${lon}`);
        const data = await response.json();

        console.log('[GEO] Backend geocode response:', data);

        if (data.success && data.county) {
            const cleanCounty  = data.county;
            const detectedSub  = data.sub_county || '';
            console.log(`[GEO] County: "${cleanCounty}", Sub-county: "${detectedSub}"`);

            state.county     = cleanCounty;
            state.sub_county = detectedSub;

            // selectCountyDropdown now awaits sub-county load + auto-selects
            await selectCountyDropdown(cleanCounty, detectedSub);
            updateLocationBanner('success', `Location identified: ${cleanCounty}${detectedSub ? ', ' + detectedSub : ''}`);
            loadSoilProfile(cleanCounty);

            return {
                success:   true,
                county:    cleanCounty,
                subCounty: detectedSub,
                countyId:  data.county_id
            };
        } else {
            console.warn('[GEO] Geocode failed or county not in DB:', data);
            updateLocationBanner('warning', 'Could not detect county within our database. Please select manually.');
            return { success: false };
        }

    } catch (err) {
        console.error('[GEO] Reverse geocode request failed:', err);
        updateLocationBanner('warning', 'Location found, but area identification failed.');
        return { success: false };
    }
}

function onLocationError(err) {
    console.warn("Geolocation failed:", err);
    let userMsg = "Location access denied. Please select your county.";
    
    // Handle our custom out-of-bounds error from onLocationSuccess
    if (err.code === 0 && err.message === "Out of Bounds") {
        userMsg = "Detected location is outside Kenya. Please select manually.";
    } else if (err.code === err.TIMEOUT) {
        userMsg = "Location detection timed out. Please select manually.";
    } else if (err.code === err.POSITION_UNAVAILABLE) {
        userMsg = "Location information is unavailable. Please select manually.";
    }
    
    updateLocationBanner('warning', userMsg);
    showManualConditions();
}

function updateLocationBanner(type, text) {
    const banner = document.getElementById('location-status');
    const msg    = document.getElementById('location-message');
    
    banner.className = `location-banner ${type}`;
    msg.textContent  = text;
}

/**
 * ── STEP 2: ECOLOGICAL CONDITIONS ────────────────────────────────────────────
 * Fetches 30-day rainfall and temp patterns for the coordinates
 */
async function fetchWeather(lat, lon) {
    try {
        const res  = await fetch(`/api/ecological-conditions?lat=${lat}&lon=${lon}`);
        const data = await res.json();
        return data;
    } catch (err) {
        console.error("Weather fetch failed:", err);
        return { success: false };
    }
}

function populateWeatherFields(data) {
    if (!data || !data.success) {
        // Weather API unavailable – show manual form
        showManualConditions();
        return;
    }
    
    state.weather = data;
    
    // Auto-fill hidden inputs for Step 2
    document.getElementById('rainfall-value').value    = data.rainfall_category;
    document.getElementById('temperature-value').value = data.temp_category;
    
    // Update UI displays
    document.getElementById('display-rainfall').textContent    = data.rainfall_label;
    document.getElementById('display-temperature').textContent = data.temp_label;
    
    // Set dropdown fallbacks
    document.getElementById('rainfall').value    = data.rainfall_category;
    document.getElementById('temperature').value = data.temp_category;
    
    // Show the auto-card, hide manual unless requested
    document.getElementById('auto-conditions-card').style.display = 'block';
    document.getElementById('manual-conditions').style.display      = 'none';
}

async function fetchEcologicalConditions(lat, lon) {
    const data = await fetchWeather(lat, lon);
    if (!data || !data.success) {
        console.warn('[WEATHER] API unavailable, using manual selection');
        updateLocationBanner('warning', 'Weather data unavailable – please select conditions manually.');
        showManualConditions();
        return;
    }
    populateWeatherFields(data);
}

function showManualConditions() {
    document.getElementById('manual-conditions').style.display = 'block';
    document.getElementById('auto-conditions-card').style.display = 'none';
}

/**
 * ── STEP 3: SOIL PROFILING ───────────────────────────────────────────────────
 * Fetches typical soil data for the selected county
 */
async function loadSoilProfile(countyName) {
    try {
        const response = await fetch(
            `/api/county-soil-profile?county_name=` +
            `${encodeURIComponent(countyName)}`
        );
        const data = await response.json();

        if (!data.success) return;

        state.soil_profile    = data;
        state.countyTypicalPh = data.typical_ph;
        state.soilType        = data.soil_type;

        // Show soil info card
        const card = document.getElementById('soil-info-card');
        if (card) {
            document.getElementById('soil-type-display').textContent = data.soil_type;
            document.getElementById('soil-description-display').textContent = data.soil_description;
            card.style.display = 'block';
        }

        // Set pH slider to county default
        const slider = document.getElementById('soil_ph');
        slider.value = data.typical_ph;
        onPhSliderChange(data.typical_ph);

        // Show county pH note with fuzzy label
        const note = document.getElementById('ph-county-note');
        if (note) {
            note.innerHTML =
                `<strong>${countyName} County default:</strong> ` +
                `pH ${data.typical_ph} ` +
                `<span class="ph-fuzzy-inline" ` +
                `style="background:${data.fuzzy.typical.color}">` +
                `${data.fuzzy.typical.label}</span><br>` +
                `Typical range: ${data.ph_min} to ${data.ph_max} ` +
                `(${data.fuzzy.min.label} to ${data.fuzzy.max.label})<br>` +
                `<em>${data.liming_advice}</em>`;
        }

        useCountyDefaultPh();

        // ── KEY ADDITION ──────────────────────────────────────────
        // If geolocation did not give us coordinates,
        // use county centre coordinates to fetch weather instead.
        if (!state.latitude && !state.longitude) {
            if (data.latitude && data.longitude) {
                console.log(
                    `[WEATHER] No GPS coords. Using county centre: ` +
                    `${data.latitude}, ${data.longitude}`
                );

                state.latitude  = data.latitude;
                state.longitude = data.longitude;

                document.getElementById('latitude').value  = data.latitude;
                document.getElementById('longitude').value = data.longitude;

                // Show loading indicator on weather section (assuming setWeatherLoading is defined elsewhere)
                if (typeof setWeatherLoading === 'function') {
                    setWeatherLoading(countyName);
                } else {
                    console.warn("setWeatherLoading function not found.");
                }

                // Fetch weather for county centre
                const weatherResult = await fetchWeather(
                    data.latitude,
                    data.longitude
                );

                if (weatherResult && weatherResult.success) {
                    populateWeatherFields(weatherResult);
                    console.log(
                        `[WEATHER] County weather loaded: ` +
                        `${weatherResult.rainfall_category} rain, ` +
                        `${weatherResult.temp_category} temp`
                    );
                } else {
                    console.warn('[WEATHER] County weather fetch failed');
                    showManualConditions();
                }
            } else {
                // No coordinates available at all, show manual
                showManualConditions();
            }
        }
    } catch (err) {
        console.error("Soil profile fetch error:", err);
    }
}

/**
 * ── pH & SLIDER LOGIC ────────────────────────────────────────────────────────
 */
function onPhSliderChange(val) {
    val = parseFloat(val);
    state.ph_used = val;
    
    document.getElementById('ph-number-display').textContent = val.toFixed(1);
    
    // Switch to manual mode if user slides
    if (state.ph_confidence === 'county_default') {
        useManualPh();
    }
    
    updatePhFuzzyUI(val);
}

async function updatePhFuzzyUI(ph) {
    // We can use a small local logic for immediate feedback, 
    // or call the backend helper. We'll do a quick local version 
    // that matches the backend logic.
    const badges = {
        acidic:   { label: 'Strongly Acidic', color: '#e74c3c' },
        neutral:  { label: 'Near Neutral',    color: '#2ecc71' },
        alkaline: { label: 'Alkaline',        color: '#3498db' }
    };
    
    let label = 'Near Neutral';
    let color = '#27ae60';
    
    if (ph < 4.5) { label = 'Extremely Acidic'; color = '#c0392b'; }
    else if (ph < 5.5) { label = 'Strongly Acidic'; color = '#e67e22'; }
    else if (ph < 6.5) { label = 'Slightly Acidic'; color = '#f1c40f'; }
    else if (ph < 7.5) { label = 'Neutral'; color = '#2ecc71'; }
    else if (ph < 8.5) { label = 'Slightly Alkaline'; color = '#3498db'; }
    else { label = 'Strongly Alkaline'; color = '#2980b9'; }

    const badge = document.getElementById('ph-fuzzy-badge');
    badge.style.backgroundColor = color;
    document.getElementById('ph-fuzzy-label').textContent = label;
}

function useCountyDefaultPh() {
    state.ph_confidence = 'county_default';
    document.getElementById('ph-confidence').value = 'county_default';
    
    document.getElementById('btn-use-county-ph').classList.add('active');
    document.getElementById('btn-use-manual-ph').classList.remove('active');
    
    if (state.soil_profile) {
        const typical = state.soil_profile.typical_ph;
        document.getElementById('soil_ph').value = typical;
        onPhSliderChange(typical);
        // Reset to default after the slider change forced manual
        state.ph_confidence = 'county_default';
        document.getElementById('ph-confidence').value = 'county_default';
    }
}

function setWeatherLoading(countyName) {
    // Show a brief loading state while weather fetches
    // for the manually selected county
    const autoCard = document.getElementById('auto-conditions-card');
    const manual   = document.getElementById('manual-conditions');

    if (autoCard) {
        autoCard.style.display = 'block';
        autoCard.innerHTML = `
            <div class="conditions-card loading">
                <p>Fetching current conditions for 
                   <strong>${countyName}</strong>...</p>
            </div>
        `;
    }
    if (manual) {
        manual.style.display = 'none';
    }
}

function useManualPh() {
    state.ph_confidence = 'manual_test';
    document.getElementById('ph-confidence').value = 'manual_test';
    
    document.getElementById('btn-use-county-ph').classList.remove('active');
    document.getElementById('btn-use-manual-ph').classList.add('active');
}

/**
 * ── NAVIGATION & STEPS ───────────────────────────────────────────────────────
 */
function nextStep() {
    if (validateStep(currentStep)) {
        document.getElementById(`step-${currentStep}`).style.display = 'none';
        currentStep++;
        document.getElementById(`step-${currentStep}`).style.display = 'block';
        updateUI();
    }
}

function prevStep() {
    document.getElementById(`step-${currentStep}`).style.display = 'none';
    currentStep--;
    document.getElementById(`step-${currentStep}`).style.display = 'block';
    updateUI();
}

function updateUI() {
    // Progress bar
    for (let i = 1; i <= totalSteps; i++) {
        const bar = document.getElementById(`progress-${i}`);
        bar.className = (i <= currentStep) ? 'progress-bar active' : 'progress-bar';
    }
    document.getElementById('progress-text').textContent = `Step ${currentStep} of ${totalSteps}`;

    // Buttons
    document.getElementById('prev-btn').style.display = (currentStep === 1) ? 'none' : 'block';
    document.getElementById('next-btn').style.display = (currentStep === totalSteps) ? 'none' : 'block';
    document.getElementById('btn-get-recommendations').style.display = (currentStep === totalSteps) ? 'block' : 'none';

    // Summary data
    if (currentStep === totalSteps) {
        populateReview();
    }
}

function populateReview() {
    const county   = document.getElementById('county').value;
    const rainfall = document.getElementById('rainfall').value || state.weather?.rainfall_category || 'Not selected';
    const temp     = document.getElementById('temperature').value || state.weather?.temp_category || 'Not selected';

    // Build richer display strings when auto-detected data is available
    let rainfallDisplay = rainfall;
    let tempDisplay     = temp;

    if (state.weather) {
        const mm  = state.weather.rainfall_30d_mm;
        const deg = state.weather.avg_temp_c;
        rainfallDisplay = `${state.weather.rainfall_category} — ${mm}mm in last 30 days`;
        tempDisplay     = `${state.weather.temp_category} — avg ${deg}°C`;
    }

    document.getElementById('review-location').textContent    = county;
    document.getElementById('review-rainfall').textContent    = rainfallDisplay;
    document.getElementById('review-temperature').textContent = tempDisplay;
    document.getElementById('review-ph').textContent          = `${state.ph_used} (${state.ph_confidence === 'county_default' ? 'Estimated' : 'Manual'})`;
    document.getElementById('review-soil-type').textContent   = state.soil_profile?.soil_type || 'General';
    document.getElementById('review-source').textContent      = state.weather
        ? `Auto-detected via GPS (${state.weather.source})`
        : 'Manual Selection';
}


function validateStep(step) {
    if (step === 1) {
        if (!document.getElementById('county').value) {
            alert("Please select your county first.");
            return false;
        }
    }
    if (step === 2) {
        const r = document.getElementById('rainfall').value || document.getElementById('rainfall-value').value;
        const t = document.getElementById('temperature').value || document.getElementById('temperature-value').value;
        if (!r || !t) {
            alert("Please provide environmental conditions.");
            return false;
        }
        // Sync values to form
        if (!document.getElementById('rainfall').value) document.getElementById('rainfall').value = r;
        if (!document.getElementById('temperature').value) document.getElementById('temperature').value = t;
    }
    return true;
}

/**
 * ── SUBMISSION ───────────────────────────────────────────────────────────────
 */
async function submitAdvisory() {
    const btn = document.getElementById('btn-get-recommendations');
    const originalText = btn.innerHTML;
    
    btn.disabled  = true;
    btn.innerHTML = '<span class="loading-spinner"></span> Generating Report...';

    const formData = {
        county:        document.getElementById('county').value,
        sub_county:    document.getElementById('sub_county').value,
        soil_ph:       state.ph_used,
        ph_confidence: state.ph_confidence,
        rainfall:      document.getElementById('rainfall').value,
        temperature:   document.getElementById('temperature').value,
        latitude:      state.latitude,
        longitude:     state.longitude
    };

    try {
        const response = await fetch('/api/recommendations', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(formData)
        });
        
        const data = await response.json();
        
        if (data.success) {
            // Save results to session storage to show on results page
            sessionStorage.setItem('advisory_results', JSON.stringify(data));
            window.location.href = '/advisory/results';
        } else {
            alert("Error: " + data.error);
            btn.disabled  = false;
            btn.innerHTML = originalText;
        }
    } catch (err) {
        console.error("Submission failed:", err);
        alert("Failed to connect to server. Please try again.");
        btn.disabled  = false;
        btn.innerHTML = originalText;
    }
}

/**
 * ── HELPERS ──────────────────────────────────────────────────────────────────
 */
async function selectCountyDropdown(name, subCounty = '') {
    const select = document.getElementById('county');
    for (let i = 0; i < select.options.length; i++) {
        if (select.options[i].value.toLowerCase() === name.toLowerCase()) {
            select.selectedIndex = i;
            await loadSubCounties(select.options[i].value);
            if (subCounty) selectSubCountyDropdown(subCounty);
            return;
        }
    }
}

/**
 * Fuzzy-select the sub-county dropdown.
 * Nominatim may return "Roysambu division" while DB has "Roysambu".
 * Tries exact → contains → starts-with matching.
 */
function selectSubCountyDropdown(name) {
    if (!name) return;

    const select = document.getElementById('sub_county');
    const countyName = document.getElementById('county').value;
    
    // Clean name for matching
    const lower = name.toLowerCase().replace(/ division| sub.county/gi, '').trim();

    let bestIdx = -1;
    for (let i = 1; i < select.options.length; i++) {   // skip placeholder
        const opt = select.options[i].value.toLowerCase();
        if (opt === lower)              { bestIdx = i; break; }          // exact
        if (opt.includes(lower) || lower.includes(opt)) { bestIdx = i; } // contains
    }

    if (bestIdx > 0) {
        select.selectedIndex = bestIdx;
        console.log(`[GEO] Auto-selected existing sub-county: ${select.options[bestIdx].value}`);
    } else {
        // PER USER REQUIREMENT: Fill with detected result even if not in DB list
        // Guard: Don't add if it's the same as the county name
        if (name.toLowerCase() === countyName.toLowerCase()) {
            console.warn(`[GEO] Detected sub-county identical to county ("${name}"), skipping add.`);
            return;
        }

        console.log(`[GEO] No match in DB list, adding custom detected sub-county: "${name}"`);
        
        // Add new option
        const newOpt = document.createElement('option');
        newOpt.value = name;
        newOpt.textContent = `${name} (Detected)`;
        select.appendChild(newOpt);
        
        // Select it
        select.value = name;
        state.sub_county = name;
    }
}

async function loadSubCounties(county) {
    const subSelect = document.getElementById('sub_county');
    subSelect.innerHTML = '<option value="">Loading...</option>';

    try {
        const res  = await fetch(`/api/sub-counties?county=${encodeURIComponent(county)}`);
        const data = await res.json();

        subSelect.innerHTML = '<option value="">Select Sub-County</option>';
        if (data.success) {
            data.sub_counties.forEach(sc => {
                const opt = document.createElement('option');
                opt.value = opt.textContent = sc;
                subSelect.appendChild(opt);
            });
        }
    } catch (err) {
        subSelect.innerHTML = '<option value="">Error loading</option>';
    }
}

function initEventListeners() {
    document.getElementById('county').addEventListener('change', (e) => {
        const county = e.target.value;
        if (county) {
            // Clear GPS coordinates if user manually overrides selection
            state.latitude = null;
            state.longitude = null;
            loadSubCounties(county);
            loadSoilProfile(county);
        }
    });
}
