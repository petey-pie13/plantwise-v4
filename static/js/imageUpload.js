/**
 * PlantWise - Image Upload Handler
 * Handles image uploads and Plant.id API integration
 */

// Store uploaded images
let uploadedImages = [];
const MAX_IMAGES = 3;
const MAX_SIZE = 5 * 1024 * 1024; // 5MB

/**
 * Initialize image upload functionality
 */
document.addEventListener('DOMContentLoaded', () => {
    const uploadZone = document.getElementById('upload-zone');
    const imageInput = document.getElementById('image-input');
    
    if (!uploadZone || !imageInput) return;
    
    // Click to upload
    uploadZone.addEventListener('click', (e) => {
        if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'INPUT') {
            imageInput.click();
        }
    });
    
    // File input change
    imageInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
    });
    
    // Drag and drop
    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('drag-over');
    });
    
    uploadZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');
    });
    
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');
        handleFiles(e.dataTransfer.files);
    });
});

/**
 * Handle file selection
 */
function handleFiles(files) {
    const fileArray = Array.from(files);
    
    // Check if we can add more images
    const remainingSlots = MAX_IMAGES - uploadedImages.length;
    if (remainingSlots === 0) {
        showToast('Maximum 3 images allowed', 'warning');
        return;
    }
    
    // Process files
    const filesToProcess = fileArray.slice(0, remainingSlots);
    
    filesToProcess.forEach(file => {
        if (validateImage(file)) {
            addImage(file);
        }
    });
}

/**
 * Validate image file
 */
function validateImage(file) {
    // Check file type
    const validTypes = ['image/jpeg', 'image/jpg', 'image/png'];
    if (!validTypes.includes(file.type)) {
        showToast(`${file.name}: Only JPG and PNG images are allowed`, 'error');
        return false;
    }

    // Check file size
    if (file.size > MAX_SIZE) {
        showToast(`${file.name}: File size must be less than 5MB`, 'error');
        return false;
    }

    return true;
}

/**
 * Add image to upload list
 */
function addImage(file) {
    // Create image data object
    const imageData = {
        id: Date.now() + Math.random(),
        file: file,
        preview: null
    };
    
    // Create preview
    const reader = new FileReader();
    reader.onload = (e) => {
        imageData.preview = e.target.result;
        uploadedImages.push(imageData);
        updateImagePreview();
        updateImageCount();
        
        // Compress image if needed
        compressImage(imageData);
    };
    reader.readAsDataURL(file);
}

/**
 * Update image preview grid
 */
function updateImagePreview() {
    const grid = document.getElementById('image-preview-grid');
    grid.innerHTML = '';
    
    uploadedImages.forEach((imageData, index) => {
        const preview = document.createElement('div');
        preview.style.cssText = `
            position: relative;
            border-radius: var(--radius-md);
            overflow: hidden;
            background-color: var(--bg-light);
            aspect-ratio: 1;
        `;
        
        preview.innerHTML = `
            <img src="${imageData.preview}" 
                 style="width: 100%; height: 100%; object-fit: cover;">
            <button type="button" 
                    onclick="removeImage(${imageData.id})"
                    style="position: absolute; top: 8px; right: 8px; 
                           background: var(--error-red); color: white; 
                           border: none; border-radius: 50%; 
                           width: 32px; height: 32px; 
                           cursor: pointer; font-size: 18px;
                           display: flex; align-items: center; justify-content: center;
                           box-shadow: var(--shadow-md);">
                ×
            </button>
            <div style="position: absolute; bottom: 8px; left: 8px; 
                        background: rgba(0,0,0,0.7); color: white; 
                        padding: 4px 8px; border-radius: 4px; 
                        font-size: 12px;">
                ${index + 1}
            </div>
        `;
        
        grid.appendChild(preview);
    });
}

/**
 * Update image count display
 */
function updateImageCount() {
    const countElement = document.getElementById('image-count');
    if (countElement) {
        countElement.textContent = `${uploadedImages.length}/${MAX_IMAGES} images`;
    }
}

/**
 * Remove image from upload list
 */
function removeImage(imageId) {
    uploadedImages = uploadedImages.filter(img => img.id !== imageId);
    updateImagePreview();
    updateImageCount();
    
    // Clear file input
    const imageInput = document.getElementById('image-input');
    if (imageInput) {
        imageInput.value = '';
    }
}

/**
 * Compress image to reduce file size
 */
function compressImage(imageData) {
    const img = new Image();
    img.onload = () => {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        
        // Calculate new dimensions (max 1024px)
        let width = img.width;
        let height = img.height;
        const maxDimension = 1024;
        
        if (width > height && width > maxDimension) {
            height = (height * maxDimension) / width;
            width = maxDimension;
        } else if (height > maxDimension) {
            width = (width * maxDimension) / height;
            height = maxDimension;
        }
        
        canvas.width = width;
        canvas.height = height;
        
        // Draw and compress
        ctx.drawImage(img, 0, 0, width, height);
        
        canvas.toBlob((blob) => {
            if (blob && blob.size < imageData.file.size) {
                // Update file with compressed version
                imageData.file = new File([blob], imageData.file.name, {
                    type: 'image/jpeg',
                    lastModified: Date.now()
                });
                console.log(`Compressed ${imageData.file.name}: ${(blob.size / 1024).toFixed(2)} KB`);
            }
        }, 'image/jpeg', 0.85);
    };
    
    img.src = imageData.preview;
}

/**
 * Convert image to base64 for API
 */
function imageToBase64(imageData) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            // Remove data URL prefix
            const base64 = reader.result.split(',')[1];
            resolve(base64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(imageData.file);
    });
}

/**
 * Call Plant.id API for disease identification
 */
async function identifyDiseaseWithPlantID(images) {
    try {
        // Convert images to base64
        const base64Images = await Promise.all(
            images.map(img => imageToBase64(img))
        );
        
        // Prepare request payload
        const payload = {
            images: base64Images,
            modifiers: ["crops_fast", "similar_images"],
            plant_details: ["common_names", "url", "wiki_description"],
            disease_details: ["cause", "treatment", "description"]
        };
        
        // Call Plant.id API
        const response = await fetch('https://api.plant.id/v2/health_assessment', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Api-Key': 'YOUR_PLANT_ID_API_KEY_HERE' // Replace with actual API key
            },
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) {
            throw new Error(`Plant.id API error: ${response.status}`);
        }
        
        const data = await response.json();
        return parseAPIResponse(data);
        
    } catch (error) {
        console.error('Plant.id API error:', error);
        throw error;
    }
}

/**
 * Parse Plant.id API response
 */
function parseAPIResponse(apiResponse) {
    const diseases = [];
    
    if (apiResponse.health_assessment && apiResponse.health_assessment.diseases) {
        apiResponse.health_assessment.diseases.forEach(disease => {
            diseases.push({
                name: disease.name,
                probability: Math.round(disease.probability * 100),
                description: disease.disease_details ? disease.disease_details.description : '',
                cause: disease.disease_details ? disease.disease_details.cause : '',
                treatment: disease.disease_details ? disease.disease_details.treatment : [],
                similar_images: disease.similar_images || []
            });
        });
    }
    
    return {
        success: true,
        diseases: diseases.slice(0, 3), // Top 3 diseases
        source: 'Plant.id API'
    };
}

/**
 * Show upload progress
 */
function showUploadProgress() {
    const progressDiv = document.getElementById('upload-progress');
    const progressBar = document.getElementById('upload-progress-bar');
    
    if (progressDiv && progressBar) {
        progressDiv.classList.remove('hidden');
        
        // Simulate progress
        let progress = 0;
        const interval = setInterval(() => {
            progress += 10;
            progressBar.style.width = `${progress}%`;
            
            if (progress >= 100) {
                clearInterval(interval);
                setTimeout(() => {
                    progressDiv.classList.add('hidden');
                    progressBar.style.width = '0%';
                }, 500);
            }
        }, 200);
    }
}

/**
 * Handle API errors gracefully
 */
function handleAPIError(error) {
    console.error('API Error:', error);
    
    // Show user-friendly error message
    if (error.message.includes('API key')) {
        showToast('Please configure Plant.id API key', 'error');
    } else if (error.message.includes('network')) {
        showToast('Network error. Please check your connection.', 'error');
    } else {
        showToast('Image analysis failed. Using symptom-based diagnosis.', 'warning');
    }
}

// Export functions for use in diagnosis.js
if (typeof window !== 'undefined') {
    window.uploadedImages = uploadedImages;
    window.removeImage = removeImage;
    window.identifyDiseaseWithPlantID = identifyDiseaseWithPlantID;
    window.handleAPIError = handleAPIError;
}
