"""Identity: users, roles, permissions, role_permissions, user_roles.

The `PermissionService` is the single chokepoint for authorization
decisions. Every mutating route should call `assert_can(user, action)`
or use the `require_permission` FastAPI dep.

W0 lands the table-only seed (global permission catalog). Per-tenant
roles + role-permission grants are seeded in W6's tenant onboarding
flow.

Import submodules directly: `from identity.models import User`, etc.
"""
