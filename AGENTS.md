# Collaboration Rules

## UI Design Source Of Truth
1. All new or refactored UI under `ui/` must follow `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.
2. `design.md` is the canonical UI design language and component/style source of truth for this repo.
3. `docs/agent.md` is kept only as a compatibility redirect and must not be treated as an independent spec.
4. If a UI change needs new tokens, component rules, or page-level exceptions, update `design.md` before implementing the code.

## Container Handling After Changes
1. After completing code changes, always restart containers with compose down first, then compose up:
   - `podman-compose -f deployment/docker-compose.single-host.yml down`
   - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
2. After restart, verify running status:
   - `podman-compose -f deployment/docker-compose.single-host.yml ps`
