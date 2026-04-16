/**
 * PlantWise - Disease Diagnosis JavaScript
 * Handles diagnosis method selection and form management
 */

let selectedMethod = null;

/**
 * Select diagnosis method
 */
function selectDiagnosisMethod(method) {
    selectedMethod = method;
    
    // Hide options, show form
    document.querySelector('.diagnosis-options').classList.add('hidden');
    document.getElementById('diagnosis-form-container').classList.remove('hidden');
    
    // Show/hide relevant sections based on method
    const imageSection = document.getElementById('image-upload-section');
    const symptomSection = document.getElementById('symptom-selection-section');
    
    if (method === 'symptom') {
        imageSection.classList.add('hidden');
        symptomSection.classList.remove('hidden');
    } else if (method === 'hybrid') {
        imageSection.classList.remove('hidden');
        symptomSection.classList.add('hidden');
    }
    
    // Scroll to form
    setTimeout(() => {
        document.getElementById('diagnosis-form-container').scrollIntoView({ 
            behavior: 'smooth', 
            block: 'start' 
        });
    }, 100);
}

/**
 * Select plant part
 */
function selectPlantPart(card) {
    // Remove selected class from all cards
    const cards = document.querySelectorAll('#symptom-selection-section .selection-card');
    cards.forEach(c => c.classList.remove('selected'));
    
    // Add selected class to clicked card
    card.classList.add('selected');
    
    // Update hidden input
    document.getElementById('plant-part').value = card.dataset.value;
    
    // Load symptoms for this plant part
    loadSymptomsForPlantPart(card.dataset.value);
}

/**
 * Load symptoms based on plant part
 * This is a placeholder - you would fetch symptoms from your database
 */
function loadSymptomsForPlantPart(plantPart) {
    const container = document.getElementById('symptoms-container');
    
    // Sample symptoms - replace with actual API call
    const symptoms = {
        'Leaf': [
            { id: 'yellow_spots', name: 'Yellow spots on leaves', part: 'Leaf' },
            { id: 'brown_edges', name: 'Brown edges on leaves', part: 'Leaf' },
            { id: 'white_powder', name: 'White powdery coating', part: 'Leaf' },
            { id: 'wilting', name: 'Wilting or drooping', part: 'Leaf' }
        ],
        'Stem': [
            { id: 'stem_rot', name: 'Stem rotting or soft', part: 'Stem' },
            { id: 'stem_lesions', name: 'Dark lesions on stem', part: 'Stem' },
            { id: 'stem_discolor', name: 'Stem discoloration', part: 'Stem' }
        ],
        'Root': [
            { id: 'root_rot', name: 'Root rot or decay', part: 'Root' },
            { id: 'root_galls', name: 'Swelling or galls on roots', part: 'Root' },
            { id: 'stunted_growth', name: 'Stunted plant growth', part: 'Root' }
        ],
        'Fruit': [
            { id: 'fruit_spots', name: 'Spots or lesions on fruit', part: 'Fruit' },
            { id: 'fruit_rot', name: 'Fruit rotting or mold', part: 'Fruit' },
            { id: 'fruit_deform', name: 'Deformed or misshapen fruit', part: 'Fruit' }
        ]
    };
    
    const relevantSymptoms = symptoms[plantPart] || [];
    
    // Clear existing symptoms
    container.innerHTML = '';
    
    // Add symptom cards
    relevantSymptoms.forEach(symptom => {
        const card = createSymptomCard(symptom);
        container.appendChild(card);
    });
}

/**
 * Create symptom card HTML
 */
function createSymptomCard(symptom) {
    const card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'margin-bottom: var(--spacing-md); padding: var(--spacing-md);';
    
    card.innerHTML = `
        <label style="display: flex; align-items: center; gap: var(--spacing-md); cursor: pointer;">
            <input type="checkbox" name="symptoms[]" value="${symptom.id}" 
                   style="width: 20px; height: 20px;" 
                   onchange="toggleSymptomConfidence(this, '${symptom.id}')">
            <div>
                <strong>${symptom.name}</strong>
                <p style="font-size: var(--font-size-sm); color: var(--text-medium); margin: 0;">${symptom.part}</p>
            </div>
        </label>
        <div id="confidence-${symptom.id}" class="hidden" style="margin-top: var(--spacing-md);">
            <label style="font-size: var(--font-size-sm); color: var(--text-medium);">Confidence Level:</label>
            <input type="range" class="range-slider" name="confidence_${symptom.id}" 
                   min="0" max="100" value="50" 
                   oninput="updateConfidenceValue('${symptom.id}', this.value)"
                   style="margin-top: var(--spacing-sm);">
            <p id="confidence-value-${symptom.id}" style="text-align: right; font-size: var(--font-size-sm); margin: var(--spacing-xs) 0 0 0;">50%</p>
        </div>
    `;
    
    return card;
}

/**
 * Toggle symptom confidence slider
 */
function toggleSymptomConfidence(checkbox, symptomId) {
    const confidenceDiv = document.getElementById(`confidence-${symptomId}`);
    if (checkbox.checked) {
        confidenceDiv.classList.remove('hidden');
    } else {
        confidenceDiv.classList.add('hidden');
    }
}

/**
 * Update confidence value display
 */
function updateConfidenceValue(symptomId, value) {
    document.getElementById(`confidence-value-${symptomId}`).textContent = `${value}%`;
}

/**
 * Submit diagnosis
 */
async function submitDiagnosis() {
    // Validate form
    const crop = document.getElementById('crop-select').value;
    if (!crop) {
        showToast('Please select a crop', 'error');
        return;
    }
    
    // Check if at least one method has data
    const hasImages = uploadedImages.length > 0;
    const hasSymptoms = document.querySelectorAll('input[name="symptoms[]"]:checked').length > 0;
    
    if (!hasImages && !hasSymptoms && selectedMethod !== 'symptom') {
        showToast('Please upload at least one image or select symptoms', 'error');
        return;
    }
    
    // Show loading
    document.getElementById('diagnosis-form-container').classList.add('hidden');
    document.getElementById('loading-state').classList.remove('hidden');
    
    // Prepare form data
    const formData = new FormData();
    formData.append('crop', crop);
    formData.append('method', selectedMethod);
    
    // Add images if present
    if (hasImages) {
        uploadedImages.forEach((imageData, index) => {
            formData.append(`image_${index}`, imageData.file);
        });
    }
    
    // Add symptoms if present
    if (hasSymptoms) {
        const symptoms = [];
        document.querySelectorAll('input[name="symptoms[]"]:checked').forEach(checkbox => {
            const symptomId = checkbox.value;
            const confidenceInput = document.querySelector(`input[name="confidence_${symptomId}"]`);
            const confidence = confidenceInput ? parseInt(confidenceInput.value) : 50;
            
            symptoms.push({
                id: symptomId,
                confidence: confidence
            });
        });
        formData.append('symptoms', JSON.stringify(symptoms));
    }
    
    // Add plant part if selected
    const plantPart = document.getElementById('plant-part').value;
    if (plantPart) {
        formData.append('plant_part', plantPart);
    }
    
    let progressInterval;
    try {
        // Start progress animation
        const progressBar = document.getElementById('loading-progress-bar');
        const progressText = document.getElementById('loading-percentage');
        let progress = 0;
        
        if (progressBar) progressBar.style.width = '0%';
        if (progressText) progressText.textContent = '0%';

        progressInterval = setInterval(() => {
            if (progress < 90) {
                // Slower progression as it gets higher
                const increment = Math.max(0.5, Math.random() * (90 - progress) * 0.15); 
                progress += increment;
                if (progress > 90) progress = 90;
                
                if (progressBar) progressBar.style.width = `${progress}%`;
                if (progressText) progressText.textContent = `${Math.floor(progress)}%`;
            }
        }, 500);

        // Submit to backend
        const response = await fetch('/api/diagnose', {
            method: 'POST',
            body: formData
        });
        
        clearInterval(progressInterval);
        
        if (!response.ok) {
            throw new Error('Diagnosis failed');
        }
        
        const result = await response.json();
        
        // Push to 100% on success
        if (progressBar) progressBar.style.width = '100%';
        if (progressText) progressText.textContent = '100%';
        
        // Redirect to results page
        // In a real implementation, you would pass the results or use session storage
        showToast('Diagnosis complete!', 'success');
        setTimeout(() => {
            window.location.href = '/diagnosis/results';
        }, 1000);
        
    } catch (error) {
        if (progressInterval) clearInterval(progressInterval);
        console.error('Diagnosis error:', error);
        showToast('Diagnosis failed. Please try again.', 'error');
        
        // Hide loading, show form again
        document.getElementById('loading-state').classList.add('hidden');
        document.getElementById('diagnosis-form-container').classList.remove('hidden');
    }
}

/**
 * Initialize diagnosis page
 */
document.addEventListener('DOMContentLoaded', () => {
    // Set up crop select change handler
    const cropSelect = document.getElementById('crop-select');
    if (cropSelect) {
        cropSelect.addEventListener('change', () => {
            // Could load crop-specific information here
            console.log('Selected crop:', cropSelect.value);
        });
    }
});
