# DLAS — Master Product Requirements Document

## 1. App Overview

DLAS (Digital Legal Aid System) is a unified digital legal-aid platform designed to let citizens access legal-aid services through different channels while keeping every interaction connected to one case record.

The system supports citizens, representatives, helpline agents, DLAO officers, support staff, lawyers, mediators, UDC operators, and assistive AI services without creating separate systems or fragmented case histories.

---

## 2. MVP Scope

The first version must contain only the following features.

### 2.1 Authentication and Roles

1. Login/logout system.
2. Role-based access.
3. Roles:

   * Citizen
   * Representative
   * Helpline Agent
   * DLAO Officer
   * DLAO Support Staff
   * UDC Operator
   * Panel Lawyer
   * Mediator
   * Admin
4. Users can only see actions and records permitted for their role.

### 2.2 Citizen Intake

5. Citizen can submit a legal-aid application.
6. Application collects:

   * Name
   * Contact information
   * Address
   * Legal problem
   * Incident description
   * Preferred contact channel
   * Safe contact number
   * Safe contact time
   * Language
7. Application receives an Application ID after submission.
8. Case ID is NOT created at initial submission.
9. Case ID is created only when an authorised DLAO officer accepts the application.

### 2.3 Unified Case Record

10. Every accepted case has one CaseRecord.
11. Every important action creates a CaseEvent.
12. Events are append-only.
13. Events record:

    * Actor
    * Role
    * Timestamp
    * Channel
    * Action
    * Description
    * Provenance
14. Provenance values:

    * Applicant-confirmed
    * Representative-reported
    * Intermediary-translated
    * Staff-entered
    * AI-inferred
15. Documents, communications, tasks, referrals and signatures are attached to the relevant case.

### 2.4 DLAO Dashboard

16. DLAO officers see a case queue.
17. Queue categories:

    * New
    * Pending
    * Accepted
    * In Progress
    * Overdue
    * Closed
18. Officer can:

    * View case
    * Accept case
    * Reject case
    * Change priority
    * Assign lawyer
    * Refer case
    * Create tasks
    * Close case
19. Consequential actions require authorised human approval.
20. All consequential actions are logged.

### 2.5 Referral

21. Officer can refer a case to another DLAO/authority.
22. Referral contains:

    * Reason
    * Destination
    * Relevant case history
    * Permitted documents
    * Expected action
    * Deadline
23. Receiving officer can acknowledge or return the referral.
24. Missed acknowledgement creates an overdue task.
25. Repeated returned referrals trigger escalation to an authorised officer.

### 2.6 Lawyer Workflow

26. Officer can assign a panel lawyer.
27. Lawyer sees assigned cases in a worklist.
28. Lawyer can accept or decline an assignment.
29. Lawyer can update case status.
30. Missed updates create an officer task.
31. Citizen status can be communicated through approved contact channels.
32. Lawyer-change requests go to an officer for approval.

### 2.7 Mediation

33. Mediator can view assigned mediation cases.
34. Mediation workflow:

    * Registration
    * Scheduling
    * Attendance
    * Session
    * Outcome
    * Closure
35. Support remote/hybrid and in-person modes.
36. Final mediation outcome requires human mediator action.

### 2.8 Documents

37. Users with permission can upload documents.
38. Documents belong to a case.
39. Document status can be:

    * Uploaded
    * Verified
    * Missing
    * Unreadable
40. Document-processing assistance can identify missing information.
41. AI must never silently invent missing document information.

### 2.9 Duplicate and Related Cases

42. System can identify possible duplicate cases.
43. Matching can use:

    * Name
    * Parent/guardian name
    * Phone
    * Address
    * Incident information
44. Possible duplicates are displayed for human review.
45. The system must NEVER automatically reject, merge, or accuse a person of fraud.
46. Related incidents can be linked while keeping individual case outcomes separate.

### 2.10 AI Assistance

47. AI may:

    * Extract information
    * Categorise information
    * Draft text
    * Identify inconsistencies
    * Flag missing information
    * Rank possible matches
48. AI output must be visibly identified.
49. AI cannot:

    * Decide eligibility
    * Reject a citizen
    * Make final priority decisions
    * Make final lawyer assignments
    * Determine fraud
    * Close a case
    * Decide mediation outcomes

### 2.11 Bangla/English

50. Every user-facing interface must support Bangla and English.
51. English and Bangla are language alternatives, not duplicated text.
52. User can switch language without losing form data.
53. Error, success, validation and status messages must also be translated.

---

# 3. Explicitly Out of Scope

The coding agent must NOT build these in the MVP unless explicitly instructed later.

1. Real 16699 integration.
2. Real USSD gateway.
3. Real SMS gateway.
4. Real IVR/voice gateway.
5. Real NID verification.
6. Real payment gateway.
7. Real government authority API.
8. Production e-signature provider.
9. Production-grade AI/LLM API integration.
10. Real Google Calendar integration.
11. Real external lawyer registry.
12. Real citizen notification infrastructure.
13. Mobile native application.
14. Separate frontend framework such as React unless explicitly approved.
15. Microservices architecture.
16. Multiple databases.
17. Blockchain.
18. Facial recognition.
19. Automated legal decisions.
20. Automated fraud detection.
21. Automatic case merging.
22. Automatic case rejection.
23. Advanced analytics platform.
24. Complex recommendation engine.
25. Social login.
26. Cryptocurrency/payment features.
27. Unapproved third-party libraries.
28. Decorative animations that do not improve usability.

External integrations should be represented with clearly labelled simulated/mock services where necessary for the prototype.

---

# 4. User Flow & Page States

## 4.1 Citizen Flow

Landing Page
→ Language Selection
→ Login/Register
→ Citizen Dashboard
→ New Legal Aid Application
→ Intake Form
→ Review
→ Submit
→ Application ID
→ Application Status
→ Case accepted by officer
→ Case ID generated
→ Case progress
→ Lawyer/Mediation updates
→ Closure

### Empty State

When the citizen has no applications:

"আপনার কোনো আবেদন নেই। নতুন আইনি সহায়তার আবেদন শুরু করুন।"

Show one clear action: "নতুন আবেদন".

### Loading State

Display a simple loading indicator and prevent duplicate submission.

### Error State

Show:

* What failed
* Whether data was saved
* What the user should do next

Never silently clear entered information.

---

## 4.2 DLAO Officer Flow

Login
→ Dashboard
→ Case Queue
→ Case Details
→ Review history/documents
→ Accept/Reject/Refer
→ Case ID generated if accepted
→ Assign lawyer
→ Monitor tasks
→ Update priority/status
→ Close case

### Empty Queue

Show:

"No cases currently require your action."

Do not show fake cases unless demo mode is explicitly enabled.

### Error

If an action fails, the database state must remain unchanged and the officer must be told whether retrying is safe.

---

## 4.3 Lawyer Flow

Login
→ Lawyer Dashboard
→ Assigned Cases
→ Case Details
→ Accept/Decline
→ Case Work
→ Update
→ Documents
→ Hearing/Deadline
→ Completion

A lawyer must never be able to modify protected citizen identity or audit information.

---

## 4.4 Mediator Flow

Login
→ Assigned Mediation Cases
→ Case Details
→ Schedule
→ Attendance
→ Mediation Session
→ Outcome
→ Submit Outcome

Only the mediator or authorised officer can record the final outcome.

---

## 4.5 UDC Flow

Login
→ Assisted Application
→ Citizen Information
→ Consent
→ Intake
→ Review Read-back
→ Submit
→ Application ID
→ End UDC Access

UDC access ends after submission.

The record must distinguish:

* What the citizen said
* What was translated
* What the operator typed

---

## 4.6 Case Status Model

Application statuses:

`DRAFT → SUBMITTED → UNDER_REVIEW → ACCEPTED → IN_PROGRESS → REFERRED → MEDIATION → RESOLVED → CLOSED`

Rejection is a terminal application decision made by an authorised officer.

Every transition must create a CaseEvent.

---

# 5. Core Design Principle

There must be:

**ONE application + ONE database + ONE case record + MANY access doors.**

The interface may change depending on the user or channel, but the underlying case record must never be duplicated.
