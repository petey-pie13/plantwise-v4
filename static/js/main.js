/**
 * PlantWise - Main JavaScript File
 * Common utilities and functions
 */

// API Base URL
const API_BASE_URL = window.location.origin;

/**
 * Show loading spinner
 */
function showLoading() {
    const loadingHTML = `
        <div id="loading-overlay" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(255,255,255,0.95); z-index: 9999; display: flex; align-items: center; justify-content: center; flex-direction: column;">
            <div class="loading-spinner"></div>
            <p class="loading-text" style="margin-top: var(--spacing-lg);">Processing...</p>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', loadingHTML);
}

/**
 * Hide loading spinner
 */
function hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
        overlay.remove();
    }
}

/**
 * Show toast notification
 */
function showToast(message, type = 'info') {
    const colors = {
        success: 'var(--success-green)',
        error: 'var(--error-red)',
        warning: 'var(--warning-yellow)',
        info: 'var(--info-blue)'
    };

    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: ${colors[type] || colors.info};
        color: white;
        padding: var(--spacing-md) var(--spacing-xl);
        border-radius: var(--radius-lg);
        box-shadow: var(--shadow-xl);
        z-index: 10000;
        animation: slideIn 0.3s ease-out;
        max-width: 400px;
    `;
    toast.textContent = message;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease-in';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * Validate form field
 */
function validateField(field, value) {
    if (!value || value.trim() === '') {
        return { valid: false, message: `${field} is required` };
    }
    return { valid: true };
}

/**
 * Validate email
 */
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * Format date
 */
function formatDate(date) {
    return new Date(date).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    });
}

/**
 * Debounce function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Smooth scroll to element
 */
function smoothScrollTo(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

/**
 * Local storage helpers
 */
const storage = {
    set: (key, value) => {
        try {
            localStorage.setItem(key, JSON.stringify(value));
            return true;
        } catch (e) {
            console.error('Error saving to localStorage:', e);
            return false;
        }
    },
    get: (key) => {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : null;
        } catch (e) {
            console.error('Error reading from localStorage:', e);
            return null;
        }
    },
    remove: (key) => {
        try {
            localStorage.removeItem(key);
            return true;
        } catch (e) {
            console.error('Error removing from localStorage:', e);
            return false;
        }
    },
    clear: () => {
        try {
            localStorage.clear();
            return true;
        } catch (e) {
            console.error('Error clearing localStorage:', e);
            return false;
        }
    }
};

/**
 * API call wrapper
 */
async function apiCall(endpoint, method = 'GET', data = null) {
    try {
        const options = {
            method: method,
            headers: {
                'Content-Type': 'application/json',
            }
        };

        if (data && (method === 'POST' || method === 'PUT')) {
            options.body = JSON.stringify(data);
        }

        const response = await fetch(`${API_BASE_URL}${endpoint}`, options);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API call error:', error);
        throw error;
    }
}

/**
 * Initialize tooltips (if needed)
 */
function initTooltips() {
    const tooltips = document.querySelectorAll('[data-tooltip]');
    tooltips.forEach(element => {
        element.addEventListener('mouseenter', (e) => {
            const tooltip = document.createElement('div');
            tooltip.className = 'tooltip';
            tooltip.textContent = e.target.dataset.tooltip;
            tooltip.style.cssText = `
                position: absolute;
                background: var(--text-dark);
                color: white;
                padding: var(--spacing-sm) var(--spacing-md);
                border-radius: var(--radius-md);
                font-size: var(--font-size-sm);
                white-space: nowrap;
                z-index: 1000;
                pointer-events: none;
            `;
            document.body.appendChild(tooltip);
            
            const rect = e.target.getBoundingClientRect();
            tooltip.style.top = `${rect.top - tooltip.offsetHeight - 8}px`;
            tooltip.style.left = `${rect.left + (rect.width - tooltip.offsetWidth) / 2}px`;
            
            e.target._tooltip = tooltip;
        });
        
        element.addEventListener('mouseleave', (e) => {
            if (e.target._tooltip) {
                e.target._tooltip.remove();
                delete e.target._tooltip;
            }
        });
    });
}

/**
 * Initialize on DOM ready
 */
document.addEventListener('DOMContentLoaded', () => {
    // Initialize tooltips
    initTooltips();
    
    // Add smooth scrolling to anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({ behavior: 'smooth' });
            }
        });
    });
    
    // Add animation class to cards when they come into view
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };
    
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('fade-in');
                observer.unobserve(entry.target);
            }
        });
    }, observerOptions);
    
    document.querySelectorAll('.card, .module-card, .results-card').forEach(el => {
        observer.observe(el);
    });
});

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        showLoading,
        hideLoading,
        showToast,
        validateField,
        validateEmail,
        storage,
        apiCall,
        debounce,
        smoothScrollTo
    };
}
