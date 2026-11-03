// Common utility functions
const showLoading = (elementId) => {
    const loading = document.getElementById(elementId);
    if (loading) loading.style.display = 'block';
};

const hideLoading = (elementId) => {
    const loading = document.getElementById(elementId);
    if (loading) loading.style.display = 'none';
};

const showError = (elementId, message) => {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = `Error: ${message}`;
        element.classList.add('error');
    }
};

const showResponse = (elementId, response) => {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = response;
        element.classList.remove('error');
    }
};

// Common form handling
const handleFormSubmit = async (formId, endpoint, dataKey, responseId, loadingId) => {
    const form = document.getElementById(formId);
    if (!form) {
        console.error(`Form with ID ${formId} not found`);
        return;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        console.log(`Form ${formId} submitted`);
        
        const input = form.querySelector('textarea');
        if (!input || !input.value.trim()) {
            alert('Please enter the required information');
            return;
        }
        
        const data = { [dataKey]: input.value.trim() };
        console.log(`Sending data to ${endpoint}:`, data);
        showLoading(loadingId);
        showResponse(responseId, '');
        
        try {
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data),
            });
            
            console.log(`Response status: ${res.status}`);
            const responseText = await res.text();
            console.log('Raw response:', responseText);
            
            if (!res.ok) {
                let errorMessage;
                try {
                    const errorData = JSON.parse(responseText);
                    errorMessage = errorData.detail || 'Failed to process request';
                } catch (e) {
                    errorMessage = responseText || 'Failed to process request';
                }
                throw new Error(errorMessage);
            }
            
            const responseData = JSON.parse(responseText);
            console.log('Parsed response:', responseData);
            showResponse(responseId, responseData.response || JSON.stringify(responseData, null, 2));
        } catch (error) {
            console.error('Error details:', error);
            showError(responseId, error.message);
        } finally {
            hideLoading(loadingId);
        }
    });
};

// Initialize forms when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // Legal Assistant
    handleFormSubmit('legalQuestionForm', '/legal-assistant', 'question', 'response', 'loading');
    
    // Document Generator
    handleFormSubmit('documentForm', '/generate-document', 'prompt', 'response', 'loading');
    
    // Contract Analyzer
    handleFormSubmit('contractForm', '/analyze-contract', 'contract_text', 'response', 'loading');
    
    // Compliance Checker
    handleFormSubmit('complianceForm', '/check-compliance', 'legal_text', 'response', 'loading');
    
    // Document Simplifier
    handleFormSubmit('simplifyForm', '/simplify-document', 'legal_text', 'response', 'loading');
}); 