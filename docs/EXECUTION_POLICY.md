# Analysis and motor execution policy

APEXA now starts with a local inspection command surface and no commissioned motor writes. These defaults address the audit's shell-policy bypass and failed motor-read behavior. They do not replace operating-system isolation, facility authorization, IOC interlocks or hardware commissioning.

## Local and remote analysis commands

Without additional configuration, `run_command` accepts a small set of simple local inspection commands such as `ls`, `pwd`, `cat`, `head`, `tail`, `wc`, `du`, `df` and checksums. They are launched as argument arrays using system executables, without a shell. Quoting a filename containing spaces is supported; shell substitutions, redirects, pipelines and scripts are not. An invalid working directory produces an error rather than falling back to the server's directory. `check_environment` reports the active command policy.

An operator can explicitly enable exploratory shell/interpreter/SSH analysis with:

```text
APEXA_ALLOW_UNSANDBOXED_COMMANDS=1
```

This setting acknowledges an unrestricted analysis code-execution capability, subject only to an ergonomics allowlist. It is **not a sandbox or per-command human approval**. Enable it only for a dedicated analysis OS identity with no EPICS write rights, mounted/authorized data roots, an isolated writable output workspace and appropriate network/resource restrictions. Apply the equivalent restrictions to the remote identity. The client-side deletion confirmation remains an additional interaction feature; it does not constrain arbitrary Python or direct MCP callers.

Raw `caput` and `pvput` invocations are refused by the analysis command tool even in exploratory mode. Python, shell scripts and network libraries can still reproduce those operations under the same OS permissions, so lexical blocking is not the security boundary. Typed scientific adapters remain available independently of this exploratory-shell setting.

Local command timeout/cancellation terminates and reaps its owned process group on supported POSIX hosts. SSH runs through the same nonblocking transport, but stopping an SSH client does not establish that remote scientific processes stopped. Remote timeout results explicitly mark the remote state unknown and require reconciliation. Use a durable facility job service for long remote computations.

## Registering motor write targets

Read tools remain available. An operator must register exact motor base PVs before enabling any ordinary writes:

```text
APEXA_EPICS_WRITE_PVS=20idMotSim:m1,20idMotSim:m2
```

The values above are examples for a simulator, not a statement that any real device is commissioned. Prefix wildcards and field suffixes are not supported. Description aliases may resolve to a motor name, but the resulting exact base PV must appear in the registry. The caller cannot authorize a new PV through a tool argument.

Motion requires fresh, finite `RBV`, `HLM`, `LLM`, `HLS`, `LLS`, `DMOV`, `LVIO`, `SEVR` and `RDBD` observations. The reads must finish within a five-second observation window. Limits must be ordered and contain the current position, the motor must be stationary and free of alarm/limit violations, switches must be inactive, and the target must remain within soft limits. This bounds observation latency; it does not independently establish freshness of a malfunctioning IOC's underlying sensor data.

By default, one request cannot move more than half of the current soft-limit range. `confirm_large_move=True` is retained for API compatibility but never authorizes a larger move. A commissioned deployment can set a different fixed fraction in `(0, 1]` through `APEXA_EPICS_MAX_MOVE_FRACTION`; the model cannot override that policy with a confirmation argument. A future hardware broker should bind a real operator approval to an exact immutable plan and device state.

`wait=True` verifies both motion completion and a final readback within the motor's configured `RDBD`, plus a final valid state. `wait=False` reports only `move_issued` and `completed: false`; the caller must monitor subsequent state. Timeout, cancellation and interrupted motion trigger STOP/jog-clear attempts, and failure results include stop outcomes. These checks have been tested with stubs, not commissioned on hardware.

Jog uses positive finite `JVEL` and `JAR` to check a conservative travel-plus-braking estimate against the same limits before starting. The jog field is cleared and STOP requested on completion, exceptions and cancellation. Tweak is implemented through the same checked relative-position route rather than bypassing checks through `TWF/TWR`.

Velocity updates require a registered stationary motor and a finite positive `VMAX` covering the requested velocity. Limit and description changes require a separate operator setting:

```text
APEXA_EPICS_ALLOW_CONFIG_WRITES=1
```

Limit changes validate both effective limits against the current readback. Multiple IOC writes are not transactional: a partial failure is reported explicitly and requires operator inspection. Homing is separately disabled unless the device's complete home behavior has been commissioned and the operator sets:

```text
APEXA_EPICS_ALLOW_HOMING=1
```

Ordinary soft limits alone do not establish a safe homing trajectory. The opt-in is an operator deployment policy, not a safety certification.

## STOP behavior and remaining commissioning

`stop_motor` requires an exact known base PV in `APEXA_EPICS_WRITE_PVS` or a separate stop-only registry:

```text
APEXA_EPICS_STOP_PVS=20idMotSim:m1
```

STOP does not perform description discovery, wait for the motion lock, require healthy readbacks, or require configuration/homing opt-ins. It uses a separate I/O worker pool and requests `STOP=1`, `JOGF=0` and `JOGR=0`. It remains available for a registered stop-only motor when normal writes are disabled. Each failed stop/clear write is returned and logged. A request interrupting an in-flight setpoint causes another STOP after that write settles, preventing an acknowledged late setpoint from silently overriding the first stop request.

A successful CA write is only an acknowledgement, not proof of a physical stop. Independently verify readback and motion state. CLI/network timeouts still bound each individual operation; Python scheduling and channel access are not a hard real-time emergency-stop system. Keep facility-provided stop/interlocks independent of APEXA and analysis saturation.

Before enabling real devices, qualify device-specific envelopes, alarms/interlocks, motion direction, resolution/deadband, missing/stale readings, simultaneous requests, cancellation/disconnect, limit switches, jog braking, homing and the independent stop path. Shared deployment also needs authenticated operators, ownership leases and a separate control-service identity. The software changes do not claim those facility qualifications are complete.
