source [file join [file dirname [info script]] common.tcl]
chipflow_note "Floorplan hook executed for $::env(DESIGN_NAME)."
if {[info exists ::env(CHIPFLOW_PARAM_LABEL)]} {
  chipflow_note "Parameter LABEL = $::env(CHIPFLOW_PARAM_LABEL)"
}
# Add OpenROAD commands here. This script runs in the live floorplan process.
