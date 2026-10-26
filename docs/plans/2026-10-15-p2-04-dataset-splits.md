# P2-04 dataset split completion

Goal: source-aware previews and suggestions with reproducible frozen policy evidence, without schema changes.

Scope: backend/backend/services/{dataset_splits,detection_export}.py, backend/backend/api/export_routes.py,
frontend/src/components/ProjectExport.tsx, frontend/src/api/export*.ts, new P2 tests and this task's docs only.

1. Read all training_package callers, snapshot routes, JSONB source metadata and export regressions.
2. Add failing P2 tests for transitive explicit source grouping, unknown metadata, per-class image budgets,
   independent holdouts, read-only previews, scoped JSONB editing and immutable snapshot evidence.
3. Implement a pure split service, shared thresholds, preview endpoint and narrowly scoped source editing.
   Keep legacy freeze policy optional and unsplit packages unchanged. No filename inference.
4. Reuse ProjectExport for manual preview, automatic suggestion, training budget, strictness and source editing.
5. Run new and old isolated tests, TypeScript/build checks, and a unique temporary PostgreSQL test database.
   Record actual evidence and limits; no commits or business-data writes, no real-test-set acceptance claim.
