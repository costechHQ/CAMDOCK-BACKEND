# Camdock International School Portal

Backend for the Camdock International School portal (Enugu State, Nigeria), built from an HTML
prototype and a written specification.

- [`DOCS/Camdock_International_School_Backend_SRS.md`](DOCS/Camdock_International_School_Backend_SRS.md): requirements (SRS)
- [`DOCS/`](DOCS): the original HTML prototype. Its location text ("Port Harcourt") is wrong; the school is in Enugu State.
- [`backend/`](backend): the API. Setup, configuration, Docker and testing are in [`backend/README.md`](backend/README.md).
- [`docker-compose.yml`](docker-compose.yml): PostgreSQL and the API for local development.

## Current status

Phase 1 (backend foundation) is built. No school features exist yet: authentication, students,
teachers, results and fees come in later phases.
