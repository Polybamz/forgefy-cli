# Forgefy CLI (initial release)

A Python 3.11+ coding-assistance CLI. Language-independent prompts support writing,
debugging, reviewing, testing, refactoring and planning code. Quality and language
coverage depend on the selected model; no model is guaranteed to be best at everything.

## Install

No Python required — installs a standalone `forgefy` binary and puts it on
your PATH, the same way Ollama's or Claude Code's installer does:

macOS / Linux:
```sh
curl -fsSL https://raw.githubusercontent.com/Polybamz/forgefy-cli/main/install.sh | sh
```

Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/Polybamz/forgefy-cli/main/install.ps1 | iex
```

Already have Python? `pip`/`pipx` work too — prefer `pipx` over plain `pip`,
since `pip install` can silently install to a user directory that isn't on
your PATH:

```powershell
pipx install forgefy-cli
```

Either way, open a new terminal and you should have the `forgefy` command:

```powershell
forgefy --help
forgefy auth          # sign in (or: forgefy login, the same thing)
forgefy config --init
forgefy providers
forgefy skills
forgefy doctor
forgefy history
```

### Developing locally

```powershell
git clone https://github.com/Polybamz/forgefy-cli.git
cd forgefy-cli
pip install -e ".[dev]"
```

## Quick start

```powershell
forgefy                                    # interactive session: the agent reads the
                                           # workspace and asks before every change
forgefy "add a --dry-run flag with tests"  # one task, same approval rules
forgefy -p "plan that change first"        # plan mode: read-only, nothing can change
forgefy --json "list TODO comments"        # newline-delimited JSON for scripts and CI
forgefy auth                               # sign in to your Forgefy account (browser)
forgefy history                            # what you have run, and resume it
```

`forgefy` with no prompt starts an interactive session; a prompt runs one task and exits.
Both are the same agent, with the same tools and the same approval boundary: it may read
the workspace, and every write is shown as a complete diff that you must approve by
typing `yes`. `-p/--plan` removes the write tools entirely.

## Commands

| Command | What it does |
| --- | --- |
| *(prompt)* | Run one task in act mode; omit the prompt for an interactive session |
| `auth [provider]` | Sign in to your Forgefy account (device code), or check a provider key |
| `config [--init]` | Print the config path, or create a starter `config.toml` |
| `doctor` | Check configuration, keys and session state without sending a request |
| `history` (`h`) | List saved sessions; add `--show ID`, `--delete ID`, `--clear` |
| `models` | List the live model IDs the selected provider offers |
| `providers` | List built-in and configured provider profiles |
| `skills` | List the built-in coding skills |
| `update` | Check PyPI for a newer release (it does not install anything) |
| `version` | Print the CLI version (`-V`/`--version` too) |
| `logout` | Remove the stored Forgefy credential |
| `run` | Legacy: one-shot suggestion with `--file` context, no tools |
| `chat` | Legacy: interactive chat with no workspace tools |
| `edit` | Legacy: edit only the files named by `--file`/`--create` |

## Global flags

Accepted before or after the subcommand, the way Cline CLI's globals are:

| Flag | Meaning |
| --- | --- |
| `-p, --plan` | Plan mode: read-only tools, no edits, no commands |
| `--json` | One JSON object per stdout line instead of text (see below) |
| `--auto-approve <true\|false>` | Skip the approval prompts (default `false`) |
| `-m, --model` / `-P, --provider` | Model and provider profile for this run |
| `-c, --cwd <path>` (`--workspace`) | Working directory (default: the current one) |
| `--config <path>` | Config directory, or a `*.toml` file |
| `--data-dir <path>` | Keep config, credentials and sessions under one directory |
| `-k, --key <api-key>` | API key for this run only; never written to disk |
| `-s, --system <prompt>` | Replace the built-in system prompt |
| `-t, --timeout <seconds>` | Request and command timeout (default 120) |
| `-i, --tui` | Start the interactive session |
| `--id <session-id>` | Resume a saved session by name |
| `--no-history` | Don't load or save session history |
| `--file` / `--create` | Restrict the agent to these paths instead of the workspace |
| `--allow-commands` | Let the agent propose shell commands (approval per command; not a sandbox) |
| `--max-turns <n>` | Maximum model requests per turn (1-30, default 12) |
| `-v, --verbose` | Extra diagnostics on stderr |

### Deliberate differences from Cline CLI

* `--auto-approve` defaults to **false**. Cline's default is `true`; here the approval
  prompt is the only thing standing between a model and your files, so it is opt-in, and
  a non-interactive run declines changes rather than applying them.
* Not implemented, and not stubbed with fake behaviour: `--thinking`, `--retries`,
  `--acp`, `-z/--zen`, `--hooks-dir`, `mcp`, `plugin`, `schedule`, `hub`, `connect`,
  `kanban`, agent teams, checkpoints and MCP servers. This is an agent over any
  OpenAI-compatible chat-completions endpoint, not a reimplementation of Cline.

### `--json` output

Each line is one JSON object, using the fields Cline CLI documents:

```json
{"type": "say", "say": "text", "text": "I'll read the file first.", "ts": 1760501486669}
{"type": "say", "say": "info", "text": "Files actually changed: none", "ts": 1760501486670}
{"type": "ask", "ask": "approval", "text": "Proposed change (complete diff): ...", "ts": 1760501486671}
```

`type` is `"say"` or `"ask"`; a `say` carries a subtype (`text`, `info`, `error`,
`history`, `message`); `ts` is Unix milliseconds. `--json` implies no streaming, so each
reply arrives as one complete message rather than partial chunks, and human-readable text
never mixes into stdout — pipe it straight into `jq`.

## Sessions and history

Every agent run and chat is saved under `--id` (default `default`) inside
`~/.forgefy/history/`: agent sessions keep the full tool conversation, chat sessions keep
their turns. `forgefy history` lists them, `--show ID` prints one, `--delete ID` and
`--clear` remove them, and `--id ID` (or `chat --resume`) continues one. `--no-history`
skips saving entirely.

## Local and hosted models

With Ollama running and a model installed, list exact IDs and choose one:

```powershell
forgefy models --provider ollama
forgefy run "Write a Rust function with unit tests that validates an email address" --provider ollama --model YOUR_INSTALLED_MODEL
```

For OpenRouter, set `OPENROUTER_API_KEY` in your environment, list models, then select
an exact available ID. Free-tier models and availability are provider-controlled;
verify pricing before sending requests. Local inference has hardware/energy costs.

```powershell
forgefy models --provider openrouter
forgefy run "Explain this Python module and suggest tests" --provider openrouter --model YOUR_MODEL_ID --workspace 'C:\Users\USER\Desktop\polycarp\forgefy-cli' --file src/forgefy_cli/context.py --skill review
```

Built-in profiles: Ollama, OpenAI, OpenRouter, DeepSeek, Groq, and `forgefy` (your
Forgefy account — see below). Anthropic and Gemini models can be used through
OpenRouter when offered there; native Anthropic/Gemini protocols are not implemented.
No automatic provider or paid-model fallback occurs. Compatibility requires the
`/models` and `/chat/completions` endpoints; a listed model is not necessarily a
compatible text-generation model.

## Using your Forgefy account

```powershell
forgefy auth
```

Opens your browser to confirm a code against your already-logged-in web session, mints
an API key, and saves it to `~/.forgefy/credentials.json` — no copying a key by hand.
In an interactive terminal it then offers to pick a default model from what your plan
allows and saves it as `default_provider`/`default_model` in `config.toml`, so every
command after that needs no `--provider`/`--model` at all:

```powershell
forgefy "explain this module"
forgefy chat
forgefy run "explain this module" --file src/app.py
```

Skip the prompt (Enter with no choice) to keep passing `--provider forgefy --model
<id>` explicitly instead. Already have `default_provider`/`default_model` set? `auth`
leaves them alone rather than silently overwriting your choice. `forgefy auth <provider>`
instead checks the key environment variable for one of the other profiles without
signing anything in, and `forgefy logout` removes the stored credential.

## Provider plugins

`forgefy config` prints the config path (normally your home directory's
`.forgefy/config.toml`). `FORGEFY_CONFIG` can select an alternate file.
Add declarative OpenAI-compatible provider profiles:

```toml
default_provider = "local_server"
default_model = "your-model-id"

[providers.local_server]
base_url = "http://localhost:1234/v1"
api_key_env = ""

[providers.company]
base_url = "https://models.example.com/v1"
api_key_env = "COMPANY_MODEL_KEY"
```

Never store keys directly in configuration. Custom profiles cannot override built-in
names. HTTPS is required for non-loopback endpoints. Only configure servers you trust:
the chosen server receives your prompt, explicit file context, and its configured key.
These plugins are configuration, not executable Python code or an MCP integration.

## Skills and context

Choose `--skill code|debug|review|test|refactor|plan`. Repeat `--skill-file` to include
trusted UTF-8 Markdown instructions. Repeat `--file` to send workspace-relative source
files. Use a prompt of `-` for standard input. No repository files are sent implicitly.
Files must resolve within the workspace. Common credential paths are excluded, but this
is not a secret scanner: review every file and prompt before sending. Requests have a
120,000-character input cap; individual models may require much smaller inputs.

## Interactive chat

```powershell
& 'C:\Users\USER\Desktop\polycarp\.venv\Scripts\forgefy.exe' chat --provider ollama --model llama3:latest
```

Chat retains conversation history for follow-up questions. `/new` clears it, `/help`
lists commands, and `/exit` or `/quit` ends the session. EOF exits normally; Ctrl+C
cancels. Each request resends retained history, so hosted-provider usage can grow each
turn. No automatic paid fallback occurs. Oldest complete user/assistant pairs are
omitted when conversational content exceeds 120,000 characters; system/skill
instructions are additional. This is a character cap, not a token budget. Failed
requests preserve prior history. Chat accepts single-line turns and skill plugins;
explicit `--file` context is currently supported by `run` only.

Replies stream to the terminal as they're generated by default; `--no-stream` waits for
the complete response instead (both `chat` and `run`).

Every session is saved to disk under `--session NAME` (default `"default"`,
`~/.forgefy/history/NAME.json`, permissioned 0600 where the OS supports it — override
the directory with `FORGEFY_HISTORY_DIR`). A fresh run always **starts empty**, even
under a name that already has history — add `--resume` to load that session's prior
turns first. `--no-history` skips saving entirely, for a fully ephemeral session like
older versions of this CLI. Session files can contain source code and other workspace
content pasted into the conversation; they're local-only and never uploaded anywhere by
this tool.

## Approved file editing

The default `forgefy "task"` / interactive session runs in **workspace scope**: the agent
may read, list and search anywhere in the working directory (hidden, credential and
symlink paths are refused), and every `replace_text`/`create_file` it proposes is printed
as a complete diff that needs a typed `yes`. Add repeatable `--file`/`--create` to narrow
that, or use `forgefy -p` for a session that cannot write at all.

`forgefy edit` is the older, tighter variant kept for scripts: it can only touch the
**existing** files you name with `--file`, and with `--create` explicitly named new ones,
using a model supporting OpenAI-compatible tool calling. `run` and `chat` remain
suggestion-only. In an interactive terminal, for
example:

```powershell
& 'C:\Users\USER\Desktop\polycarp\.venv\Scripts\forgefy.exe' edit "Improve error handling in this module" --provider ollama --model YOUR_TOOL_CALLING_MODEL --workspace 'C:\Users\USER\Desktop\polycarp\forgefy-cli' --file src/forgefy_cli/context.py
& 'C:\Users\USER\Desktop\polycarp\.venv\Scripts\forgefy.exe' edit "Add a --dry-run flag with tests" --provider ollama --model YOUR_TOOL_CALLING_MODEL --workspace 'C:\Users\USER\Desktop\polycarp\forgefy-cli' --file src/forgefy_cli/cli.py --create tests/test_dry_run.py
```

Review the selected files for secrets before starting: the model can read their contents
and send them to the selected provider without further read approval. Each replacement
shows a complete diff and requires typing `yes`. `--auto-approve true` removes that
prompt, which is why it defaults to false. The executor requires a prior read, an exact
single match, and unchanged contents before and after approval. Updates use a sibling
temporary file and atomic replacement. `list_files` and `search_files` are read-only and
skip the same hidden, credential, symlink and oversized/binary files.

Each `--create` path is dormant until the model proposes it: the full new file content is
printed (`+`-prefixed) and needs the same typed `yes`, and an existing file is never
overwritten — the tool errors instead. Parent directories are not created, so only paths
whose directory already exists can be created. Nothing about a `--create` path is sent as
file content, since no file exists yet to read.

Only files named by repeatable `--file` options (or `--create` paths) are accessible.
Hidden paths, common credential files, symlinks, junctions, hardlinks and nonregular
files are rejected. Files and replacements are limited to 32,000 bytes; oversized diffs
are rejected, not truncated for approval. These checks are not a secret scanner or an OS
security sandbox.
Use a trusted workspace without concurrent writers: a small filesystem race window
remains between validation and replacement. Atomic replacement preserves mode bits,
not necessarily all filesystem metadata or custom ACLs.

The default limit is 12 model requests (`--max-turns` accepts 1–30), with a
120,000-character serialized conversation cap. Exit code 2 means a limit stopped an
incomplete session; 1 indicates an error and 130 indicates cancellation. Exit code 0
means the model finished, not that its changes are correct or tested. Applied edits
remain on disk if the session stops or fails—there is no session-wide rollback.
Use version control or backups and review the printed list of files actually changed.

Editing currently supports replacements, approved creation of explicitly named new
files, and, with `--allow-commands`, running shell commands — no file deletion, no
directory creation, and no custom skill files yet. Its integration tests use mocked
model responses and temporary files; live model-driven editing has not been verified.

### Running commands (`--allow-commands`)

Off by default. With it, the model gains a `run_command` tool — use it to build, lint,
or run tests on the files it just edited, closing the loop that used to require you to
verify changes yourself. The safety model is identical to file edits: the model proposes
one exact command, you see it and the working directory, and must type `yes` before
anything runs. **It is not a sandbox** — an approved command runs with your full user
privileges, filesystem access, and network, exactly as if you'd typed it yourself.
Approval is the only boundary; review every command before approving it, the same way
you'd review a diff. `--command-timeout` (default 120s) kills a hung command; stdout and
stderr are each capped at 32,000 characters before being shown back to the model.

## Current boundaries

This release can explore a workspace with read-only tools, apply approved replacements,
create approved new files, and, opt-in, run approved shell commands (`--allow-commands`)
— but that opt-in is not a sandbox, so read the section above before turning it on.
`--auto-approve true` waives the prompts entirely for unattended runs; it is off by
default for a reason. It does not connect to the Forgefy admin catalogue. Provider
profiles and skill files are the initial plugin interfaces, not a full autonomous
coding-agent system.
Output is untrusted: inspect it before running anything, whether it's a file diff or a
command result. Tests use mocked HTTP (and, for the process-level suite, a real loopback
server), not live model quality benchmarks.

## Tests

```powershell
pytest
```

## Releasing (maintainers)

CI runs on every push/PR (`.github/workflows/ci.yml`). One tag produces both
distribution channels:

1. Bump `version` in `pyproject.toml` and commit.
2. `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. `.github/workflows/release.yml` then, in parallel:
   - builds and publishes the PyPI package via Trusted Publishing — no token
     stored in the repo. One-time setup: on the PyPI project's *Publishing*
     settings, add a Trusted Publisher for `Polybamz/forgefy-cli`, workflow
     `release.yml`, environment `pypi`.
   - builds a standalone `forgefy` binary for Windows/macOS/Linux with
     PyInstaller and attaches them to a GitHub Release for the tag — what
     `install.sh`/`install.ps1` fetch. No setup needed; uses the repo's
     built-in `GITHUB_TOKEN`.

To build the standalone binary locally (e.g. to test before tagging):

```powershell
pip install -e . pyinstaller
pyinstaller --onefile --name forgefy --paths src --hidden-import anyio._backends._asyncio --distpath dist_native --workpath build_native --specpath build_native build_installer/entrypoint.py
.\dist_native\forgefy.exe --help
```

