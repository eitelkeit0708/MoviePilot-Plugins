# Admission Condition Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw predicate AST form with a lossless, readable condition editor while preserving the existing evaluator, global scope, configuration merge, and save protections.

**Architecture:** Keep the persisted AST unchanged. A focused `predicate-editor.mjs` module owns draft IDs, tree edits, exact units, validation, and summaries; Vue components only render and route actions. A small authenticated admission-test endpoint calls the existing Python evaluator with optional trace collection so the browser never reimplements predicate semantics.

**Tech Stack:** Vue 3, native HTML controls, Node test renderer, Python/FastAPI/Pydantic, existing `regex` evaluator.

---

### Task 1: Lossless predicate draft operations

**Files:**
- Create: `plugins.v3/subscribetter/frontend/src/predicate-editor.mjs`
- Test: `plugins.v3/subscribetter/frontend/test/predicate-editor.test.mjs`

- [ ] Add failing tests for CE-T01–T10 and CE-T18/T21: AST round trip, all/any mutation, compatible operator changes, disabled-session restoration, incomplete nodes, exact B/KiB/MiB/GiB conversion, regex preservation, stable IDs, undo, and advanced-node preservation.
- [ ] Run `node --test test/predicate-editor.test.mjs` and confirm the new assertions fail because the module does not exist.
- [ ] Implement the smallest pure functions needed by those tests; store editor-only IDs and unit choices outside the serialized AST.
- [ ] Re-run the focused test and keep every legacy value byte-for-byte equivalent when untouched.

### Task 2: Row and group editor

**Files:**
- Replace: `plugins.v3/subscribetter/frontend/src/PredicateEditor.vue`
- Modify: `plugins.v3/subscribetter/frontend/src/style.css`
- Test: `plugins.v3/subscribetter/frontend/test/components.test.mjs`

- [ ] Add failing mounted-component checks for field → operator → value order, named groups, real disclosure targets, keyboard controls, compact mobile structure, validation messages, predictable focus, and no raw value-type selector.
- [ ] Run the focused component test and confirm failure against the old four-row editor.
- [ ] Render `fieldset`/`legend` groups and typed condition rows from the draft module; keep deep groups readable with capped indentation and local group navigation.
- [ ] Add explicit condition/group actions, collapse summaries, undo, and validation events; do not serialize incomplete rows.
- [ ] Re-run focused tests at desktop, 390px, and 320px fixtures.

### Task 3: Global scope and shared rule list

**Files:**
- Modify: `plugins.v3/subscribetter/frontend/src/Policies.vue`
- Modify: `plugins.v3/subscribetter/frontend/src/ConfigEditor.vue`
- Test: `plugins.v3/subscribetter/frontend/test/components.test.mjs`

- [ ] Add failing tests for CE-T06/T13–T16/T22: global scope label, session disable/restore, explicit clear, compact shared-rule list, validation blocking save, and precise save-scope text.
- [ ] Confirm the old toggle resets to `{literal:true}` and the old rule list renders every editor.
- [ ] Cache the disabled expression in the current component session, present global limits as its own object, and edit one shared definition at a time.
- [ ] Route child validity to the module footer so invalid/incomplete predicates cannot be saved.
- [ ] Re-run configuration merge, refresh, conflict, and leave-flow regressions.

### Task 4: Authoritative admission explanation

**Files:**
- Modify: `plugins.v3/subscribetter/policy.py`
- Modify: `plugins.v3/subscribetter/ui.py`
- Modify: `plugins.v3/subscribetter/frontend/src/PredicateEditor.vue`
- Modify: `plugins.v3/subscribetter/frontend/src/contract.json`
- Test: `tests/v3/subscribetter/test_policy.py`
- Test: `tests/v3/subscribetter/test_management.py`
- Test: `plugins.v3/subscribetter/frontend/test/components.test.mjs`

- [ ] Add failing backend tests for CE-T11/T12/T17: short-circuit trace, missing evidence, invalid regex/reference, bounded timeout, manual sample, candidate sample, and no execution side effects.
- [ ] Add failing UI tests that change expression/sample while a request is pending and reject the stale result.
- [ ] Extend the existing evaluator with optional bounded trace collection; unvisited short-circuit branches return `NOT_RUN` without being evaluated.
- [ ] Add authenticated `POST /policies/admission-test`, accepting a draft policy plus either a bounded manual sample or candidate key.
- [ ] Render row-linked PASS/FAIL/MISSING/NOT_RUN/ERROR results and distinguish this from full policy simulation.
- [ ] Regenerate `contract.json`; run focused backend and frontend checks.

### Task 5: Full verification and isolated-host review

**Files:**
- Modify: `docs/subscribetter/external-review/IMPLEMENTATION_REVIEW_20260929.md`
- Create: `docs/subscribetter/external-review/ADMISSION_EDITOR_EVIDENCE_20260929.json`

- [ ] Run the 22 CE checks, full frontend tests, full Python tests, `compileall`, production build, and `git diff --check`.
- [ ] Deploy only to `moviepilot-v3-subscribetter-test`; keep tracking disabled and dry-run enabled.
- [ ] Inspect real DOM and host rendering at desktop, 390px, and 320px; record screenshots only if capture succeeds.
- [ ] Record PASS/FAIL/BLOCKED/NOT_RUN separately for pure tree, mounted component, evaluator contract, real DOM, and host operations.
- [ ] Commit and push `codex/subscribetter-v3` after verification.
