# Deployment platforms implementation plan

**Goal:** Register each platform once, discover its model slots, and prepare multiple versions across multiple platforms with reusable mappings.

**Architecture:** Store platform configuration as a dedicated kind in the existing deployment target table. Resolve discovered slots into existing per-model deployment records so audit, validation, SHA256 package reuse and rollback remain available. Serialize updates sharing a service; reuse per-batch packages per source model.

**Tech Stack:** FastAPI, SQLAlchemy, existing SSH-manager executor, React and Ant Design.

1. Add isolated tests for platform registration/discovery, two models across twenty platforms, mapping persistence, tenant isolation, missing or duplicate slots, and shared-service serialization. Verify missing functionality fails.
2. Add platform schemas, service and routes; extend read-only discovery for Docker/K8s services and model directories. Import existing configurations without changing historical records.
3. Add platform and version selection with an explicit model matching table and one preparation action; retain image and historical workflows.
4. Run targeted backend tests, frontend build and browser fixtures covering multiple models, twenty platforms, tomorrow's version reuse, and no automatic activation.
5. Compare local and 115 files, check active tasks, back up and deploy, then run isolated Linux tests, authenticated read-only checks and browser validation. Do not publish actual business models during acceptance.

No commits or image builds are required. Execute this plan in the current authorized workspace, preserving its existing uncommitted changes.
