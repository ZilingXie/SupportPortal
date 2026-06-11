# Vendored cusmem

This directory contains a vendored copy of the cusmem/Graphiti-based graph RAG project used as a local integration source for SupportPortal experiments.

Import notes:
- Source code, package metadata, docs, examples, schemas, and the Apache-2.0 license are preserved.
- Local secrets and deployment overrides are not vendored.
- Local experiment outputs and copied benchmark artifacts are excluded from this repository.
- Local scripts with hard-coded private service addresses were excluded pending sanitization.
- The vendored code is not wired into SupportPortal runtime by this import alone.

Before runtime integration, add a narrow adapter, sanitize configuration, and use environment-driven credentials only.
