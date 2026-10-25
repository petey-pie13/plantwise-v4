/**
 * PlantWise Modern Toast Notification & Universal Safe Fetch Engine
 * Accessible (WCAG AA), CSP-compliant, Dependency-free, Offline-aware.
 */
(function() {
  'use strict';

  // SVG Icons for each toast category
  const ICONS = {
    success: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>',
    error: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>',
    warning: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>',
    info: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>'
  };

  const DURATIONS = {
    success: 4000,
    info: 4500,
    warning: 7000,
    error: 9500
  };

  const MAX_VISIBLE = 3;
  const queue = [];
  let visibleCount = 0;
  const activeMessages = new Set();

  function getContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      document.body.appendChild(container);
    }
    return container;
  }

  function createToastElement(type, message, action) {
    const toast = document.createElement('div');
    toast.className = `plantwise-toast toast-${type}`;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toast.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');

    // Icon
    const iconSpan = document.createElement('span');
    iconSpan.className = 'toast-icon';
    iconSpan.innerHTML = ICONS[type] || ICONS.info;
    toast.appendChild(iconSpan);

    // Content container
    const contentDiv = document.createElement('div');
    contentDiv.className = 'toast-content';

    const msgP = document.createElement('p');
    msgP.className = 'toast-message';
    msgP.textContent = message; // Safe textContent to prevent XSS
    contentDiv.appendChild(msgP);

    if (action && action.text && typeof action.onClick === 'function') {
      const actionBtn = document.createElement('button');
      actionBtn.type = 'button';
      actionBtn.className = 'toast-action-btn';
      actionBtn.textContent = action.text;
      actionBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        action.onClick();
        dismiss(toast);
      });
      contentDiv.appendChild(actionBtn);
    }
    toast.appendChild(contentDiv);

    // Close button (44px target)
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'toast-close-btn';
    closeBtn.setAttribute('aria-label', 'Close notification');
    closeBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
    closeBtn.addEventListener('click', () => dismiss(toast));
    toast.appendChild(closeBtn);

    return toast;
  }

  function show(type, message, action) {
    if (!message) return;

    // Deduplicate rapid identical messages
    const msgKey = `${type}:${message}`;
    if (activeMessages.has(msgKey)) return;

    if (visibleCount >= MAX_VISIBLE) {
      queue.push({ type, message, action });
      return;
    }

    activeMessages.add(msgKey);
    visibleCount++;

    const container = getContainer();
    const toast = createToastElement(type, message, action);
    container.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => {
      toast.classList.add('show');
    });

    let dismissTimer = null;
    let remainingTime = DURATIONS[type] || 5000;
    let startTime = Date.now();

    function startTimer() {
      startTime = Date.now();
      dismissTimer = setTimeout(() => {
        dismiss(toast, msgKey);
      }, remainingTime);
    }

    function pauseTimer() {
      if (dismissTimer) {
        clearTimeout(dismissTimer);
        dismissTimer = null;
        remainingTime -= (Date.now() - startTime);
        if (remainingTime <= 0) remainingTime = 1000;
      }
    }

    // Hover / Touch pause
    toast.addEventListener('mouseenter', pauseTimer);
    toast.addEventListener('mouseleave', startTimer);
    toast.addEventListener('touchstart', pauseTimer, { passive: true });
    toast.addEventListener('touchend', startTimer, { passive: true });

    // Mobile swipe to dismiss
    let touchStartX = 0;
    let touchCurrentX = 0;
    toast.addEventListener('touchstart', (e) => {
      touchStartX = e.touches[0].clientX;
    }, { passive: true });

    toast.addEventListener('touchmove', (e) => {
      touchCurrentX = e.touches[0].clientX;
      const diffX = touchCurrentX - touchStartX;
      if (diffX > 0) {
        toast.style.transform = `translateX(${diffX}px)`;
        toast.style.opacity = `${Math.max(0.2, 1 - (diffX / 200))}`;
      }
    }, { passive: true });

    toast.addEventListener('touchend', () => {
      const diffX = touchCurrentX - touchStartX;
      if (diffX > 80) {
        dismiss(toast, msgKey);
      } else {
        toast.style.transform = '';
        toast.style.opacity = '';
      }
    });

    startTimer();
  }

  function dismiss(toast, msgKey) {
    if (!toast || toast.classList.contains('hide')) return;
    toast.classList.remove('show');
    toast.classList.add('hide');

    setTimeout(() => {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
      if (msgKey) activeMessages.delete(msgKey);
      visibleCount--;
      if (queue.length > 0) {
        const next = queue.shift();
        show(next.type, next.message, next.action);
      }
    }, 250);
  }

  // Keyboard dismiss (Esc)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const container = document.getElementById('toast-container');
      if (container && container.lastElementChild) {
        dismiss(container.lastElementChild);
      }
    }
  });

  // Global Toast API
  window.toast = {
    success: (msg, action) => show('success', msg, action),
    error:   (msg, action) => show('error', msg, action),
    warning: (msg, action) => show('warning', msg, action),
    info:    (msg, action) => show('info', msg, action)
  };

  // Offline / Online awareness
  window.addEventListener('offline', () => {
    window.toast.warning("You're offline. Check your internet connection.");
  });

  window.addEventListener('online', () => {
    window.toast.success("Back online. Connected to network.");
  });

  // Universal Safe Fetch Wrapper with CSRF and sanitized friendly error handling
  window.plantwiseFetch = async function(url, options = {}) {
    options = options || {};
    options.headers = options.headers || {};

    // Auto-inject CSRF token for state-changing requests
    const method = (options.method || 'GET').toUpperCase();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
                     || document.querySelector('input[name="csrf_token"]')?.value;
      if (csrfToken && !options.headers['X-CSRFToken'] && !options.headers['X-CSRF-Token']) {
        options.headers['X-CSRFToken'] = csrfToken;
      }
    }

    if (!navigator.onLine) {
      window.toast.warning("We couldn't connect. Check your internet and try again.");
      throw new Error("Offline");
    }

    try {
      const response = await fetch(url, options);

      if (response.status === 401 || response.status === 403) {
        window.toast.error("Please sign in to continue.");
        return response;
      }

      if (response.status === 429) {
        window.toast.warning("Too many attempts. Please wait a moment and try again.");
        return response;
      }

      if (response.status >= 500) {
        window.toast.error("Something went wrong. Please try again.");
        return response;
      }

      return response;
    } catch (err) {
      if (err.name === 'AbortError') {
        window.toast.warning("Request timed out. Please try again.");
      } else if (!navigator.onLine || err.message === 'Failed to fetch') {
        window.toast.error("We couldn't connect. Check your internet and try again.");
      } else {
        window.toast.error("Something went wrong. Please try again.");
      }
      throw err;
    }
  };

  // Page Load: Flush server-rendered Flash Messages into Toasts
  document.addEventListener('DOMContentLoaded', () => {
    const flashCarrier = document.getElementById('plantwise-flash-carrier');
    if (flashCarrier) {
      try {
        const flashes = JSON.parse(flashCarrier.textContent || '[]');
        flashes.forEach(([category, message]) => {
          let type = 'info';
          if (category === 'success') type = 'success';
          else if (category === 'error' || category === 'danger') type = 'error';
          else if (category === 'warning') type = 'warning';
          window.toast[type](message);
        });
      } catch (e) {
        // Fallback gracefully
      }
    }

    // Hide no-js fallback block since JS is active
    const noJsBlock = document.getElementById('no-js-flash-fallback');
    if (noJsBlock) {
      noJsBlock.style.display = 'none';
    }
  });

})();
