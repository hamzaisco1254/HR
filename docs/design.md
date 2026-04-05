# User Stories & Acceptance Criteria

This document provides a detailed breakdown of the user stories currently implemented in the **HR Document Generator & Financial Intelligence** platform, along with their acceptance criteria.

---

## 🔑 Authentication & Access Control

### 1. Admin Secure Login
**As an** HR Administrator,  
**I want to** log in with my email and password,  
**so that** I can access the system's management and reporting features.

**Acceptance Criteria:**
* User must provide a valid email address and password.
* On successful login, the user should be redirected to the dashboard.
* If credentials are incorrect, a clear error message (e.g., "Email ou mot de passe incorrect") must be displayed.
* The session should be maintained for 12 hours.

### 2. User Management (Admin Only)
**As an** HR Administrator,  
**I want to** create and manage other user accounts,  
**so that** I can control who has access to the HR and financial tools.

**Acceptance Criteria:**
* Admin can add a new user with a name, email, and password.
* Admin can see a list of all current users.
* Admin can toggle a user's account to "blocked" or "active".
* Admin can change a user's password.
* The maximum limit of users is 5 (including the admin).

---

## 📄 HR Document Automation

### 3. Work Certificate Generation
**As an** HR Staff member,  
**I want to** generate an *Attestation de Travail* for an employee,  
**so that** they can use it as proof of employment.

**Acceptance Criteria:**
* User can manually enter employee data (Name, CIN, Position, Start Date).
* The document must be generated in `.docx` format.
* The generated file must include pre-configured company details (Name, Legal ID, Address).
* The document must use the next available reference number from the system.

### 4. Mission Order Document
**As a user**,  
**I want to** generate an *Ordre de Mission* with destination and travel dates,  
**so that** employees have official travel authorization.

**Acceptance Criteria:**
* User must specify the destination country and expected return date.
* The system must correctly format dates according to French locale (e.g., DD/MM/YYYY).
* The generated document must be available for immediate download.

### 5. Smart Reference Numbering
**As a user**,  
**I want to** have a document numbering system that auto-increments,  
**so that** all documents are uniquely identifiable and follow a chronolgical order.

**Acceptance Criteria:**
* Reference format must be `YY/XXXX` (e.g., `26/0001`).
* The counter must increment by exactly 1 for each new document generated.
* Users can manually reset the counter to zero only after a confirmation prompt.
* Users can change the year prefix manually.

---

## 💰 Financial Intelligence & Invoices

### 6. AI-Powered Invoice Processing
**As a user**,  
**I want to** upload an invoice image or PDF and have data extracted automatically,  
**so that** I can avoid manual data entry for supplier invoices.

**Acceptance Criteria:**
* Supported file formats include PDF, PNG, JPG, and WebP.
* The system should extract the supplier name, total amount, currency, and date using AI.
* Per-field confidence scores must be returned along with the extraction results.
* Users must be able to review and correct any extracted information before saving.

### 7. Financial Dashboard & KPIs
**As a user**,  
**I want to** see a dashboard with the company's financial status,  
**so that** I can monitor liquidity and cash flow.

**Acceptance Criteria:**
* Dashboard must display at least: Total Cash, Paid Invoices, and Overdue Invoices.
* A "Cash Flow" line chart must show monthly inflows and outflows for the last 12 months.
* KPI cards should indicate trends (positive/negative) for metrics like Burn Rate and DPO.
* An AI-powered insight paragraph should summarize risks and recommendations.

### 8. Cloud Data Import
**As a user**,  
**I want to** import employee data from external sources like Google Drive or Dropbox,  
**so that** I can use existing data stored on the cloud.

**Acceptance Criteria:**
* The system must accept public sharing links from Google Sheets, Dropbox, and OneDrive.
* The system should automatically normalize the URL to a direct download link.
* Data must be downloaded securely to a temporary location for processing.
* The system must handle cases where the file format is invalid or the link is private.

---

## 📊 Inventory & Settings

### 9. Company Data Configuration
**As an** Administrator,  
**I want to** save my company's legal details once,  
**so that** they are automatically applied to all future documents.

**Acceptance Criteria:**
* Admin can edit and save: Company Name, ID (MF), Representative Name, Address, and City.
* These details must persist in the `config/company.json` file.
* Saved details must be pre-filled in all document generation forms.
