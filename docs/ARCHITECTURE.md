# DLAS — Technical Architecture & Data Schema

## 1. Tech Stack

### Backend

* Python 3
* Django
* Django Authentication
* Django ORM

### Database

* SQLite for MVP/prototype

### Frontend

* Django Templates
* HTML5
* CSS3
* Vanilla JavaScript

Do not introduce React, Vue, Angular, Node.js, or another frontend framework unless explicitly approved.

### Architecture

Use a simple Django monolith:

Browser
↓
Django URLs
↓
Views
↓
Services
↓
Django ORM
↓
SQLite

All business logic that affects case state should be placed in service functions rather than duplicated across templates.

---

# 2. Application Structure

Recommended structure:

```text
DLAS/
├── manage.py
├── config/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── accounts/
├── cases/
├── documents/
├── referrals/
├── lawyers/
├── mediation/
├── dashboard/
├── core/
│
├── templates/
├── static/
├── media/
└── db.sqlite3
```

Keep the project modular but do not split it into microservices.

---

# 3. Database Schema

## 3.1 User

Django's built-in User model should be used where possible.

Additional profile information:

### UserProfile

```text
id              Integer PK
user_id         FK → User
role            String
phone           String
language        String
created_at      DateTime
updated_at      DateTime
```

Allowed roles:

```text
citizen
representative
helpline_agent
dlao_officer
support_staff
udc_operator
panel_lawyer
mediator
admin
```

---

# 3.2 Application

Represents an application before acceptance as a legal-aid case.

```text
id                  Integer PK
application_id      String UNIQUE
applicant_user_id   FK → User NULL
name                String
phone               String
address             Text
legal_problem       Text
incident_description Text
preferred_channel   String
safe_contact_number String
safe_contact_time   String
language            String
status              String
created_at          DateTime
updated_at          DateTime
```

Important rule:

`case_id` does not exist here as a generated case identifier.

---

# 3.3 CaseRecord

Created only after an authorised officer accepts an application.

```text
id                  Integer PK
case_id             String UNIQUE
application_id      OneToOne → Application
assigned_officer_id FK → User
assigned_lawyer_id  FK → User NULL
priority            String
status              String
accepted_at         DateTime
closed_at           DateTime NULL
created_at          DateTime
updated_at          DateTime
```

---

# 3.4 CaseEvent

This is the central audit/history table.

```text
id                  Integer PK
case_id             FK → CaseRecord
actor_id            FK → User NULL
actor_role          String
channel             String
action              String
description         Text
provenance          String
authority           String NULL
created_at          DateTime
```

Provenance values:

```text
applicant_confirmed
representative_reported
intermediary_translated
staff_entered
ai_inferred
```

CaseEvent should be treated as append-only.

Do not edit historical events.

---

# 3.5 Document

```text
id                  Integer PK
case_id             FK → CaseRecord
uploaded_by         FK → User
title               String
file                 File
status              String
description         Text
ai_summary          Text NULL
ai_confidence       Float NULL
created_at          DateTime
updated_at          DateTime
```

Status:

```text
uploaded
verified
missing
unreadable
```

---

# 3.6 Communication

```text
id                  Integer PK
case_id             FK → CaseRecord
actor_id            FK → User
channel             String
recipient            String
message              Text
result               String
safe_contact_used   Boolean
created_at           DateTime
```

Channels:

```text
voice
sms
web
udc
walk_in
```

---

# 3.7 Task

```text
id                  Integer PK
case_id             FK → CaseRecord
assigned_to         FK → User
title               String
description         Text
due_at              DateTime
status              String
priority             String
created_at          DateTime
completed_at        DateTime NULL
```

Status:

```text
pending
in_progress
completed
overdue
cancelled
```

---

# 3.8 Referral

```text
id                  Integer PK
case_id             FK → CaseRecord
created_by          FK → User
destination         String
reason              Text
expected_action     Text
deadline            DateTime
status              String
returned_count      Integer
created_at          DateTime
acknowledged_at     DateTime NULL
```

Status:

```text
pending
acknowledged
returned
escalated
completed
```

---

# 3.9 LawyerAssignment

```text
id                  Integer PK
case_id             FK → CaseRecord
lawyer_id           FK → User
assigned_by         FK → User
status              String
assigned_at         DateTime
responded_at        DateTime NULL
```

Status:

```text
pending
accepted
declined
changed
completed
```

---

# 3.10 Mediation

```text
id                  Integer PK
case_id             OneToOne → CaseRecord
mediator_id         FK → User
mode                String
scheduled_at        DateTime NULL
attendance_status   String
outcome             Text NULL
status              String
created_at          DateTime
completed_at        DateTime NULL
```

---

# 3.11 Signature

```text
id                  Integer PK
case_id             FK → CaseRecord
document_id         FK → Document
signer_id           FK → User
document_hash       String
signature_data      Text
signed_at            DateTime
offline_created     Boolean
verified             Boolean
```

The prototype may simulate signatures.

---

# 3.12 RelatedCase

```text
id                  Integer PK
case_id             FK → CaseRecord
related_case_id     FK → CaseRecord
relationship_type   String
created_by          FK → User
created_at          DateTime
```

Related cases are linked, not automatically merged.

---

# 3.13 DuplicateCandidate

```text
id                  Integer PK
case_id             FK → CaseRecord
possible_case_id    FK → CaseRecord
match_score         Float
match_reason        Text
review_status       String
reviewed_by         FK → User NULL
created_at          DateTime
```

The system only suggests possible duplicates.

---

# 4. Data Flow

## Read

Browser request
→ URL
→ Django view
→ permission check
→ service/query
→ ORM
→ SQLite
→ template
→ browser

## Write

Browser form
→ Django POST
→ CSRF validation
→ authentication
→ permission check
→ service function
→ database transaction
→ create/update record
→ create CaseEvent
→ response

Important:

Any operation that changes case state must create an audit event.

Example:

```text
Officer accepts application
        ↓
transaction begins
        ↓
create CaseRecord
        ↓
generate Case ID
        ↓
update Application status
        ↓
create CaseEvent
        ↓
transaction commits
```

If any operation fails, the transaction must roll back.

---

# 5. Permissions

### Citizen

Can:

* Create application
* View own application
* View own case status
* Upload permitted documents
* Request lawyer change

Cannot:

* Modify audit history
* Assign lawyers
* Change case priority
* Accept/reject cases

### DLAO Officer

Can:

* Review cases
* Accept/reject
* Assign lawyers
* Refer
* Change priority
* Create tasks
* Close cases

### Lawyer

Can:

* View assigned cases
* Accept/decline
* Add permitted updates
* Upload permitted documents

### Mediator

Can:

* View assigned mediation cases
* Record attendance
* Record outcome

### Support Staff

Can:

* Search permitted case history
* Assist officers
* Upload permitted information

### UDC

Can:

* Create assisted applications
* Record consent
* Submit application

Cannot access the case after submission unless explicitly authorised.

---

# 6. AI Architecture

AI is a service layer, not an authority.

```text
Case Data
   ↓
AI Service
   ↓
Suggestion / Extraction / Draft / Flag
   ↓
Human Review
   ↓
Accepted result
   ↓
CaseEvent
```

Every AI-generated result must identify itself as AI-generated.

No AI result directly changes eligibility, rejection, priority, assignment, closure, or mediation outcome.

---

# 7. Mock External Services

Create simple internal mock services for:

```text
MockSMSService
MockIVRService
MockUSSDService
MockNIDService
MockPaymentService
MockSignatureService
MockAIService
```

These must clearly display:

`SIMULATED`

They must not pretend to be connected to real government systems.
