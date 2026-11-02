# Feature requests and feedback

Use **Request a feature** in the Web UI header (the lightbulb icon on narrow
screens). Describe the feature and the beamline task it would help, then choose
**Save request**. It works before starting a conversation. A successful save
displays a request ID; the confirmation stays open until you dismiss it.

Requests are saved on the machine running the APEXA web server. The default is
`~/.apexa/feedback/feedback-YYYY-MM.jsonl`, using the server account's home
directory and UTC month. These files survive browser refreshes and server
restarts. They are outside the source repository and do not become GitHub issues.
An operator can set `APEXA_FEEDBACK_DIR` to another persistent directory; use the
same setting when reviewing requests. Back up that directory with other service
data. Separate hosts have separate stores unless configured to share a location.

Feature requests use the existing `/api/feedback` endpoint with `kind=report`
and `category=feature_request`. Records include an ID, timestamp, description,
server host/account and basic APEXA version metadata. The dedicated feature form
does not attach conversation text or recent tool diagnostics. The server account
is not an authenticated submitter identity.

## Review and export

Run these commands on the APEXA server, from the repository directory:

```bash
# If the server uses a custom directory, export the same value in this shell:
# export APEXA_FEEDBACK_DIR=/path/to/persistent/apexa-feedback

# Recent feature requests, including the IDs shown to users
uv run python apexa_feedback.py list --category feature_request -n 50

# Counts for this category
uv run python apexa_feedback.py summary --category feature_request

# Full descriptions and metadata for all saved feature requests
uv run python apexa_feedback.py export --category feature_request > ~/apexa-feature-requests.json
```

Omit `--category` to include answer ratings and issue reports. Requests are an
append-only intake queue; prioritization and implementation status are currently
managed by maintainers outside this form. Exporting does not delete the originals.

The form keeps its text visible if saving fails or cannot be confirmed. A receipt
is shown only after the server acknowledges a save. If a connection breaks after
the write, maintainers may find a saved request even though the browser could not
confirm it; retrying in that case can create another entry.

## Load the update

Refresh the Web UI after installing the rebuilt frontend. The feature form uses
the existing feedback API, so this UI addition does not require restarting a
running server that already provides `/api/feedback`. Changes to the server's
`APEXA_FEEDBACK_DIR` environment setting require a restart.
