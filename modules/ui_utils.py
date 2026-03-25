"""
UI Utilities - Toast notifications i loading spinners
"""

def get_ui_components():
    """Zwraca HTML/CSS/JS dla UI components"""
    return '''
    <style>
    /* Toast notifications */
    .toast-container {
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 9999;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    
    .toast {
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 15px 20px;
        min-width: 300px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        animation: slideIn 0.3s ease;
        display: flex;
        align-items: center;
        gap: 12px;
    }
    
    .toast.success {
        border-color: var(--green);
        background: rgba(34, 197, 94, 0.1);
    }
    
    .toast.error {
        border-color: var(--red);
        background: rgba(239, 68, 68, 0.1);
    }
    
    .toast.info {
        border-color: var(--blue);
        background: rgba(59, 130, 246, 0.1);
    }
    
    .toast.warning {
        border-color: var(--yellow);
        background: rgba(251, 191, 36, 0.1);
    }
    
    .toast-icon {
        font-size: 1.5rem;
    }
    
    .toast-content {
        flex: 1;
    }
    
    .toast-title {
        font-weight: 600;
        margin-bottom: 2px;
    }
    
    .toast-message {
        font-size: 0.85rem;
        opacity: 0.8;
    }
    
    .toast-close {
        background: none;
        border: none;
        color: var(--text);
        cursor: pointer;
        font-size: 1.2rem;
        opacity: 0.5;
        transition: opacity 0.2s;
    }
    
    .toast-close:hover {
        opacity: 1;
    }
    
    @keyframes slideIn {
        from {
            transform: translateX(400px);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(400px);
            opacity: 0;
        }
    }
    
    /* Loading spinner overlay */
    .loading-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(10, 10, 15, 0.8);
        backdrop-filter: blur(4px);
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 9998;
    }
    
    .loading-overlay.active {
        display: flex;
    }
    
    .spinner {
        width: 60px;
        height: 60px;
        border: 4px solid rgba(139, 92, 246, 0.2);
        border-top-color: var(--purple);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }
    
    .loading-text {
        position: absolute;
        margin-top: 100px;
        color: var(--text);
        font-weight: 600;
    }
    
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    /* Inline spinner (małe spinnery w przyciskach) */
    .btn-spinner {
        display: inline-block;
        width: 14px;
        height: 14px;
        border: 2px solid rgba(255,255,255,0.3);
        border-top-color: white;
        border-radius: 50%;
        animation: spin 0.6s linear infinite;
        margin-right: 8px;
        vertical-align: middle;
    }
    </style>
    
    <div class="toast-container" id="toastContainer"></div>
    <div class="loading-overlay" id="loadingOverlay">
        <div>
            <div class="spinner"></div>
            <div class="loading-text" id="loadingText">Ładowanie...</div>
        </div>
    </div>
    
    <script>
    // Toast notification system
    const Toast = {
        show: function(message, type = 'info', duration = 3000) {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            
            const icons = {
                success: '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span>',
                error: '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">cancel</span>',
                info: '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#3b82f6">info</span>',
                warning: '<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">warning</span>'
            };
            
            toast.innerHTML = `
                <div class="toast-icon">${icons[type]}</div>
                <div class="toast-content">
                    <div class="toast-message">${message}</div>
                </div>
                <button class="toast-close" onclick="this.parentElement.remove()">×</button>
            `;
            
            container.appendChild(toast);
            
            setTimeout(() => {
                toast.style.animation = 'slideOut 0.3s ease';
                setTimeout(() => toast.remove(), 300);
            }, duration);
        },
        
        success: function(message, duration) {
            this.show(message, 'success', duration);
        },
        
        error: function(message, duration) {
            this.show(message, 'error', duration);
        },
        
        info: function(message, duration) {
            this.show(message, 'info', duration);
        },
        
        warning: function(message, duration) {
            this.show(message, 'warning', duration);
        }
    };
    
    // Loading overlay system
    const Loading = {
        show: function(text = 'Ładowanie...') {
            const overlay = document.getElementById('loadingOverlay');
            const textEl = document.getElementById('loadingText');
            textEl.textContent = text;
            overlay.classList.add('active');
        },
        
        hide: function() {
            const overlay = document.getElementById('loadingOverlay');
            overlay.classList.remove('active');
        }
    };
    
    // Automatyczne zastąpienie alertów toastami
    window.originalAlert = window.alert;
    window.alert = function(message) {
        if (message.includes('<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#22c55e">check_circle</span>') || message.toLowerCase().includes('sukces')) {
            Toast.success(message);
        } else if (message.includes('<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle;color:#ef4444">cancel</span>') || message.toLowerCase().includes('błąd')) {
            Toast.error(message);
        } else if (message.includes('<span class="material-symbols-outlined" style="font-size:inherit;vertical-align:middle">warning</span>')) {
            Toast.warning(message);
        } else {
            Toast.info(message);
        }
    };
    
    // Helper do pokazywania spinnera w przycisku
    function showButtonSpinner(button, originalText) {
        button.disabled = true;
        button.dataset.originalText = originalText || button.innerHTML;
        button.innerHTML = '<span class="btn-spinner"></span>' + button.textContent;
    }
    
    function hideButtonSpinner(button) {
        button.disabled = false;
        if (button.dataset.originalText) {
            button.innerHTML = button.dataset.originalText;
        }
    }
    
    // Automatyczne loadery dla formularzy
    document.addEventListener('submit', function(e) {
        const form = e.target;
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn && !submitBtn.dataset.noSpinner) {
            showButtonSpinner(submitBtn);
        }
    });
    </script>
    '''

def add_ui_to_template(html_content):
    """Dodaje UI components do każdego template"""
    ui_components = get_ui_components()
    # Dodaj przed zamykającym </body> lub na końcu
    if '</body>' in html_content:
        return html_content.replace('</body>', ui_components + '</body>')
    else:
        return html_content + ui_components
