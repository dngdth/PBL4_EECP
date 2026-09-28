# Final demo guide

Use protected non-example secrets and a trusted production CA. Steps marked
**Windows VM only** require an isolated elevated Windows target and must not be run
on an administrator workstation carrying real network policy.

1. Start PostgreSQL and verify its health check.
2. Start Redis and verify `PING` plus Backend presence health.
3. Run migrations, then start the Backend; `/health` must report dependencies.
4. Start the Local Gateway with WSS, its Gateway credential, Agent credential map,
   and writable SQLite buffer; verify uplink ONLINE.
5. Start the Vite/Nginx Web image and open the Dashboard.
6. **Windows VM only:** install/start Agent Service under SCM LocalSystem with policy
   and command verification keys and a persistent state directory.
7. Start Agent Client as a normal user with no signing key; verify Gateway presence.
8. Log in as Examiner and demonstrate an unauthorized role receives 403.
9. Create a Session and assign the Agent.
10. Deploy policy; show its canonical expected hash.
11. Trace command ID Backend → Gateway → Client → Named Pipe → Service.
12. Show successful ACK and identical Service/ACK/Backend applied hash.
13. **Windows VM only:** verify blocked domain in the EECP hosts section.
14. **Windows VM only:** verify deterministic outbound Firewall IP/CIDR rules and an
    unrelated Firewall rule remains.
15. Start a forbidden test process; show sensor detection/Service enforcement.
16. Show the persisted Incident and audit entry in the Dashboard.
17. Disconnect the Gateway Backend uplink while keeping Agent LAN connectivity.
18. Emit Events and show the Gateway SQLite pending/backlog health.
19. Restore WAN/uplink and wait for backlog zero.
20. Show Events flush exactly once logically while delivery duplicates are deduped.
21. Finish the Session to issue authorized `RESTORE_BASELINE`.
22. **Windows VM only:** verify hosts/USB baseline and only EECP Firewall rules are
    restored; a forged RESTORE must be denied.
23. Verify the audit hash chain and Session summary.

For a repeatable non-mutating CI regression run `scripts/release-e2e.ps1`. For the
real target checklist run `scripts/windows/validate-release.ps1
-ConfirmIsolatedTestMachine`; the script intentionally refuses mutation without
Windows, elevation, and explicit confirmation.
