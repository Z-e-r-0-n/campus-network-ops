# Roadmap

The project is in active development. Milestones prioritize a complete operational workflow across discovery, measurement, review and verified maintenance.

## Milestones

| Milestone | Scope | Completion criteria |
|---|---|---|
| Discovery and inventory | Extend platform parsers, classification and NetBox reconciliation | Scoped discovery through corrected inventory publication, including partial failures and conflicting identities |
| Device enrollment | Bootstrap helper, account/privilege profiles, key installation, rotation and recovery | Verified dedicated access and persistence on each supported platform |
| Measurement coverage | Extend H01–H04 local collection to remote/VRF sources, physical counters, infrastructure services and approved load tests | Source-specific evidence with correct timestamps, units and coverage across supported checks |
| Approved maintenance | Isolated executor, scoped access, backup integration, independent verification and recovery controls | One complete supported change/recovery workflow, followed by broader runbook qualification |
| AI diagnosis | Qualify gateway/account restrictions, live free-provider rollover and diagnostic quality; extend scoped context | Application workflow is implemented; two real eligible providers must demonstrate bounded rollover with no paid route |
| Configuration review | Scheduled evidence collection, findings, exceptions and supported change preparation | Periodic recommendations traceable to accepted policy and current observations |
| Production deployment | Installer, TLS, service privileges, notifications, upgrades and recovery | Repeatable installation and demonstrated restore on a separate host |

## Action families

| ID | Action | Status |
|---|---|---|
| RB01 | Dedicated account and key enrollment | Typed plan; adapter pending |
| RB02 | NTP/syslog destination correction | Cisco prototype; device qualification pending |
| RB03 | Persist reviewed configuration | Typed plan; standalone action qualification pending |
| RB04 | Authorized DHCP enforcement | Typed parameters; platform and path verification pending |
| RB05 | Trunk/VLAN correction | Typed parameters; peer and management-path verification pending |
| RB06 | Route correction | Typed parameters; adapter and return-path verification pending |
| RB07 | Recover approved configuration fields | Typed request; explicit recovery workflow pending |

The existing runner handles a single typed action. Support for an action family requires its platform adapter, precondition checks, reconciliation, independent verification and recovery behavior.

## Design direction

- Existing LibreNMS monitoring and NetBox intended inventory retain distinct responsibilities.
- Device support is capability-based, with CLI adapters for platforms lacking suitable management APIs.
- Service requirements and network intent are installation-specific, versioned inputs.
- Diagnostic models propose registered operations; application policy and approved plans govern execution.
- Physical faults can produce inspection or replacement work items instead of configuration changes.
- Workflow orchestration remains in Temporal. Additional libraries are evaluated against concrete integration needs.
- Test fixtures and the sample workspace remain separate from operational inventory.

## Integration work

Nornir's dispatch role remains under evaluation. Oxidized integration requires model-level compatibility testing. Device-native diagnostics and iperf3 require source qualification and explicit test budgets. OmniRoute integration includes provider eligibility, bounded retries, route identity and failure handling.

Implementation details are in [ARCHITECTURE.md](ARCHITECTURE.md). The [check catalogue](HEALTH-CHECKS.md), [configuration rules](CONFIGURATION-REVIEW.md), [execution lifecycle](EXECUTION.md) and [acceptance criteria](ACCEPTANCE.md) define the required behavior.
