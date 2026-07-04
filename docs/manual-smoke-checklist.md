# Manual smoke checklist (release verification)

Automated coverage: service layer (unit), uv/network (`-m integration`), real
deployment round-trip (`-m e2e`), Caddyfile (`-m caddy`), UI logic (AppTest).
This checklist covers what only a human in a browser can confirm. Walk it
before calling a release done — once in **direct-port mode** and once in
**Caddy mode** where marked ⑂.

Setup: `uv run python -m waloader.tools.doctor` → all green →
`uv run python -m waloader.tools.serve`.

## Login & accounts
- [ ] Fresh DB shows the first-time setup screen; creating the admin logs you in.
- [ ] Log out; wrong password shows an error; correct password logs in.
- [ ] Account page changes the password (old one stops working).
- [ ] Admin → WALoader users: create a second (non-admin) user; log in as them:
      no Admin section is visible.

## Create & deploy (use examples/sample-bundle.md)
- [ ] Create-new-app: typing a name shows ✅ + slug; an existing name shows ❌
      with the smaller italic reason; reserved names (e.g. `admin`) rejected.
- [ ] Submit with the sample bundle → spinner → success panel with copyable URL
      code block; **Open app** loads the running app in a new tab. ⑂
- [ ] Upload a deliberately broken bundle (delete the metadata block) → error
      panel with copyable block → upload the good bundle in the retry control
      → succeeds.
- [ ] Dashboard card shows 🟢 running, version, port; refresh keeps it.

## Gear dialog
- [ ] Stop (with confirmation) → ⚪ stopped, app URL stops responding.
- [ ] Resume (with confirmation) → 🟢 running again, same port.
- [ ] Restart works; PID changes (`appctl status`).
- [ ] Update code with a modified bundle → v2 deployed, same URL. ⑂
- [ ] Delete (with confirmation) → app leaves the dashboard; archive zip
      appears under `data/archives/`; name stays reserved for reuse attempts.

## Datasets (sample app's `clients` concept)
- [ ] Before upload, the app shows the italic *No data uploaded yet*.
- [ ] Add concept `clients`; upload a CSV → app displays the data after rerun.
- [ ] Upload a replacement with a changed column → schema diff shown in a
      copyable block → confirm → app shows new data.
- [ ] Upload an .xlsx → sheet-name input appears pre-filled `Sheet1`; wrong
      sheet name errors listing available sheets.
- [ ] Delete concept asks for confirmation.

## App users & child login
- [ ] Enable Users Management Support on the app; open the app URL: login form
      appears as the default screen.
- [ ] Create an app user; their credentials log into the child app; logout
      works; password change works.
- [ ] Deactivated user cannot log in; attachment upload/download/remove works.

## Admin panels
- [ ] Configuration: change `health.interval_seconds` → shows source `db`;
      clear override restores; invalid port range is rejected with an error.
- [ ] Processes: kill a child process manually (`kill <pid>`) → within the
      health interval the card shows 🔴 failed and (after grace) a crash-email
      stub line appears in `data/logs/waloader/app.log`; reconcile + resume
      brings it back.
- [ ] Maintenance: "Back up database now" reports created/unchanged.
- [ ] Caddy ⑂: start/status/reload from the panel; generated Caddyfile visible;
      `http://host:8080/waloader` and `/apps/<slug>` both work; `redir /` lands
      on /waloader/.

## CLIs (spot check)
- [ ] `appctl list/status/logs` sensible; `doctor` all green;
      `maintenance run-all` prints a summary.
