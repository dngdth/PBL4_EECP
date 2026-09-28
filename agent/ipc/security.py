from __future__ import annotations

# No World (WD) or Anonymous (AN). Authenticated local users are the temporary
# client-principal strategy until enrollment provisions a dedicated Agent SID/group.
PIPE_ACL_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;AU)"
PIPE_SERVICE_PRINCIPALS = ("LocalSystem", "Builtin Administrators")
PIPE_CLIENT_PRINCIPAL_STRATEGY = "Authenticated Users, local-only pipe"


def validate_pipe_acl_sddl(sddl: str = PIPE_ACL_SDDL) -> None:
    upper = sddl.upper()
    if ";;;WD)" in upper or ";;;AN)" in upper:
        raise ValueError("pipe ACL must not grant World or Anonymous access")
    if ";;;SY)" not in upper:
        raise ValueError("pipe ACL must include LocalSystem")
    if ";;;AU)" not in upper:
        raise ValueError("pipe ACL must include the intended client principal strategy")
