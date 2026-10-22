# Software Requirements Specification (SRS)
## Camdock International School Portal

**Document version:** 1.0  
**Date:** 22 October 2026  
**Source:** `Camdock International School — Portal (1).html`  
**Purpose:** Backend implementation specification for use with Claude Code  
**Project location/context:** Camdock International School, Enugu State, Nigeria

---

## 1. Introduction

### 1.1 Purpose

This SRS defines the backend requirements for converting the supplied Camdock International School Portal HTML prototype into a persistent, secure school management portal.

The current HTML prototype includes a public school landing page and portal interfaces for administrators, teachers, and parents. It uses sample records stored in browser `localStorage`; the login is a role-selection simulation and does not authenticate users against a server. The prototype itself explicitly describes that limitation and says production should connect to the school's real student and account records.

This document describes the backend needed to support the visible portal workflows. It is intended to be used as the implementation reference for Claude Code. Claude Code must not begin modifying project files until the developer has reviewed and approved the proposed implementation plan and current repository state.

### 1.2 Scope

The backend shall support:

- School public-site statistics where the values are derived from school records.
- User accounts and role-based access for administrators, teachers, and parents.
- Student records and class assignments.
- Teacher records, subjects, and assigned classes.
- Academic terms, subjects, student scores, grades, individual averages, and class averages.
- Fee records, payment entries, outstanding balances, payment status, and printable receipt data.
- Student result-sheet data suitable for display and printing.
- Dashboard summaries and filtering by academic term.
- Validation, access control, auditability, automated tests, and deployment configuration.

The supplied HTML includes visual layout, navigation, forms, sample data, grade thresholds, fee categories, and receipt/result-sheet layouts. It does not specify every production rule. Where this SRS makes a necessary engineering recommendation that is not explicitly stated in the HTML, it is labelled **Proposed / confirm before implementation**.

### 1.3 Out of scope unless approved

The HTML does not establish requirements for the following. Do not implement them without approval:

- Online payment processing or payment-gateway integration.
- SMS, email, push notifications, or password-reset delivery.
- Parent self-registration or public account creation.
- Attendance, timetables, homework, transport, library, admissions, payroll, or inventory.
- Multiple campuses or multi-tenant school management.
- Mobile-native applications.
- A full content-management system for editing public mission, vision, motto, and branding.
- Automated promotion between classes or academic years.

### 1.4 Definitions

| Term | Meaning |
|---|---|
| Administrator | User allowed to manage student and teacher records, results, and fee records. |
| Teacher | User allowed to view assigned students and manage results for permitted classes. |
| Parent | User allowed to view the linked child’s results and fee records and print available documents. |
| Academic term | First Term, Second Term, or Third Term. |
| Score | Student's numeric mark for a subject in a term. |
| Class average | Average score calculated for students in the same class and term, reported by subject and overall. |
| Fee record | A charge for a student, fee category, and term, including billed and paid amounts. |
| Receipt | A printable representation of a recorded fee transaction/record. The prototype currently prints the fee record rather than documenting a separate payment transaction ledger. |
| Result sheet | Printable term report containing student scores, grades, subject class averages, and overall average. |

---

## 2. Product overview

### 2.1 Product context

The portal is branded **Camdock International School**. The project owner confirms that the school is located in Enugu State, Nigeria; the supplied HTML prototype's location text should not override this correction. The public page describes a unified portal for administrators, teachers, and parents.

The UI includes:
1. Public home page with mission, vision, motto, school statistics, and portal entry points.
2. Shared sign-in page with role selection.
3. Administrator portal.
4. Teacher portal.
5. Parent portal.
6. Student, teacher, payment, receipt, and result-sheet modals.

### 2.2 User roles and visible permissions

| Capability | Administrator | Teacher | Parent |
|---|---:|---:|---:|
| View dashboard overview | Yes | Yes, dashboard labelled “My overview” | Yes, child-specific overview |
| View student directory | All students | Students in assigned classes | No general directory |
| Add student | Yes | No | No |
| View teacher directory | Yes | Not shown in teacher navigation | No |
| Add teacher | Yes | No | No |
| View/edit results | All classes in the prototype | Assigned classes | Linked child only |
| View class averages | Yes | For permitted classes | Child's class averages |
| View all fee records | Yes | No fee navigation shown | No |
| Record fee record/payment | Yes | No | No |
| View fee records and receipt data | Yes | No | Linked child only |
| Print result sheet | UI exposes for parent; admin/teacher result-sheet support should be confirmed | UI includes result grid; printing is not clearly established for staff | Yes |
| Sign out | Yes | Yes | Yes |

**Important implementation rule:** Hiding a navigation item in the frontend is not authorization. Every protected API must enforce permissions server-side.

### 2.3 School classes

The prototype lists the following classes:

- Nursery 1
- Nursery 2
- Primary 1 through Primary 6
- JSS 1 through JSS 3
- SSS 1 through SSS 3

The prototype's class names must be stored consistently. It contains an inconsistency in sample teacher assignments (`JSS1` versus `JSS 1`, for example). Production data must use one canonical class identifier and display label.

### 2.4 Subjects shown in the prototype

For Nursery and Primary classes, the prototype maps these subjects:

- Mathematics
- English Language
- Basic Science
- Social Studies
- Quantitative Reasoning

For JSS and SSS classes, it maps:

- Mathematics
- English Language
- Basic Science
- Civic Education
- Agricultural Science

These are the prototype's default subject mappings, not proof that the school's complete curriculum is limited to these subjects. **Proposed / confirm before implementation:** administrators should be able to manage subjects and class-subject assignments rather than having them permanently hard-coded.

### 2.5 Academic terms

The prototype supports:
- First Term
- Second Term
- Third Term

An academic year is not modelled in the supplied HTML. **Proposed / confirm before implementation:** include an academic-year entity or field so scores and fee records from different years cannot overwrite or become mixed with one another.

---

## 3. Functional requirements

Requirements use identifiers so they can be tracked in Claude Code tasks and tests.

### 3.1 Accounts, authentication, and sessions

**FR-AUTH-001 — Server-side authentication**  
The backend shall authenticate registered accounts. Selecting a role or choosing a student/teacher from a dropdown shall not be considered authentication.

**FR-AUTH-002 — Role-based access**  
Each account shall have a server-controlled role: `admin`, `teacher`, or `parent`. The client shall not be able to grant itself a role by submitting a role value.

**FR-AUTH-003 — Account identity**  
Each account shall have a stable unique identifier and authentication credential stored securely. Passwords, if used, must be stored as strong password hashes, never plaintext.

**FR-AUTH-004 — Login and logout**  
The API shall provide a login mechanism and a way to invalidate or expire the active session/token. The specific authentication method (session cookie or access/refresh tokens) requires approval before implementation.

**FR-AUTH-005 — Disabled accounts**  
Disabled accounts shall not be able to sign in or continue using protected endpoints.

**FR-AUTH-006 — Parent-child relationship**  
A parent account shall be linked to one or more authorized students through an explicit relationship table. The parent must not gain access to another student's records by changing a student ID in a request.

**FR-AUTH-007 — Teacher-class assignment**  
A teacher's access to student lists and result operations shall be restricted to classes assigned to that teacher, unless an administrator grants an explicit additional permission.

**FR-AUTH-008 — Initial administrator provisioning**  
Production must have a controlled process for creating the first administrator. No public endpoint may allow anonymous users to become administrators.

**FR-AUTH-009 — Demo accounts**  
The prototype's role picker and prefilled “Head Admin” name are demo behaviour only. They must not be treated as a production authentication mechanism.

### 3.2 Dashboard and public statistics

**FR-DASH-001 — Public statistics**  
The public page displays student count, teaching staff count, and class count. If these values are supplied by the backend, they shall be computed from active records and must not expose personal information.

**FR-DASH-002 — Administrator/teacher overview**  
The dashboard shall provide:
- Total students.
- Total teachers.
- Active classes represented in student records.
- Fees collected.
- Outstanding fee balance.
- Class averages for the selected term.

**FR-DASH-003 — Parent overview**  
The parent dashboard shall show only the linked child's:
- Name and class.
- Selected term.
- Student average.
- Class average.
- Outstanding fee balance.
- Number of subjects with recorded result entries.
- Links/actions to view results and fee records.

**FR-DASH-004 — Term filter**  
Dashboard calculations that depend on academic term shall use the selected term. Fee totals must be clearly defined as term-specific or all-term totals. The current HTML mixes all fee records into the dashboard totals while labelling the dashboard with the selected term; this ambiguity must be resolved before implementation.

**FR-DASH-005 — Empty state**  
When no records exist, endpoints shall return valid empty collections and zero or null values according to a documented response schema. They must not fail because the database has no seeded sample data.

### 3.3 Student records

**FR-STU-001 — Create student**  
An administrator shall be able to create a student with:
- Full name (required).
- Class (required and valid).
- Gender (the prototype offers Female and Male; whether other values are required should be confirmed).
- Guardian/parent display name (optional in the prototype).
- Stable unique student identifier.

**FR-STU-002 — List students**  
Administrators shall be able to list students across the school. Teachers shall receive only students in their assigned classes. Parents shall not have access to the general directory.

**FR-STU-003 — Search students**  
The student directory shall support searching by student name. Server-side pagination and filtering are recommended for production datasets.

**FR-STU-004 — Student details**  
The API shall expose permitted student information including name, class, gender, guardian display information, and calculated average where appropriate.

**FR-STU-005 — Student initialization**  
When a student is created, the system shall make the student available for class/subject result management and fee recording. It shall not silently create fabricated scores or payments.

**FR-STU-006 — Unique identifiers**  
Student identifiers must be generated safely and uniquely by the backend/database. The prototype's random numeric IDs are not sufficient for production uniqueness guarantees.

**FR-STU-007 — Update/archive**  
Student editing, transfer, withdrawal, deletion, and archival are not clearly implemented in the supplied UI. Do not add these workflows without approval. Prefer archival over destructive deletion once financial or academic records exist.

### 3.4 Teacher records and assignments

**FR-TEA-001 — Create teacher**  
An administrator shall be able to create a teacher record with:
- Full name (required).
- Subject or subjects.
- Assigned class or classes.
- Phone number.

**FR-TEA-002 — List teachers**  
Administrators shall be able to view the teacher directory with subject, assigned classes, and phone number.

**FR-TEA-003 — Class assignment**  
Teacher-to-class assignments shall be represented as structured relationships, not an unvalidated comma-separated string.

**FR-TEA-004 — Teacher identity**  
The teacher record used for login must be linked to an actual account. Selecting a teacher's name from a public dropdown must not grant access to that teacher's data.

**FR-TEA-005 — Assignment restrictions**  
A teacher may read or modify results only for classes and subjects they are authorized to teach, unless school policy approved by the administrator states otherwise.

**FR-TEA-006 — Update/deactivate**  
Teacher editing and deactivation are not explicitly provided in the visible prototype. Confirm before adding these operations.

### 3.5 Results and grading

**FR-RES-001 — Result records**  
The system shall store student scores by student, academic year (if approved), term, subject, and class context.

**FR-RES-002 — Score validation**  
Scores shall be numeric and bounded from 0 to 100 inclusive unless the school approves another scoring model. Reject invalid values rather than silently coercing them.

**FR-RES-003 — Result entry**  
Administrators and authorized teachers shall be able to enter and update scores for permitted students and subjects.

**FR-RES-004 — Result retrieval**  
The API shall return a student's subject scores for a selected term.

**FR-RES-005 — Grade mapping**  
The HTML prototype defines the following grade thresholds:

| Score | Grade |
|---:|---|
| 75–100 | A1 |
| 70–74.99 | B2 |
| 65–69.99 | B3 |
| 60–64.99 | C4 |
| 55–59.99 | C5 |
| 50–54.99 | C6 |
| 45–49.99 | D7 |
| 40–44.99 | E8 |
| 0–39.99 | F9 |

The prototype uses integer scores but the API should document whether decimals are permitted. Grade thresholds must be configurable or centrally defined so different parts of the application cannot calculate different grades.

**FR-RES-006 — Student average**  
Calculate the arithmetic mean of the student's recorded subject scores for the selected term. Define the rounding rule consistently; the prototype rounds to one decimal place.

**FR-RES-007 — Subject class average**  
For each subject, calculate the mean of valid recorded scores for students in the same class and term. Do not include missing scores as zero unless school policy explicitly requires that.

**FR-RES-008 — Overall class average**  
The prototype calculates the overall class average as the arithmetic mean of its subject-level class averages. Preserve this behaviour unless the school approves another formula.

**FR-RES-009 — Result sheet**  
The result-sheet data shall include:
- School name and location.
- Student name and class.
- Academic term (and academic year if introduced).
- Subject, score, grade, and subject class average.
- Overall student average.

The frontend may render and print the returned data. The backend shall provide authoritative data rather than generating the report from browser-local values.

**FR-RES-010 — Result publication**  
The prototype does not define draft, reviewed, or published result states. **Proposed / confirm before implementation:** results should not become visible to parents until an authorized staff member publishes them.

**FR-RES-011 — Result history and audit**  
Score changes should record who changed the score, when, and the previous/new values. This is a production safety recommendation requiring confirmation.

**FR-RES-012 — Subject mapping**  
The prototype chooses subjects by class level. Production subject assignment should be data-driven if the school needs subject variations by class or academic year.

### 3.6 Fee records, balances, and receipts

**FR-FEE-001 — Fee record creation**  
An administrator shall be able to record a fee entry for a student with:
- Student.
- Fee type.
- Term.
- Amount billed.
- Amount paid.
- Date recorded.
- Unique receipt/record identifier.

**FR-FEE-002 — Fee categories**  
The prototype includes:
- Tuition Fee.
- Development Levy.
- Examination Fee.
- PTA Levy.
- Uniform & Materials.

The system shall use a controlled fee-category list. Whether administrators can configure categories is to be confirmed.

**FR-FEE-003 — Currency**  
Amounts shall be stored as decimal monetary values in Nigerian naira (NGN), not floating-point values. The interface displays the `₦` symbol.

**FR-FEE-004 — Amount validation**  
The billed amount must be greater than zero. The paid amount must not be negative. The system must reject a paid amount greater than the billed amount unless overpayment handling is explicitly approved.

**FR-FEE-005 — Payment status**  
Calculate status from billed and paid amounts:
- `paid`: paid amount is equal to billed amount.
- `partial`: paid amount is greater than zero but less than billed amount.
- `due`: paid amount is zero and a balance remains.

The prototype treats a zero or negative balance as paid; production validation should prevent invalid overpayment unless an explicit credit policy exists.

**FR-FEE-006 — Outstanding balance**  
For each fee record, balance = amount billed − amount paid. Student outstanding balance is the sum of outstanding balances for the relevant records. Define whether dashboard totals are current-term or all-term totals before implementation.

**FR-FEE-007 — Fee listing**  
Administrators shall be able to list fee records for all students. Parents shall be able to list only the fee records linked to their authorized child.

**FR-FEE-008 — Receipt data**  
A receipt shall include:
- Unique receipt/record number.
- Date.
- Student name and class.
- Fee type and term.
- Amount billed.
- Amount paid.
- Outstanding balance where applicable.
- Payment status.
- School identity.
- Bursar signature and school-stamp placeholders.

**FR-FEE-009 — Print receipt**  
The backend shall return receipt data in a stable format so the frontend can render and print it. Printing itself may be performed by the browser.

**FR-FEE-010 — Payment history**  
The supplied prototype stores one fee record containing billed and paid amounts. It does not define multiple instalments, reversals, refunds, or gateway-confirmed payments. Do not invent these workflows. If multiple payments against one bill are required, model bills and payment transactions separately and obtain approval first.

**FR-FEE-011 — Auditability**  
Creation or modification of fee/payment records must be attributable to an authenticated user. An audit trail is strongly recommended because the records are financial.

**FR-FEE-012 — No online payment assumption**  
The UI supports manually recording a payment; it does not show a payment gateway. Do not claim that the system processes online payments unless separately approved.

### 3.7 Printing and reporting

**FR-REP-001 — Result report**  
The backend shall provide complete term result data for the authorized student and class context.

**FR-REP-002 — Fee receipt**  
The backend shall provide receipt data for a specific fee record only when the requesting user has permission to access it.

**FR-REP-003 — Print layout independence**  
PDF generation is not explicitly required by the HTML; it uses browser print. PDF export may be added only after confirmation.

### 3.8 Error handling and API consistency

**FR-API-001 — Consistent responses**  
The API shall use a consistent JSON structure for successful responses and errors.

**FR-API-002 — HTTP status codes**  
Use suitable HTTP status codes, including 400/422 for invalid input, 401 for unauthenticated requests, 403 for insufficient permissions, 404 for missing records, and 409 for uniqueness or state conflicts.

**FR-API-003 — Validation errors**  
Validation errors shall identify the invalid field(s) without leaking sensitive data or internal stack traces.

**FR-API-004 — Pagination**  
List endpoints for students, teachers, and fee records should support pagination. Search/filter parameters must be validated.

**FR-API-005 — Data consistency**  
Operations that create related records must run transactionally. For example, creating a student and any required default associations must not leave partially-created records.

---

## 4. Suggested API surface

The following is a proposed REST API design based on the visible UI, not an existing API contract. Confirm endpoint names and authentication approach before implementation. Prefix examples with `/api/v1`.

| Method | Endpoint | Purpose | Access |
|---|---|---|---|
| GET | `/public/stats` | Public school counts | Public |
| POST | `/auth/login` | Authenticate account | Public |
| POST | `/auth/logout` | End current session/token | Authenticated |
| GET | `/auth/me` | Return current account and role | Authenticated |
| GET | `/dashboard/overview` | Role-specific dashboard | Authenticated |
| GET | `/terms` | List academic terms | Authenticated |
| GET | `/classes` | List classes | Authenticated |
| GET | `/students` | List/search permitted students | Admin/teacher within scope |
| POST | `/students` | Create student | Admin |
| GET | `/students/{student_id}` | Student detail within scope | Authorized roles |
| GET | `/teachers` | List teachers | Admin |
| POST | `/teachers` | Create teacher | Admin |
| GET | `/results` | List results by class/term filters | Admin/authorized teacher |
| PUT/PATCH | `/results/{result_id}` | Update score | Admin/authorized teacher |
| GET | `/students/{student_id}/results` | Get student's term results | Authorized staff/linked parent |
| GET | `/students/{student_id}/result-sheet` | Get printable result data | Authorized staff/linked parent |
| GET | `/classes/{class_id}/averages` | Get class averages for a term | Admin/authorized teacher/linked parent as scoped |
| GET | `/fees` | List permitted fee records | Admin/linked parent |
| POST | `/fees` | Record a fee entry/payment amount | Admin |
| GET | `/fees/{fee_id}` | Get fee record | Admin/linked parent |
| GET | `/fees/{fee_id}/receipt` | Get printable receipt data | Admin/linked parent |

Implementation notes:
- Do not trust `student_id`, `class_id`, `teacher_id`, or role values supplied by the client without checking the authenticated user's permissions.
- Use request/response schemas and documented pagination.
- If a receipt represents a bill with multiple payment instalments, revise the data model and API before implementation.
- Avoid returning private guardian details to roles that do not need them.

---

## 5. Proposed database model

This is a recommended relational model for review, not a claim that these tables already exist.

### 5.1 Core tables

**users**
- `id` — primary key
- `email` or `username` — unique login identifier, subject to the school's chosen login policy
- `password_hash`
- `role` — `admin`, `teacher`, `parent`
- `is_active`
- `created_at`, `updated_at`, optional `last_login_at`

**students**
- `id` — primary key
- `student_number` — unique human-readable identifier
- `full_name`
- `class_id` — foreign key
- `gender` — approved controlled value
- `guardian_display_name` — optional compatibility/display field if needed
- `is_active`
- `created_at`, `updated_at`

**parent_student**
- `parent_user_id` — foreign key to users
- `student_id` — foreign key to students
- `relationship` — optional (mother, father, guardian, etc., if approved)
- unique constraint across parent and student

**teachers**
- `id` — primary key
- `user_id` — optional/required FK to users according to account provisioning design
- `full_name`
- `phone`
- `is_active`
- `created_at`, `updated_at`

**classes**
- `id` — primary key
- `name` — unique canonical class label
- `level` — nursery/primary/junior_secondary/senior_secondary
- `is_active`

**teacher_classes**
- `teacher_id`
- `class_id`
- unique constraint across teacher and class

**subjects**
- `id`
- `name`
- `is_active`

**class_subjects**
- `class_id`
- `subject_id`
- optional academic-year scope
- unique constraint across class, subject, and applicable academic year

**academic_years** — proposed
- `id`
- `name`
- `start_date`, `end_date`
- `is_current`

**terms**
- `id`
- `name` — First Term, Second Term, Third Term
- optional `academic_year_id`
- optional `start_date`, `end_date`
- optional status (draft/open/closed) if school approves term lifecycle rules

**results**
- `id`
- `student_id`
- `subject_id`
- `term_id`
- `score`
- `created_by`, `updated_by`
- timestamps
- unique constraint for student + subject + term (plus academic year if term does not own year)
- score check constraint, typically 0–100

**fee_records**
- `id`
- `student_id`
- `term_id`
- `fee_type`
- `amount_billed` — decimal
- `amount_paid` — decimal if preserving the prototype's one-record model
- `recorded_at`
- `receipt_number` — unique
- `created_by`, `updated_by`
- timestamps
- check constraints for valid amounts

**audit_logs** — recommended
- `id`
- `actor_user_id`
- `action`
- `entity_type`
- `entity_id`
- safe metadata for changes
- timestamp

### 5.2 Data model decisions requiring approval

Before implementation, decide:
1. Login identifier: email, username, phone, or school-issued account ID.
2. Password-based login versus another authentication mechanism.
3. Whether one parent can access multiple children and whether a child can have multiple parent/guardian accounts.
4. Whether academic year must be introduced in the first release.
5. Whether scores require draft/review/publish states.
6. Whether a fee record can have multiple instalment payments.
7. Whether subjects and fee categories are administrator-configurable.
8. Whether records can be edited, archived, or deleted and by whom.

---

## 6. Non-functional requirements

### 6.1 Security and privacy

**NFR-SEC-001** All protected endpoints shall require authentication.

**NFR-SEC-002** Authorization shall be enforced in the backend for every operation and every record returned.

**NFR-SEC-003** Passwords must never be stored in plaintext.

**NFR-SEC-004** Secrets and credentials must be supplied through environment variables or an approved secrets mechanism, not committed to source control.

**NFR-SEC-005** Production must use HTTPS.

**NFR-SEC-006** Validate and normalize all input server-side. Use ORM/query parameterization to prevent SQL injection.

**NFR-SEC-007** Prevent cross-site scripting by safely serializing data and using appropriate frontend escaping; do not trust HTML supplied by users.

**NFR-SEC-008** Logs must not expose passwords, authentication tokens, or unnecessary student/guardian information.

**NFR-SEC-009** Parent access must be scoped to explicitly linked students. Teacher access must be scoped to assigned classes and subjects.

**NFR-SEC-010** Financial and academic changes should be auditable.

### 6.2 Reliability and integrity

- Persist production data in the database, not browser `localStorage`.
- Use database transactions for related writes.
- Apply database constraints for uniqueness, valid score ranges, and valid monetary amounts.
- Avoid random client-generated identifiers for persistent entities.
- Backups and restore procedures must be documented before production launch.
- Seed data used for development must be clearly separated from real school data.

### 6.3 Performance and maintainability

- Use pagination for potentially large lists.
- Avoid calculating class averages through repeated unbounded queries; use suitable query aggregation and indexes.
- Keep business rules (grading, fee status, permissions) in backend services rather than duplicating them in routes and frontend code.
- Maintain API documentation and automated tests.
- Configure development and production settings separately.

### 6.4 Accessibility and device compatibility

The HTML is responsive and supports desktop and mobile layouts. The backend must return consistent data independent of device. Responsive layout and visual accessibility remain frontend responsibilities.

---

## 7. Business rules captured from the prototype

1. The school offers classes from Nursery 1 through SSS 3 as listed in Section 2.3.
2. The prototype supports First Term, Second Term, and Third Term.
3. The prototype's default subject mapping depends on class level.
4. Student average is the mean of recorded subject scores for a term, rounded to one decimal place.
5. Subject class averages use the students in the same class with a recorded score for that subject and term.
6. Overall class average is the mean of the subject-level averages in the prototype.
7. Fee status is shown as Paid, Partial, or Due according to billed and paid values.
8. The listed fee categories are Tuition Fee, Development Levy, Examination Fee, PTA Levy, and Uniform & Materials.
9. Parents see the selected child's results and fee records in the prototype.
10. Teachers see students in their assigned classes and have a results workflow.
11. Administrators have navigation for students, teachers, results, and fees.
12. The prototype stores data in browser `localStorage`, seeds sample data, and uses role selection rather than real authentication. These are limitations to replace, not production requirements.

---

## 8. Acceptance criteria

The backend release shall not be considered complete until the following are verified.

### Authentication and authorization
- [ ] A valid registered account can authenticate.
- [ ] Invalid credentials are rejected.
- [ ] Anonymous requests cannot read protected data or mutate records.
- [ ] A parent cannot read another student's results or fee records by changing IDs.
- [ ] A teacher cannot read or modify records outside assigned classes/subjects.
- [ ] An administrator-only endpoint rejects teacher and parent accounts.
- [ ] Logout/session invalidation behaves according to the approved authentication design.

### Students and teachers
- [ ] An administrator can create a valid student.
- [ ] Invalid or missing required fields return documented validation errors.
- [ ] Student identifiers are unique.
- [ ] Teachers see only students in their assigned classes.
- [ ] An administrator can create a teacher and assign classes.
- [ ] Class names are stored consistently.

### Results
- [ ] Authorized staff can create and update scores.
- [ ] Scores below 0 or above 100 are rejected unless the scoring policy is changed.
- [ ] The correct grade is returned for each score boundary.
- [ ] Student averages and class averages match the documented formulas.
- [ ] Missing scores are not silently treated as zero.
- [ ] Parents can only retrieve results for linked students.
- [ ] Result-sheet data contains the required student, term, subject, score, grade, and average fields.

### Fees and receipts
- [ ] Only authorized administrators can record fee entries.
- [ ] Invalid billed or paid amounts are rejected.
- [ ] Paid, Partial, and Due statuses are calculated correctly.
- [ ] Outstanding balances are calculated correctly.
- [ ] Parents can retrieve fee records only for linked students.
- [ ] Receipt numbers are unique.
- [ ] Receipt data includes the fields specified in FR-FEE-008.
- [ ] Financial writes are attributable to an authenticated user.

### Quality
- [ ] Automated tests cover successful flows, validation failures, unauthorized access, and forbidden cross-user access.
- [ ] API documentation is available in the development environment.
- [ ] Database migrations can create the schema on a clean database.
- [ ] No production workflow depends on browser `localStorage` as its source of truth.
- [ ] No production account can authenticate merely by selecting a role or name.

---

## 9. Testing requirements

At minimum, create automated tests for:

1. Authentication success/failure and inactive accounts.
2. Role-based access for all three roles.
3. Parent-to-student ownership boundaries.
4. Teacher-to-class assignment boundaries.
5. Student creation, uniqueness, and validation.
6. Teacher creation and assignment.
7. Score bounds and each grade threshold.
8. Student average and class average formulas.
9. Missing result handling.
10. Fee amount validation and payment status boundaries.
11. Outstanding balance calculations.
12. Receipt access control and unique receipt numbers.
13. Empty database/dashboard responses.
14. Database transaction rollback for failed related writes.
15. Regression tests for every bug fixed during development.

Tests must use isolated test data and must not rely on the sample names or randomly generated results from the HTML prototype.

---

## 10. Recommended implementation sequence

This sequence is a proposed plan. Claude Code must inspect the existing repository before recommending changes and must request approval before editing files.

### Phase 0 — Repository and requirements checkpoint
- Inspect the current repository, existing files, branch, README, project instructions, dependencies, and tests.
- Report what already exists and what is missing.
- Do not overwrite or delete existing work.
- Present a proposed file tree and architecture for approval.
- Resolve the decisions in Section 5.2 that block implementation.

### Phase 1 — Backend foundation
- Set up the approved Python version and `uv` workflow.
- Configure the selected web framework, environment settings, database connection, migrations, and health endpoint.
- Add consistent error handling and API documentation.
- Add automated test configuration.

### Phase 2 — Database and core entities
- Create migrations for users, students, teachers, classes, assignments, subjects, terms, results, and fee records.
- Add constraints and indexes.
- Implement safe development-only seed data if approved.

### Phase 3 — Authentication and authorization
- Implement login/session handling and account provisioning.
- Implement role-based access and parent/teacher data scopes.
- Test unauthorized and forbidden access before adding business features.

### Phase 4 — Student and teacher management
- Implement student and teacher endpoints.
- Add class assignment logic and search/pagination.
- Add tests for role restrictions.

### Phase 5 — Results
- Implement score entry and retrieval.
- Implement grade mapping, student averages, class averages, and result-sheet data.
- Add tests for boundaries and calculations.

### Phase 6 — Fees and receipts
- Implement fee records, validation, balance/status calculations, and receipt data.
- Add audit events for financial changes.
- Do not integrate a payment gateway without explicit approval.

### Phase 7 — Dashboards and integration
- Implement role-specific dashboard endpoints and public statistics.
- Integrate the frontend with the API after the API contract is approved.
- Remove dependency on browser-local sample data for production workflows.

### Phase 8 — Hardening and release
- Run automated tests, migrations from a clean database, lint/type checks where applicable, and security checks.
- Document environment variables, local setup, deployment, backups, and operational checks.
- Demonstrate the acceptance criteria in Section 8.

---

## 11. Instructions for Claude Code

Use this SRS as the requirements baseline for the Camdock International School Portal backend.

**Required working rules:**

1. Start by inspecting the repository and reporting the current state. Do not assume the repository is empty.
2. Read the project's existing `CLAUDE.md`, README, and other contributor instructions before proposing changes.
3. Confirm the backend framework and architecture against existing project decisions. Do not silently select or replace the stack.
4. Use Python and `uv` for Python dependency management. Do not use `pip` as the project workflow.
5. Do not begin coding immediately. First provide:
   - A concise repository assessment.
   - A requirements-to-existing-code gap analysis.
   - The proposed architecture and file tree.
   - The database entity/relationship proposal.
   - The API endpoint plan.
   - Any unresolved questions or assumptions.
   - The exact files expected to change.
6. Wait for explicit approval before modifying files.
7. Work in small, reviewable phases. After each phase, report files changed, commands executed, tests run, results, and remaining issues.
8. Do not start the next phase until the user approves the checkpoint.
9. Never claim tests, migrations, or endpoints work unless they were actually run or inspected.
10. Do not delete branches, rewrite Git history, overwrite unrelated work, or commit changes without explicit authorization.
11. Keep secrets out of source control and do not use prototype demo identities as real production accounts.
12. If the HTML does not define a business rule, identify the ambiguity and ask rather than inventing a rule.
13. Prioritize backend correctness, access control, data integrity, and test coverage before visual enhancements.
14. Keep the SRS updated if an approved requirement changes.

**First response expected from Claude Code:** an inspection report and proposed plan only. No code changes until approved.

---

## 12. Open questions requiring school/product-owner decisions

These are not answered by the supplied HTML:

1. What login identifier should each role use: email, phone, username, or school-issued ID?
2. Who creates parent and teacher accounts, and how are credentials delivered?
3. Can a parent account be linked to multiple children? Can a child have multiple authorized guardians?
4. Is an academic year required in the first release?
5. Can teachers edit scores after submission, or must an administrator approve/publish results?
6. Should results be draft, reviewed, and published?
7. Can one fee bill receive multiple instalment payments?
8. What should happen if an overpayment occurs?
9. Are fee amounts set per student, class, term, or fee category?
10. Can administrators edit or reverse an incorrect fee entry?
11. Are subjects and fee categories configurable by administrators?
12. Are Nursery classes expected to use the same grading and result-sheet rules as Primary and Secondary?
13. Should the backend generate PDFs, or is browser printing sufficient?
14. Are email/SMS notifications required?
15. What hosting, deployment, backup, and data-retention policies are expected?

---

## 13. Source limitations and implementation cautions

- This SRS is derived from the supplied HTML prototype. It does not represent a separate interview with school staff or an approved school policy document.
- The prototype's sample student, teacher, result, and fee data are illustrative and must not be imported into production as real records.
- The prototype's login does not verify credentials. Production authentication is a new backend requirement.
- The prototype stores data in `localStorage`; this is not suitable as the authoritative data store for a multi-user school system.
- The prototype's dashboard fee totals include all fee records while the selected term is shown. The intended reporting period must be clarified.
- The prototype uses inconsistent class-label formatting in sample teacher assignments. Canonical class identifiers are needed.
- The prototype does not define academic years, publication workflow, payment instalments, refunds, or online payments. These require decisions before implementation.
- The proposed database and API designs are engineering recommendations based on the visible workflows and should be reviewed before they are treated as final.

**End of SRS**
