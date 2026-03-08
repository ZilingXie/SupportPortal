# Collaboration Rules

## Container Handling After Changes
1. After completing code changes, always restart containers with compose down first, then compose up:
   - `podman-compose -f deployment/docker-compose.single-host.yml down`
   - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
2. After restart, verify running status:
   - `podman-compose -f deployment/docker-compose.single-host.yml ps`
