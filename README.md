# pltr-cli

> **A fork of [`anjor/pltr-cli`](https://github.com/anjor/pltr-cli) by [@anjor](https://github.com/anjor), who built the original CLI.** All credit for the base tool is theirs. This fork keeps upstream's name, command, and MIT license, then takes it agent-native: the `pltr-agent-v1` JSON contract, the dependency / change-impact gate, the skill bundle, and optional Langfuse tracing.

An **agent-native** fork of the command-line interface for Palantir Foundry.

`pltr-cli` wraps the official [`foundry-platform-sdk`](https://github.com/palantir/foundry-platform-python) and adds three things on top of upstream:

1. **A stable machine contract.** Every command can emit one JSON envelope (`pltr-agent-v1`) with `--agent`, so an autonomous caller never has to parse tables or scrape text.
2. **A read-only dependency and change-impact gate.** Before you touch a Foundry resource, `pltr dependency` tells you what breaks — with explicit coverage gaps, provenance, and a CI exit code.
3. **A drop-in skill bundle.** `skills/pltr-cli/` teaches any coding agent (Claude, Codex, others) how to drive the CLI safely.

**Why this fork exists.** The JSON contract, the change-impact gate, and the skill bundle let an autonomous agent operate Foundry safely and cheaply, with no human in the loop. Everything else — Rich tables, the interactive shell, multi-profile switching, and the 100+ commands across datasets, SQL, ontology, orchestration, filesystem, and admin — comes from upstream and still works the same way.

---

## Install

Installed from git (not published to PyPI). Upstream `anjor/pltr-cli` is on PyPI; this fork is not, so install it by URL. The command is still `pltr`.

```bash
uv pip install "git+https://github.com/zaycruz/pltr-cli"
```

Or clone for development:

```bash
git clone https://github.com/zaycruz/pltr-cli.git
cd pltr-cli
uv sync
uv run pltr --help
```

## Authenticate

```bash
# Interactive setup (token or OAuth2). Credentials go in the system keyring, never plain text.
pltr configure configure

# Or use environment variables (CI / automation):
export FOUNDRY_TOKEN="your-api-token"
export FOUNDRY_HOST="foundry.company.com"

# Confirm it works:
pltr verify
```

OAuth2 uses `FOUNDRY_CLIENT_ID` and `FOUNDRY_CLIENT_SECRET` instead of `FOUNDRY_TOKEN`. See [Authentication Setup](docs/user-guide/authentication.md).

---

## Agent interface

Add the global `--agent` flag to any command. The command then returns a single stable JSON envelope on stdout instead of a table:

```bash
pltr --agent capabilities
pltr --agent resource list --folder-rid ri.compass.main.folder.0
pltr --agent dataset files list ri.foundry.main.dataset.abc123 --page-size 50
```

Every envelope has the same shape:

```json
{
  "schema_version": "pltr-agent-v1",
  "data": {},
  "meta": {},
  "warnings": [],
  "errors": [],
  "pagination": null,
  "artifacts": []
}
```

**Contract guarantees:**

- **Stable schema.** `schema_version` is `pltr-agent-v1`. Fields do not move between commands.
- **Credential redaction.** Any field whose name contains `token`, `secret`, `password`, `private_key`, or `authorization` is replaced with `[REDACTED]`. Pagination cursors (`page_token`) are kept, because a caller needs them to resume.
- **Resumable pagination.** When a result is paged, `pagination` carries the next cursor.
- **Non-interactive by default.** `--agent` forbids prompts. A mutation that would normally ask for confirmation fails with a clear policy error unless you pass its explicit confirmation flag (for example `--force`). Use `--non-interactive` to get the same no-prompt behavior without switching output to the envelope.

Start every agent session with `pltr --agent capabilities` to discover the available command surface.

---

## Dependency and change-impact analysis

`pltr dependency` runs a **read-only, evidence-backed** assessment of one Foundry target. One invocation resolves the target, discovers a bounded dependency graph, writes the complete graph as a JSON artifact, and renders the view you asked for. It never mutates Foundry.

```bash
# What depends on this dataset? Retain the full evidence graph.
pltr dependency resource ri.foundry.main.dataset.abc123 \
  --change "rename a column" \
  --change-type rename \
  --output-mode agent \
  --graph-output ./before.json
```

**Targets:** `resource`, `object-type`, `property`, `link-type`, `action-type`, `query-type`.

**Three output modes:**

| Mode | Use for | Result |
|------|---------|--------|
| `graph` | Full programmatic detail | The complete result (nodes, edges, paths, evidence, provenance) |
| `agent` | Compact machine reasoning | Status, ranked impacts, blast-radius + release-risk scores, action/query contracts, coverage, `must_verify_before_merge`, `should_verify_before_deploy` |
| `ci`    | Pipeline gating | A one-line payload and an exit code |

**CI exit codes:** `0` clean, `2` needs verification, `1` fatal.

**Honest about coverage.** Outcomes are `covered`, `covered-empty`, `partial`, `inaccessible`, `unsupported`, `unresolved`, or `budget-exhausted`. Anything other than covered is reported as a **gap** — never silently treated as "no impact." Each result carries provenance: SDK method, capability IDs, branch/preview resolution, timestamps, and known limitations.

**Merge gate (baseline → change → compare):**

```bash
# 1. Capture a baseline before the change (retained artifact).
pltr dependency property ri.ontology.main.ontology.example Employee email \
  --change "email string -> struct" --change-type type-change \
  --direction downstream --output-mode agent \
  --graph-output ./employee-email-before.json

# 2. After the change, compare against the baseline and gate CI.
pltr dependency property ri.ontology.main.ontology.example Employee email \
  --change "email string -> struct" --change-type type-change \
  --direction downstream \
  --compare-artifact ./employee-email-before.json \
  --output-mode ci --graph-output ./employee-email-after.json
```

Artifacts are written atomically with mode `0600`, to `--graph-output` or to `${XDG_STATE_HOME:-~/.local/state}/pltr/dependency/<analysis-id>.json`. Bounds (`--depth`, `--max-nodes`, `--time-budget-seconds`, …) are configurable with hard ceilings.

Full command reference: [`skills/pltr-cli/reference/dependency-commands.md`](skills/pltr-cli/reference/dependency-commands.md). Full operating sequence: [`skills/pltr-cli/workflows/change-impact-assessment.md`](skills/pltr-cli/workflows/change-impact-assessment.md).

---

## Skill bundle for coding agents

`skills/pltr-cli/` is the single, model-agnostic source of truth for driving `pltr` from an agent. Point your agent client at it; do not fork per-provider copies.

- **[`SKILL.md`](skills/pltr-cli/SKILL.md)** — overview, critical concepts, when to load which reference.
- **[`AGENTS.md`](AGENTS.md)** — repository rules, including the **mandatory change-impact gate**: assess with `pltr dependency` before proposing or applying any Foundry change, and do not merge while status is `needs-verification`.
- **`workflows/`** — [change-impact-assessment](skills/pltr-cli/workflows/change-impact-assessment.md), [data-pipeline](skills/pltr-cli/workflows/data-pipeline.md), [data-analysis](skills/pltr-cli/workflows/data-analysis.md), [permission-management](skills/pltr-cli/workflows/permission-management.md).
- **`reference/`** — 17 per-module command references (datasets, SQL, ontology, orchestration, filesystem, admin, connectivity, mediasets, streams, functions, AIP agents, models, language models, dependency).

---

## Human use

For interactive work, every command supports `--format table|json|csv`, `--output <file>`, and `--profile <name>`.

```bash
pltr sql execute "SELECT * FROM my_table LIMIT 10"
pltr dataset get ri.foundry.main.dataset.abc123
pltr ontology list
pltr orchestration builds search
pltr folder list ri.compass.main.folder.0        # root folder
pltr resource-role grant <resource-rid> <user-id> User viewer
pltr shell                                         # REPL with tab completion + history
pltr completion install                            # bash / zsh / fish completion
```

Full command list: `pltr --help`, or per command `pltr <command> --help`. See the [Command Reference](docs/user-guide/commands.md) and [Common Workflows](docs/user-guide/workflows.md).

---

## Configuration

Manage multiple Foundry environments as named **profiles** — switch the default, or pick one per command:

```bash
pltr configure configure          # add or edit a profile (interactive)
pltr configure list               # list profiles
pltr configure use <name>         # switch the default profile
pltr configure delete <name>      # remove a profile
pltr <command> --profile <name>   # use a specific profile for one command
```

- **Profiles:** `~/.config/pltr/profiles.json`
- **Credentials:** encrypted in the system keyring
- **Shell history:** `~/.config/pltr/repl_history`

### Optional Langfuse tracing

Install the extra and set all three variables to trace command paths, redacted arguments, duration, and exit codes. Tracing is a no-op when the variables are absent, and a tracing failure never changes the command result.

```bash
uv pip install "pltr[langfuse] @ git+https://github.com/zaycruz/pltr-cli"
export LANGFUSE_HOST="https://cloud.langfuse.com"
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
```

---

## Development

Requires Python 3.10+ and [uv](https://github.com/astral-sh/uv).

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run ruff check src/ && uv run ruff format src/
uv run mypy src/
```

**Architecture** is layered: CLI (Typer) → command layer (validation) → service layer (`foundry-platform-sdk`) → auth (keyring). Agent output and dependency analysis live in `src/pltr/utils/` and `src/pltr/services/`. See [API Wrapper Documentation](docs/api/wrapper.md) and [`CONCEPTS.md`](CONCEPTS.md).

When extending the SDK surface, be exact about what Foundry exposes and preserve explicit gaps instead of guessing — see [`AGENTS.md`](AGENTS.md).

## License

MIT, same as upstream. See [LICENSE](LICENSE).

Original CLI by [@anjor](https://github.com/anjor) — [`anjor/pltr-cli`](https://github.com/anjor/pltr-cli). Built on the official [Palantir Foundry Platform Python SDK](https://github.com/palantir/foundry-platform-python).
