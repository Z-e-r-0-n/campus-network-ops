# Periodic configuration review

The rule engine is implemented. Complete evidence collection and scheduled review integration are in development. See [Architecture](ARCHITECTURE.md) for component status.

Configuration review evaluates the accepted network design, observed configuration, measured demand and administrator policies. It is separate from incident diagnosis. Both modes use the same approved execution machinery.

## Operation

Run a light evaluation daily against collected evidence; run a complete review weekly. Re-evaluate affected rules after an accepted topology/policy change or a verified configuration change. Reuse fresh observations instead of repeatedly fetching entire configurations. The operator can request a review with a visible read budget.

Each finding contains:

- The rule and affected devices, interfaces, VLANs or services.
- Current behavior, intended behavior and the evidence supporting the difference, with timestamps.
- Practical benefit, possible disruption, exceptions and prerequisites still missing.
- A proposed configuration diff where a qualified adapter can produce one; otherwise a precise investigation or physical-work task.
- A route to a supported repair plan, or an explicit reason automatic execution is unavailable.

Rule evaluation is deterministic. AI may explain a finding, identify a possible exception or request a bounded evidence check; it cannot turn an unevidenced recommendation into a confirmed finding.

The administrator can **Prepare change**, **Request evidence**, **Defer until a date**, or **Accept an exception** with a reason and review date. Preparing a change does not approve execution. An exception applies only to the named rule and scope. Deduplicate by rule, scope and normalized condition; a daily review updates the existing finding. Deferred findings resurface only on expiry or a material change, such as increased impact or a changed baseline. Show why they resurfaced.

## Rule catalogue

All rules are required product capabilities, but application to a device depends on verified evidence and feature support. “Supported change” means an adapter qualified for that exact platform and action; it is not blanket permission to write.

| ID | Review | Evidence required | Useful result and change boundary |
|---|---|---|---|
| C01 | Authorized DHCP paths | Approved server/relay addresses, VLANs, actual paths, port roles, platform capabilities | Propose DHCP snooping or equivalent enforcement only on supported segments; show unmanaged segments still exposed |
| C02 | DHCP snooping side effects | Relay behavior, Option 82 policy, trust direction, rate limits, binding persistence and legitimate server exceptions | Identify settings that would drop legitimate offers or break other protections; pilot and test before expanding |
| C03 | Loop-prevention coverage | STP mode/root intent, port roles, BPDU observations, link aggregation | Propose root/edge/guard corrections; missing LLDP alone does not establish an edge port |
| C04 | VLAN/trunk consistency | Accepted VLAN use, both ends of a link, native/tagging configuration | Identify mismatched or unnecessarily exposed VLANs; check management and dependent services before pruning |
| C05 | Link and LAG configuration | Speed/duplex, optics limits, member state, peer configuration and error/demand history | Distinguish configuration correction from an optic/cable replacement; peer capability is a prerequisite for speed changes |
| C06 | Single points of failure | Accepted physical links, routing/HA intent and measured demand | Name unprotected paths and likely affected services; a spare device or drawn second link is not proven failover |
| C07 | Route and gateway behavior | Current routes, next-hop reachability, tracking/HA rules, return paths and policy routing | Identify loops, missing withdrawal or unintended paths; supported changes need a tested recovery route |
| C08 | Address and subnet consistency | Infrastructure assignments, masks, DHCP scopes, reservations and gateway roles | Find overlaps, stale infrastructure records and conflicting assignments without inventorying clients |
| C09 | Time and event reporting | Clock offsets, NTP configuration/reachability, syslog destination and actual receipt | Suggest reachable approved servers and event destinations; verify delivery, not just configuration text |
| C10 | Management access boundaries | Required management methods, approved source networks, actual account privileges and host identity | Recommend scoped access and dedicated accounts while preserving required password/HTTP/Telnet access |
| C11 | Key/account maintenance | Enrollment records, fresh key-auth test, account scope, key age/revocation policy | Suggest rotation or privilege correction; never remove the only verified recovery access |
| C12 | Saved configuration and backups | Running/startup comparison where supported, successful backup/readback, restore qualification | Highlight unsaved changes and unqualified recovery; hash equality does not imply service health |
| C13 | Resource and capacity margin | Demand percentiles, discard/error rates, CPU/DRAM trends, hardware limits | Explain sustained low headroom and likely bottlenecks; do not infer a memory leak from a flat high value |
| C14 | Monitoring and test coverage | Expected check/source coverage, NMS poll age, credentials, probe placement | Name blind segments, stale polls and unavailable path tests; propose access/probe work rather than “all healthy” |
| C15 | Configuration drift | Versioned accepted intent, approved change history and normalized observed configuration | Separate intentional changes awaiting review from unexplained drift; never silently restore the baseline |
| C16 | Firewall/service dependencies | Approved service intents, firewall rules/order, gateways, NAT and observed path tests | Flag rule/intent conflicts for review; no automatic removal based only on a low hit count |
| C17 | IPv6 control policy | Explicit IPv6 deployment decision, router/DHCPv6 roles, multicast requirements, platform support | Propose RA/DHCPv6 protections only when IPv6 policy is known; IPv4 DHCP rules do not cover IPv6 |

## DHCP example: what the user actually gets

“VLAN 802 should accept DHCP offers from the approved DHCP service through these verified uplinks. This access port currently permits untrusted offers.” The finding must name the evidence and exact trust path. It cannot assume the server connects directly to the building switch.

The proposed plan contains platform-specific commands/configuration operations, scope, current values, the authorized-offer test, the unauthorized-offer test in a controlled lab, management-connectivity checks, rollback operations and a pilot boundary. It also identifies clients that could be affected by relay or Option 82 changes. Do not enable dependent protections such as dynamic ARP inspection without validated bindings and static-address exceptions.

For a downstream unmanaged segment, say: “This change protects traffic crossing the managed boundary. It cannot stop exchanges wholly inside the unmanaged segment.” A historical containment action must not become a permanently open seeded incident or a recurring recommendation without fresh evidence.

## Ordering recommendations

Order by evidenced service impact, then feasibility and dependency. An inaccessible management interface or unknown port role comes before a change that depends on it. Display urgency, evidence strength and execution availability separately. Do not invent a single network score or claim confidence from the number of models agreeing.

After a verified change, rerun the affected rule and its service checks. Resolve only when the condition has actually cleared. If an administrator intentionally changes the design, propose a separate baseline update; never rewrite accepted intent simply to make a finding disappear.
