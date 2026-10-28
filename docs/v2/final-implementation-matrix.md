# Final implementation matrix

`Validated` means the cited automated or isolated integration evidence exists.
Windows OS rows remain PENDING until an isolated elevated VM run.

| Capability | Implemented | Validated | Environment | Known limitation | Evidence |
|---|---|---|---|---|---|
| Protocol v2 | Yes | Yes | CI/local | v2 only | protocol contract tests |
| Backend authority | Yes | Yes | API integration | single Backend topology | full vertical tests |
| PostgreSQL | Yes | Yes | Docker isolated | no HA | Phase 8.1 external tests |
| Redis presence | Yes | Yes | Docker isolated | ephemeral; loss degrades presence | Phase 8.1 external tests |
| Local Gateway | Yes | Yes | loopback/Docker | single process | Gateway integration/benchmark |
| TLS/WSS | Yes | Yes | ephemeral test CA | production CA pending | Phase 8.1 validation |
| Gateway SQLite Event buffer | Yes | Yes | loopback | disk loss loses local backlog; 250 Event timeout | Phase 7 tests/Phase 9 benchmark |
| Agent Client | Yes | Yes | cross-platform tests | Windows deployment pending | Client/Gateway tests |
| Sensors | Yes | Yes | fake process/network inputs | no kernel sensor | Phase 7 tests |
| Named Pipe | Yes | Partial | CI loopback | real ACL/SCM PENDING | Phase 4 tests |
| Agent Service | Yes | Partial | process lifecycle | LocalSystem PENDING | Service tests |
| hosts enforcement | Yes | Partial | temp file | real hosts PENDING | Phase 6 tests |
| Windows Firewall | Yes | Partial | fake adapter | elevated real rules PENDING; no WFP | Phase 6 tests |
| process enforcement | Yes | Partial | unit/process fake | target process PENDING | enforcement tests |
| USB enforcement | Yes | Partial | fake registry/service | hardware optional/PENDING | enforcement tests |
| Event retry/dedupe | Yes | Yes | integration/WAN benchmark | Agent→Gateway pre-persistence window | Phase 7 + WAN artifact |
| RBAC/session scope | Yes | Yes | HTTP integration | configured role model | Phase 8/9 security tests |
| Agent credential | Yes | Yes | WSS integration | configured rotation | auth tests |
| Gateway credential | Yes | Yes | WSS integration | configured rotation | auth tests |
| Policy signature | Yes | Yes | Service unit/integration | HMAC symmetric | Phase 8/9 tests |
| Privileged command authorization | Yes | Yes | Service unit/integration | HMAC symmetric | Phase 9 tests |
| Persistent replay protection | Yes | Yes | Service recreation | local journal availability | Phase 9 restart test |
| Docker images/compose | Yes | Yes | Docker Desktop | host-specific deployment | Phase 8.1 validation |
| Windows SCM/recovery | Yes | PENDING | isolated elevated VM | current host unsuitable | Windows validation script |
| Kill Client enforcement persistence | Designed | PENDING | isolated elevated VM | Service must remain running | Windows validation script |
| Restore/preserve unrelated rules | Yes | Partial | fake adapter | real OS PENDING | Phase 6 tests/script |
