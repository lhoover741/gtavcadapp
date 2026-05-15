class AIAssist {
    constructor() {
        this.modal = null;
        this.isLoading = false;
        this.init();
    }

    init() {
        this.createModal();
        this.attachEventListeners();
    }

    createModal() {
        const html = `
            <div id="ai-assist-modal" class="ai-assist-modal hidden">
                <div class="ai-assist-overlay"></div>
                <div class="ai-assist-panel">
                    <div class="ai-assist-header">
                        <h2>🤖 AI Civilian Generator</h2>
                        <button class="ai-assist-close">&times;</button>
                    </div>

                    <div class="ai-assist-content">
                        <div class="ai-assist-section">
                            <label>Gender</label>
                            <select id="ai-gender" class="ai-input">
                                <option value="random">Random</option>
                                <option value="male">Male</option>
                                <option value="female">Female</option>
                            </select>
                        </div>

                        <div class="ai-assist-section">
                            <label>Ethnicity</label>
                            <select id="ai-ethnicity" class="ai-input">
                                <option value="random">Random</option>
                                <option value="African American">African American</option>
                                <option value="Hispanic/Latino">Hispanic/Latino</option>
                                <option value="Caucasian">Caucasian</option>
                                <option value="Asian">Asian</option>
                                <option value="Middle Eastern">Middle Eastern</option>
                                <option value="Mixed">Mixed</option>
                            </select>
                        </div>

                        <div class="ai-assist-section">
                            <label>Occupation Type</label>
                            <select id="ai-occupation" class="ai-input">
                                <option value="random">Random</option>
                                <option value="Construction Worker">Construction Worker</option>
                                <option value="Mechanic">Mechanic</option>
                                <option value="Security Guard">Security Guard</option>
                                <option value="Bartender">Bartender</option>
                                <option value="Taxi Driver">Taxi Driver</option>
                                <option value="Unemployed">Unemployed</option>
                            </select>
                        </div>

                        <div class="ai-assist-section">
                            <label>Neighborhood</label>
                            <select id="ai-neighborhood" class="ai-input">
                                <option value="random">Random</option>
                                <option value="Grove Street">Grove Street</option>
                                <option value="Downtown">Downtown</option>
                                <option value="Vinewood">Vinewood</option>
                                <option value="Del Perro">Del Perro</option>
                                <option value="Sandy Shores">Sandy Shores</option>
                            </select>
                        </div>

                        <button id="ai-randomize-btn" class="ai-button secondary">
                            🎲 Randomize Everything
                        </button>
                    </div>

                    <div class="ai-assist-footer">
                        <button id="ai-cancel-btn" class="ai-button secondary">Cancel</button>
                        <button id="ai-generate-btn" class="ai-button primary">
                            ✨ Generate Civilian
                        </button>
                    </div>

                    <div id="ai-loading" class="ai-loading hidden">
                        <div class="ai-spinner"></div>
                        <p>Generating realistic civilian...</p>
                    </div>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', html);
        this.modal = document.getElementById('ai-assist-modal');
    }

    attachEventListeners() {
        // Open modal
        const aiAssistBtn = document.getElementById('ai-assist-btn');
        if (aiAssistBtn) {
            aiAssistBtn.addEventListener('click', () => this.openModal());
        }

        // Close modal
        this.modal.querySelector('.ai-assist-close').addEventListener('click', () => this.closeModal());
        document.getElementById('ai-cancel-btn').addEventListener('click', () => this.closeModal());
        this.modal.querySelector('.ai-assist-overlay').addEventListener('click', () => this.closeModal());

        // Generate
        document.getElementById('ai-generate-btn').addEventListener('click', () => this.generate());

        // Randomize
        document.getElementById('ai-randomize-btn').addEventListener('click', () => this.randomizeAll());
    }

    openModal() {
        this.modal.classList.remove('hidden');
    }

    closeModal() {
        this.modal.classList.add('hidden');
    }

    randomizeAll() {
        const selects = ['ai-gender', 'ai-ethnicity', 'ai-occupation', 'ai-neighborhood'];
        selects.forEach(id => {
            const select = document.getElementById(id);
            const options = select.querySelectorAll('option');
            const randomIndex = Math.floor(Math.random() * options.length);
            select.selectedIndex = randomIndex;
        });
    }

    async generate() {
        if (this.isLoading) return;

        this.isLoading = true;
        document.getElementById('ai-loading').classList.remove('hidden');
        document.getElementById('ai-generate-btn').disabled = true;

        try {
            const params = {
                gender: document.getElementById('ai-gender').value,
                ethnicity: document.getElementById('ai-ethnicity').value,
                occupation_type: document.getElementById('ai-occupation').value,
                neighborhood: document.getElementById('ai-neighborhood').value,
            };

            const response = await fetch('/api/ai/civilian-assist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params),
            });

            const result = await response.json();

            if (result.success) {
                this.autofillForm(result.data);
                this.closeModal();
                this.showToast('✨ Civilian generated successfully', 'success');

                // Scroll to form
                setTimeout(() => {
                    const form = document.getElementById('civilian-form');
                    if (form) {
                        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                }, 300);
            } else {
                this.showToast(`❌ Generation failed: ${result.error}`, 'error');
            }
        } catch (error) {
            this.showToast(`❌ Error: ${error.message}`, 'error');
        } finally {
            this.isLoading = false;
            document.getElementById('ai-loading').classList.add('hidden');
            document.getElementById('ai-generate-btn').disabled = false;
        }
    }

    autofillForm(data) {
        // Map API response keys to form field name attributes
        const fieldMap = {
            'first_name': 'firstName',
            'last_name': 'lastName',
            'date_of_birth': 'dob',
            'gender': 'gender',
            'phone_number': 'phone',
            'address': 'address',
            'occupation': 'occupation',
            'gang_affiliation': 'faction',
            'emergency_contact_name': 'emergencyName',
            'emergency_contact_phone': 'emergencyPhone',
            'driver_license_status': 'driverLicense',
            'firearm_license_status': 'firearmLicense',
            'business_license_status': 'businessLicense',
            'vehicle_make': 'vehicleMake',
            'vehicle_model': 'vehicleModel',
            'vehicle_year': 'vehicleYear',
            'vehicle_color': 'vehicleColor',
            'plate_number': 'plate',
            'insurance_status': 'insurance',
            'criminal_background_notes': 'background',
            'character_backstory': 'backstory',
        };

        for (const [dataKey, fieldName] of Object.entries(fieldMap)) {
            const value = data[dataKey];
            if (value === null || value === undefined) continue;

            // Try by name attribute
            const input = document.querySelector(`[name="${fieldName}"]`);
            if (input) {
                input.value = value;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
    }

    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('show');
        }, 10);

        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    window.aiAssist = new AIAssist();
});
