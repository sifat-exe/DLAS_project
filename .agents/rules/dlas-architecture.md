---
trigger: always_on
description: "Permanent architecture and product constraints for the DLAS legal-aid system."

The authoritative documents are:

- docs/MASTER_PRD.md
- docs/ARCHITECTURE.md

Read them before making architectural changes.
---

# DLAS Development Rules

The project is DLAS (Digital Legal Aid System).

The authoritative product requirements are in:

@../../docs/MASTER_PRD.md

The authoritative technical architecture and database schema are in:

@../../docs/ARCHITECTURE.md

## Non-negotiable rules

1. Use Django + SQLite.
2. Use Django Templates, HTML, CSS and vanilla JavaScript.
3. Do not introduce React, Vue, Angular, Node.js, Tailwind, Bootstrap or another frontend framework.
4. Keep the system as one Django application architecture with one database.
5. Do not create microservices.
6. Do not create a second database.
7. Do not invent features outside the Master PRD.
8. Do not install libraries unless explicitly required and approved.
9. CaseEvent is append-only.
10. Every important case state change must create a CaseEvent.
11. Application ID is created when an application is submitted.
12. Case ID is created ONLY when an authorised DLAO officer accepts the application.
13. AI can assist with extraction, categorisation, drafting, flags and matching.
14. AI must never make final legal-aid decisions.
15. AI must never automatically reject, merge, assign, close or declare fraud.
16. Every AI-generated result must be clearly identified.
17. All user-facing text must support both Bangla and English.
18. Server-side permission checks are mandatory.
19. Never expose one citizen's case to another citizen.
20. Keep the UI simple and functional.
21. Prefer existing Django functionality over adding dependencies.
22. Before changing architecture, ask for confirmation.
23. Before implementing a feature not explicitly covered by the PRD, ask for confirmation.
24. After each major implementation, run appropriate tests/checks and report the result.