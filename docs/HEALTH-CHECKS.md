# Health test catalogue

This catalogue defines target coverage. H01–H04 support reviewed schedules from verified local sources, ICMP collection and rolling latency, TCP observations, and HTTP/HTTPS or SSH response checks. Other checks have evaluators; their collectors and probe integration remain in development. See [Measurement configuration](MEASUREMENTS.md) for current behavior and [Architecture](ARCHITECTURE.md) for component status.

## What health means

Measure availability, packet loss, round-trip delay and variation, throughput where safely measurable, routing correctness, link integrity, service correctness and recovery behavior. Low ping alone does not prove the network is healthy. A switch may forward normally while its management CPU answers ping slowly. Physical-layer counters/optics can suggest a path fault; they cannot identify a dirty connector without supporting tests.

Every result includes check/version, source device or probe, source interface/address/VRF, destination, path/baseline revision, timestamp/clock quality, measurement duration, samples, units, loss denominator, capability and private evidence reference. Required canonical result states: `passed`, `failed`, `unknown`, `skipped` or `timed_out`, with reason codes such as `inconclusive`, `unsupported_source`, `policy_blocked` and `stale_input`. Absent counters remain unknown.

## Probe placement and scheduling

Create a directed source→destination coverage matrix. Device-native source ping is used only on tested platforms; Linux/FreeBSD infrastructure probes or approved dedicated test hosts fill forwarding-path gaps. Results identify the actual measurement origin. A source with no usable source/VRF support is marked unsupported.

A full N-node directed matrix has N(N−1) pairs. Continuous coverage focuses on adjacent routed dependencies and critical services, with remaining eligible pairs rotated within the configured budget. Show when each pair last ran and which paths lack coverage. For 1,000 nodes use a partitioned schedule and declared sampled coverage, not a promise of fresh all-pairs results. Persist schedules, avoid synchronized bursts and back off on device pressure.

Initial limits: one active test job per legacy device; one echo/second/source, 20 aggregate echoes/second/installation; 10 samples per ordinary reachability run. Native CLI diagnostic sessions no more than once per five minutes/device unless a reviewed adapter/profile permits otherwise. Prefer existing NMS counters to repeated SSH when their poll time and metric semantics are valid. Critical paths from dedicated probes may run every minute under their separate budget. Skip rather than queue unbounded stale tests; record the skip reason.

Service targets and thresholds are installation-specific. Calibrate a provisional baseline over 24 hours and review representative busy/quiet periods over seven days. Explicit service requirements take precedence over learned historical performance. Initial warning logic can use three failing windows and three recovering windows; confirmed link-down events can open immediately with source confidence. Threshold changes are versioned and cannot erase previous failures.

## Test catalogue

Cadences below are proposed defaults subject to device capability, supported sensors and approved load budget. “Event” means an authenticated/validated relevant observation, not arbitrary webhook text. Tests read state unless explicitly described as an approved active test.

| ID | Check and measurements | Source / cadence | Interpretation and follow-up |
|---|---|---|---|
| H01 | Source-specific reachability: sent/received, timeout count, RTT min/median/max | Device native probe or dedicated probe; critical paths 1m, other paths rotation | ICMP filtering/deprioritization differs from data-plane outage; compare H04/H13 |
| H02 | Delay distribution and variation: median, p95 after ≥100 samples, consecutive successful RTT differences | Rolling valid H01 samples; 5m evaluation | Call this RTT variation, not one-way jitter; preserve gaps and sample count |
| H03 | Sustained loss and burst length | H01 or approved UDP receiver samples; 3-window decision | Retain denominator; one missed echo is not a confirmed outage |
| H04 | TCP connect and service response from selected sources | Probe to allowed service/port; 5m | Distinguish reachability, connection refusal, timeout and service error; no broad port probing |
| H05 | Physical-link error growth: CRC/FCS, alignment/input errors, packets and reset markers | Both adjacent supported interfaces; NMS cadence or 5m direct read | Measure rate and errors per million received frames where counters align; reset/wrap/discontinuity invalidates interval |
| H06 | Optical diagnostics: receive/transmit levels, temperature and vendor alarm thresholds | Supported transceiver diagnostics; 15m or fault event | No universal optical dBm threshold; absent DOM is unsupported, not healthy |
| H07 | Link flaps and negotiation: operational changes, speed/duplex, resets | Events + 5m reconciliation | Check both ends and expected media; speed/duplex changes require a separate reviewed plan |
| H08 | Congestion: utilization, discards, queue drops, output errors, pause frames when available | Supported interface/queue counters; 5m | Average utilization can miss microbursts; averages alone cannot establish line-rate capacity or a specific cause |
| H09 | MTU/path packet-size failure | Small bounded DF/packet-size sequence from capable probes; daily and relevant incident | Filtered ICMP and tunnelling can make results inconclusive; no live MTU edit during test |
| H10 | LAG health and member imbalance | Both ends' member state/counters; 5m | Distinguish a missing member from hash-dependent unequal traffic; verify intended minimum members |
| H11 | Routing loop evidence | Bounded repeated traceroutes, route tables, TTL behavior; event/daily critical destinations | Repeated hops are a candidate; nonresponses/ECMP need corroboration. No high-rate traceroute storm |
| H12 | Layer-2 loop indicators | STP changes, MAC moves, broadcast/multicast rates and interface evidence; event/5m | Correlated indicators, not proof from high broadcast alone; active loop injection is outside this check |
| H13 | DNS availability, answer correctness and UDP/TCP consistency | Probe→authorized resolver with approved test name; 5m | Include recursion/authoritative expectation, response code and latency; use no client browsing records |
| H14 | DHCP service/process and pool pressure | Server status/log aggregates/lease totals; 5–15m | Pools/lease counts are not active-client counts; warn on trend and accepted capacity policy |
| H15 | DHCP relay and VLAN coverage | Relay configuration plus approved segment probe; after relevant change/daily | Ordinary renewals may not exercise discovery. A controlled lease test requires agreed test client/segment |
| H16 | Unauthorized DHCP server evidence | Snooping/logs or approved passive observation point; event-driven | Capture only necessary headers/identities; server-ID alone can be spoofed; unmanaged local broadcasts may be invisible |
| H17 | Default gateway/ARP-neighbour consistency | Probe and infrastructure ARP/ND/routing views; 15m/event | Virtual MAC/HA changes may be legitimate; compare accepted policy, no ARP poisoning |
| H18 | Duplicate infrastructure address indicators | Conflicting identity/ARP events and verified interface addresses; event | Avoid endpoint sweep; qualify HA and aliases before declaring a duplicate |
| H19 | Firewall/gateway path health | pfSense interface/gateway status plus explicit permitted source probes; 5m | Keep controller and forwarded-path results separate; inspect policy routing rather than declaring ISP down |
| H20 | Resource pressure vs forwarding | CPU, memory, process/event history and independent service probes; 5m | A stable high-memory baseline is low headroom, not proof of a leak; Cisco/D-Link semantics differ |
| H21 | Clock quality | Device/system time versus known reference; hourly | Record offset/uncertainty; cross-device causal ordering is inconclusive with unsynchronized clocks |
| H22 | Configuration drift and unsaved changes | Snapshot diff, running/startup where supported; daily/change event | Exclude volatile fields carefully; a drift is not automatically wrong |
| H23 | Approved path asymmetry or unexpected route change | Paired source-specific traces/routes; event/daily | ICMP paths/ECMP and asymmetry may be legitimate; compare explicit intent |
| H24 | TCP throughput for selected path | iperf3 between approved infrastructure probes; explicit scheduled window | Default 10s, one stream, explicitly capped offered rate; not a capacity claim. Dedicated probes generate and receive throughput traffic |
| H25 | UDP loss/receiver jitter under known offered load | iperf3 between approved probes; same controlled window | Record datagram size/rate/duration and receiver jitter definition; not comparable with H02 RTT variation |
| H26 | Latency under controlled load | Concurrent critical-path samples during H24/H25 | Stop on agreed loss/latency impact; shows behavior at that load, not all campus workloads |
| H27 | Error-rate change with traffic | H05 paired with traffic rates and pre/post intervention | Quiet links cannot establish repair; compare equivalent windows and retain uncertainty |
| H28 | Monitoring/discovery coverage | Last poll, collector heartbeat, missing endpoints, parser completeness; each cycle | Separate a failed monitor from an observed network failure; no freshness from API retrieval alone |
| H29 | Backup/access readiness | Snapshot age/hash/restore availability and key capability; daily/read activity | A key being loaded is not successful device login; avoid extra login attempts to held targets |
| H30 | Post-action verification bundle | Appropriate subset of H01–H29 plus action-specific assertions; each repair | Success requires affected service outcomes as well as intended config; timeout cannot be success |

Controlled H24–H26 default proposed offered load is the lesser of 10 Mb/s and 1% of the slowest **known** path link, with one such test per shared failure domain. These are reviewable starting ceilings, not guarantees of safety. If path capacity, endpoint permission or background headroom is unknown, the load test is blocked. A true available-capacity test requires a separate explicit load budget/window. Record actual bytes, retransmits and endpoint CPU limits; capped offered throughput is reported separately from capacity.

## Rates, jitter and confidence

Calculate counters only over ordered, same-interface, same-epoch samples. Use counter width, device uptime/discontinuity indicators and interface identity where available. Unknown rollover/reset yields a new baseline. Store exact interval and units. Packet-error ratios require compatible packet/error counters and a nonzero denominator.

RTT/2 is not a one-way delay measurement. One-way delay needs appropriate synchronized endpoints and a supported test, outside the initial mandatory checks. p95 on tiny samples is not a stable statistic. Label latency to device management separately from latency through the device. A pass is bounded to its source/destination/time and coverage.

## Default diagnostic selection

Start with the symptom and known dependencies. Prefer cached fresh evidence, then the lowest-cost supported discriminating check. For poor throughput: inspect negotiation/errors/drops and endpoint pressure before H24. For slow management ping: compare forwarded service paths before declaring a transit bottleneck. For DHCP trouble: check service/pool/relay and observation coverage before suggesting port enforcement. The scheduler enforces test budgets and uses registered probe operations.
