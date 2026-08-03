## 2026-08-03 17:32 - Match experiment streams without source IDs

### Request
Allow the full experiment monitor configuration to find streams without requiring an exact LSL source ID, while retaining runtime selection when multiple outlets match.

### Changes
- Modified `json/experiment_monitor_full.json`.
- Replaced shared `identity` rules with explicit LSL `name` rules.
- Corrected each configured LSL `type` and the `MotorControlMarkers` outlet name to match the live producers.
- Kept the internal monitor stream IDs unchanged and omitted source IDs so the existing duplicate-stream chooser remains applicable.

### Validation
- Ran configuration validation: valid configuration with 8 configured streams.
- Queried live LSL discovery and confirmed each of the 8 configured entries currently resolves to exactly one expected outlet.
- Ran `python -m pytest tests/test_config.py tests/test_lsl.py -q --basetemp .pytest_tmp`: 22 passed.
- An initial test run reported 3 setup errors because the sandbox could not access the default pytest temporary directory; rerunning with a workspace-local base temporary directory passed all tests.

### Limitations or unresolved issues
- Live uniqueness reflects the outlets visible during validation. If multiple outlets later share the same name and type, the existing runtime chooser will require the user to select one.

### Fallbacks
- None.
