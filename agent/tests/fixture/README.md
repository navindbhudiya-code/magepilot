# Synthetic test fixture (NOT real code)

This `Vendor/Faq` module is a **hand-written, synthetic Magento 2 module** used only by the
agent's deterministic tests (`agent/tests/run_tests.py`) to exercise indexing, search, grep,
and file reads against known content.

- **No real, client, or proprietary code.** Every file is a generic textbook pattern.
- `Faq` is one of the project's approved neutral worked entities.
- Bodies are neutral (a flat `5.0` surcharge, a standard `getById`) — no business logic, secrets, or PII.

It exists so tests have a stable, public-safe codebase to run against. Do not add real code here.
