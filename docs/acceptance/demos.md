# CodeOracle Acceptance Demos

Start the live stack, run preflight, then run the demo scripts from the
repository root:

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.e2e.yml up -d
bash infra/scripts/preflight.sh
bash scripts/demos/proven.sh
bash scripts/demos/unknown.sh
bash scripts/demos/live-edit.sh
```

Regenerate recordings only after the live stack passes preflight:

```bash
asciinema rec -q -c "bash scripts/demos/proven.sh" tests/fixtures/acceptance-recordings/proven.cast
asciinema rec -q -c "bash scripts/demos/unknown.sh" tests/fixtures/acceptance-recordings/unknown.cast
asciinema rec -q -c "bash scripts/demos/live-edit.sh" tests/fixtures/acceptance-recordings/live-edit.cast
```

The scripts use only `curl` and `jq`. Each exits non-zero when the expected
verdict/status is not observed.

The `.cast` files are tracked test fixtures. The integration test
`tests/integration/slice/test_demo_recordings.py` validates that they remain
valid asciinema v2 JSON-lines recordings with the expected verdict markers.
