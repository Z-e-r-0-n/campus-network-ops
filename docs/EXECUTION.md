# Execution lifecycle

The execution runner processes a stored execution ID. Its plan contains one typed action, target identity, configuration evidence, recovery steps, service verification policy and an execution deadline. Approval is bound to the canonical plan digest.

**Implementation status:** runner and Cisco service adapter prototypes are implemented. The isolated worker, independent service probes and platform qualification remain in development.

## Lifecycle

1. Validate the approval and claim a unique attempt lease. An active attempt prevents concurrent delivery for the same target.
2. Store the start time and deadline once. Resumption retains both values.
3. Verify target isolation and current identity. Record the original configuration backup and verify its integrity on subsequent attempts.
4. Reload approval, device revision, qualification, pause/stop state and lease before each mutation. Inspect the device and evaluate OPA against the current conditions.
5. Persist write intent before dispatch and completion after acknowledgement. Exceptions after dispatch produce an uncertain outcome.
6. Verify the intended configuration and independent service behavior before persistence, then verify saved state.
7. Apply the approved recovery branch when its preconditions hold, recording each recovery write and verification result.

## Reconciliation

A write may succeed on a device even when its acknowledgement is lost. The runner distinguishes applied, unchanged, partially applied and unknown state through adapter-specific reconciliation.

Unchanged readback after an uncertain dispatch does not authorize a retry. An applied state can proceed to verification once the previous attempt has stopped. Interrupted persistence is checked through readback rather than immediately repeated. Recovery uses the same authorization, isolation and deadline checks as the original action.

A global pause or stop request prevents subsequent writes, including persistence and automatic reversal. A command already in progress may complete; its outcome is established through reconciliation.

## Launcher contract

The execution launcher provides:

- `verify(target, addresses)`: validates the actual target egress restrictions.
- `previous_attempt_stopped(attempt_id)`: confirms termination of an abandoned attempt.
- A dedicated execution identity and scoped credential access.
- Bounded process and transport lifetimes.

Database leases coordinate application attempts. They cannot cancel a command already received by a device. Production launcher implementation and qualification are pending.

## Adapter contract

Adapters implement identity/configuration inspection, backup creation and integrity checks, reconciliation, typed actions, persistence, service verification and recovery. Device operations use bounded timeouts; execution limits are checked at operation boundaries.

The Cisco prototype handles plain IPv4 NTP/syslog destinations. Unsupported destination options are rejected because their exact semantics cannot yet be preserved. Its snapshot digest covers running and startup configuration. Persistence is blocked when those configurations differed before the plan or unrelated configuration changed during execution.

## Remaining integration

- Isolated execution worker and credential delivery.
- Encrypted backups and verified recovery access.
- Independent service probes for supported actions.
- Firmware-specific command, save and rollback qualification.
- Operator reconciliation and explicit recovery controls.
- Restart behavior, notification delivery and installation recovery.
