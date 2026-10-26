# YOLO Import and Training Usability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Import annotated YOLO images from image management, download training artifacts, delete inactive task records, and present readable training logs.

**Architecture:** User clarified that imports must not bind projects. Extend resumable uploads with YOLO mode and persist a reusable imported annotation on each library image; adding to a detection project optionally copies that source into independent project annotation/history. Reuse ssh-manager for artifact download staging; deletion affects only inactive platform records, preserving remote files. Normalize terminal logs and preserve stdout/stderr ordering.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, existing storage/SSH MCP bridge, React/Ant Design.

### Task 1: YOLO archive import
- Files: `backend/backend/services/yolo_import.py`, `services/image_uploads.py`, `api/image_upload_routes.py`, `models/image_upload.py`, `alembic/versions/020_yolo_import.py`, `frontend/src/components/ImageUploadDialog.tsx`, `pages/AssetsPage.tsx`, `api/imageUploads.ts`.
- Write failing tests for ZIP pairing, class order, normalized coordinates, negative samples, invalid/missing labels, archive traversal, existing annotation conflicts, resume and project scoping.
- Add nullable `annotation_format` and default-false trust receipt metadata; add nullable `images.imported_annotation` JSONB. Keep ordinary resume keys compatible; include format/trust in annotated import keys.
- Validate all archive label files before image writes, then checkpoint imports per image. Only detection boxes, explicit empty TXT for negatives, `data.yaml` names or `classes.txt`; no segmentation conversion. User clarified: optional explicit administrator trust makes newly imported annotations directly usable for training/validation, with import provenance in approval history; default remains pending.
- UI: project-free YOLO library import; both add-to-project flows offer an explicit copy checkbox, matching category names, retaining import trust and protecting existing project annotations. Verify source/project independence and isolated browser upload fixtures.

### Task 2: Download and delete
- Files: `services/training_runs.py`, `services/training_executor.py`, `scripts/training_mcp_bridge.mjs`, `api/training_run_routes.py`, `frontend/src/api/training.ts`, `pages/TrainingPage.tsx`.
- Write failing tests for tenant/auth scope, traversal/symlinks, immutable metadata checks, inactive-only delete, holds and referenced validation provenance.
- SFTP to owned local temp directory, verify SHA/size against remote metadata, respond as a file and clean temp data. Never delete remote files.
- Explicit deletion confirmation; reject active/unknown/held runs. Preserve validation snapshots.

### Task 3: Logs and verification
- Files: `services/training_executor.py`, `services/training_logs.py`, `frontend/src/components/TrainingLogs.tsx`, related tests/scripts/docs.
- Reproduce carriage-return/ANSI/repeated-progress disorder in tests. Capture both Docker streams together and normalize terminal output, retaining errors.
- Show a fixed-width, horizontally scrollable log panel, line numbers, wrap toggle, refresh, follow-bottom; avoid changing training commands.
- Verify focused and full backend tests, disposable PG migration, build/lint, desktop/mobile browser flows; enable local backend only after checking activity and backing up affected data.
- No commits or deletion of business tasks as part of verification. Remote smoke checks only read existing artifacts/logs.

Commands: `backend/backend/.venv/Scripts/python.exe -m pytest backend/backend/tests/test_yolo_import.py backend/backend/tests/test_training_outputs.py -q`; broader regressions after focused tests pass; `npm run build` and `npm run lint` in frontend.
