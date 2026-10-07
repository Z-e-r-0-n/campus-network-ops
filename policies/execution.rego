package campus.execution

import rego.v1

default allow := false

allow if {
  input.enabled == true
  input.paused == false
  input.approval_valid == true
  input.digest_valid == true
  input.identity_valid == true
  input.configuration_matches == true
  input.qualified == true
  input.backup_verified == true
  input.recovery_ready == true
  input.egress_enforced == true
  input.lease_valid == true
  input.within_deadline == true
  input.target_count == 1
  input.action_count > 0
  input.action_count <= 8
}
