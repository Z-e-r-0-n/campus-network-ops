# Acceptance criteria

These criteria define release verification across application behavior, device integration and deployment. Implementation status is recorded in [Architecture](ARCHITECTURE.md); development milestones are in the [Roadmap](ROADMAP.md).

## Delivery gates

| Gate | Required result |
|---|---|
| G0 | Reproducible environment and an initially unconfigured inventory |
| G1 | Working login, durable state, audit and supervised processes with tested privilege boundaries |
| G2 | Scoped discovery, identity correction and consistent reviewed inventory publication |
| G3 | Qualified dedicated-account enrollment, key login, rotation and recovery |
| G4 | Real source-specific measurements and periodic configuration review with explicit coverage |
| G5 | Evidence-based diagnosis and verified bounded free-provider rollover |
| G6 | Approved actions execute through actual isolation and independent verification/recovery |
| G7 | Supported hardware, interface, scale and deployment flows qualified |
| G8 | Repeatable new-PC installation and protected restore demonstrated without automatically replaying old write jobs |

## Acceptance cases

Algorithm tests use deterministic fixtures. Device qualification exercises actual platform behavior, including failed or uncertain commands. Integration tests verify the complete user workflow.

| ID | Test and required outcome |
|---|---|
| A01 | Fresh install: choose administrator password, sign in/out, change password, expire/revoke sessions; no fixed default credential |
| A02 | All privileged APIs reject missing/expired session, CSRF and stale revision; repeated login throttles without leaking account details |
| A03 | Seed traversal handles cycles, duplicate IP aliases, a changed identity and missing credentials without repeated exploration or auth attempts |
| A04 | Out-of-scope candidates, maximum depth/node count and time budgets stop traversal visibly; no packet/auth leaves the approved target scope |
| A05 | NMS misclassification and conflicting CLI identity produce a reviewable conflict; a label alone cannot prove physical adjacency |
| A06 | Operator adds a missing node, corrects a link, splits an alias and accepts a subset; original observations remain intact |
| A07 | NetBox publication failure, retry and concurrent external edit preserve a consistent accepted revision and show pending/conflicting state |
| A08 | Dedicated-account enrollment verifies fresh key login, minimum supported privilege and persistence; failed verification rolls back newly created scope |
| A09 | Unsupported key algorithms/accounts do not trigger automatic weakening or password fallback; administrator HTTP/Telnet/password access remains available |
| A10 | Key rotation, host-key mismatch, service reboot and missing key recovery produce explicit states; desktop-session closure does not break the service |
| A11 | Source/VRF selection produces the intended directed test, including an unavailable source; control-plane ping is not reported as end-to-end forwarding quality |
| A12 | Counter resets/wrap, stale NMS polls, API refetches, missing metrics and low sample count cannot generate a false clear or misleading rate |
| A13 | Every H01–H30 check has prerequisite, parser, scheduling and result tests; unsupported measurements remain unknown with a coverage reason |
| A14 | Throughput/UDP/load jobs obey duration, rate, target and shared-path limits; absent authorization or unknown path blocks the job |
| A15 | Repeated related symptoms join one incident; recovery requires the specified evidence; later recurrence links to historical episodes |
| A16 | All C01–C17 rules distinguish confirmed condition, exception and missing evidence; daily reevaluation does not duplicate or constantly reopen deferred findings |
| A17 | DHCP pilot tests include legitimate relay/Option 82 behavior and downstream unmanaged limitations; no claim that a boundary drop stops all local exchanges |
| A18 | Free-route tests cover 429/Retry-After, 5xx, timeout, invalid provider key, gateway auth failure, budget exhaustion and expired pricing eligibility |
| A19 | Two eligible providers demonstrate controlled live rollover without a paid route; otherwise live rollover stays an explicit incomplete deployment dependency |
| A20 | Model input/output and error logs contain no credentials/raw backups; malicious device text cannot request arbitrary tools, commands or scope expansion |
| A21 | AI unavailable: monitoring, deterministic review and previously approved execution still work; queued diagnosis uses fresh context when resumed |
| A22 | Approval binds exact plan/actions/targets/versions; changed configuration, expired approval, revoked approval or different parameter fails before a write |
| A23 | Double-click/replayed request/outbox retry starts one execution record; conflicting jobs cannot mutate the same device concurrently |
| A24 | OPA unavailable/undefined, forged capability, wrong target, forbidden command and worker egress outside scope are denied independently of model behavior |
| A25 | Worker or network loss after a command but before acknowledgement becomes outcome unknown; reconciliation prevents duplicate application |
| A26 | RB02 changes a real lab configuration, verifies the intended result, then an induced verification failure triggers exact approved recovery and service checks |
| A27 | RB01–RB07 each have typed plan, error, partial-application, recovery and platform support tests; unsupported actions cannot be enabled through UI/API |
| A28 | Stop, global pause, lease expiry and manual-recovery path work without AI; cancellation does not misreport an in-flight command as undone |
| A29 | UI flows pass keyboard, contrast, small-screen and loading/empty/stale/failure review; real browser screenshots show usable map/table and approval screens |
| A30 | 1,000-node offline inventory with cycles and aliases completes within configured limits; API queries and UI graph expansion are paginated/bounded; scheduler does not emit a catch-up storm |
| A31 | Fresh second-PC install or isolated equivalent host restores encrypted application data, topology, history, config backups and required credentials with documented recovery inputs; old live jobs do not automatically resume |
| A32 | Upgrade failure and database migration recovery preserve application data, evidence and backups; retention is explicit and does not erase required history |
| A33 | A fresh installation starts with unconfigured sources and no seeded operational inventory, incidents or active schedules |
| A34 | Fresh accepted evidence creates a new incident/recommendation without editing code; removing all sample data leaves a truthful empty state |
| A35 | Notification failure cannot block checks or execution; maintenance suppresses configured notifications without falsifying results or silently approving changes |
| A36 | Configuration snapshot restore detects intervening edits and preserves unrelated fields; unavailable recovery access blocks an unattended high-impact change |

## Requirements traceability

| Requirement | Gates | Acceptance |
|---|---|---|
| R01 Discovery | G2 | A03–A05, A30 |
| R02 Reviewed topology | G2 | A05–A07, A34 |
| R03 Dedicated access | G3, G6 | A08–A10, A27 |
| R04 Direct measurements | G4 | A11–A14 |
| R05 General incidents | G4 | A12, A15, A34–A35 |
| R06 Free AI rollover | G5 | A18–A21 |
| R07 Approved autonomous response | G6–G7 | A22–A28, A36 |
| R08 Configuration review | G4, G6 | A16–A17, A27 |
| R09 History and recovery | G4, G7 | A26, A31–A32, A36 |
| R10 Professional interface | G1–G7 | A01–A02, A06, A29, A34 |
| R11 Repeatable installation | G0–G1, G7 | A10, A30–A32 |
| R12 Installation state and migration | G0, G8 | A32–A33 |


## Restore and commissioning verification

Restore testing covers application PostgreSQL, Temporal, NetBox, configuration history and required authentication material on an isolated host. The manifest identifies included components, versions and recovery inputs. Restored write jobs require reconciliation before execution.

Verification covers decryption, schema migration, login, accepted inventory, operational history and read access. Off-host recovery includes retrieval from the configured external backup destination. Upgrade tests exercise migration failures and recovery without losing existing records.

Platform qualification progresses from read-only collection to supported enrollment and maintenance actions. Results identify the hardware, firmware, adapter version and operations exercised, together with remaining coverage gaps.
