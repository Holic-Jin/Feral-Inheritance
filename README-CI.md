# Feral Inheritance: CI and model gateway

This repository is used to quality-test a low-cost coding flow. A free NVIDIA-hosted model
proposes a change as a **unified diff**. A stdlib-only Python script validates it, applies it on a
**fresh branch** and opens a **draft PR**. CI checks the PR and QA reviews it. The model never runs
commands and never sees secrets. Nothing merges automatically; the owner merges.

## Flow

1. The owner starts **Actions → model-call → Run workflow** from `main` (`workflow_dispatch` only)
   with `task_id`, `task`, optional context `files`, optional `model` and optional `dry_run`.
2. `tools/model_gateway.py` (Python standard library only, no SDKs):
   1. Policy checks, before anything else:
      - The project must be in the hard-coded allowlist (`feral-inheritance` only;
        `tools/gateway_config.json` can only narrow it).
      - Anything mentioning **FinisMundi** is refused in the project, task, task_id, model, file paths,
        repo path and target repo name (`github_repo` and `GITHUB_REPOSITORY`), even if allowlisted.
        The check applies NFKD, strips combining marks and format characters, lowercases, and maps
        confusables (Cyrillic and Greek lookalikes, `Ϝ`/`ꜰ`/`ƒ`→`f`, `ı` and `l`→`i`, `5`→`s`,
        `!` and `|`→`i`, `$`→`s`, `3`→`e`, plus `1`→`i` and `0`→`o`). The full name matches anywhere,
        including inside `xFinisMundi` or `FinisMundi2` and with spaces between letters. Standalone
        `finis` still requires a word boundary, so `finish`, `Definis`, `infinis` and `fin is` are not matches.
      - `task_id` must match `^[a-z0-9][a-z0-9_-]{0,47}$`.
      - The task text and every context file are scanned for secrets (AWS keys, GitHub
        `ghp_/gho_/github_pat_` tokens, `nvapi-` keys, `sk-` keys, private-key blocks, Slack, Google API keys,
        JWTs, URLs with passwords, `.env`-style `KEY=long-random`, and the live values of `NVIDIA_API_KEY`,
        `GITHUB_TOKEN`, `GH_TOKEN`). A hit refuses the run without echoing the value.
   2. **Fresh branch per run.** It fetches the latest `origin/main` and creates
      `model/<task_id>-<UTC timestamp>` (for example `model/fix-tox-cloud-20261026T091500Z`) from it.
      It refuses a dirty worktree, and it refuses if that branch already exists locally or on origin.
      A branch is never reused.
   3. Context files are read from that branch. Only allowed paths, plus `About/About.xml`, `LoadFolders.xml`
      and `Source/FeralInheritance.csproj` as read-only context, can be sent.
   4. Rate limit: at most **40 calls per rolling 60 s**. State is kept in `.gateway/rate_state.json`
      (file-locked). The workflow concurrency group `model-call` sets `cancel-in-progress: false`.
      GitHub runs one job in that group and keeps only one queued run: a newer trigger cancels the
      previously queued run, and the running job is left alone. There is no extra queue-length
      setting. The state file only lives as long as one job, which is why a second run must wait.
   5. It calls `https://integrate.api.nvidia.com/v1/chat/completions`. The default model is
      `moonshotai/kimi-k2.6`. A caller-supplied model id must match `^[A-Za-z0-9._/-]{1,100}$`,
      every `/` segment must start with an alphanumeric and must not be `.` or `..`, it must not
      contain a newline or other control character, and it is passed through the secret filter.
      The key comes from the `nvidia-gateway` environment secret `NVIDIA_API_KEY` only and is never
      printed or logged. urllib is configured with no proxy and a redirect handler that refuses every
      3xx, so the `Authorization` header is not sent to another host.
   6. The model is asked for a unified diff only. The gateway extracts it and validates it: allowed paths,
      deny list, traversal, symlinks, binaries, mode changes, renames, copies, deletes, and secrets in the
      added lines. Hunk bodies are parsed by the `@@` line counts, so a removed line that starts with
      `-- ` is content, not a file header. Patches are split on LF only (one trailing CR is CRLF);
      any other line separator is rejected, and a line after a hunk must be a header or blank.
      An empty hunk line with no leading space is context, which is what `git apply` accepts.
      It then cross-checks with `git apply --numstat --summary --check`.
      **If any file in the diff is denied, the whole diff is rejected.**
   7. It runs `git apply`, re-checks `git status`, commits as `feral-model-gateway`, pushes the branch with
      `GITHUB_TOKEN` (as a per-command header, never stored in `.git/config`) and opens a **draft PR** through
      the GitHub REST API (`urllib`). The PR title has newlines and control characters stripped and is capped
      at 120 characters. A 3xx response or an empty PR URL is an error and is not recorded as `pr_opened`.
   8. **Call log:** one append-only JSONL line per run, written whether the run succeeds or is refused.
      Every line has `task_id`, `timestamp` (UTC), `model`, `prompt_sha256`, `prompt_tokens`,
      `completion_tokens`, `total_tokens` (each `null` when the API's `usage` is missing or malformed) and
      `outcome`. Optional fields are `project`, `http_status`, `duration_ms`, `dry_run`, `paths`, `branch`,
      `base_sha` and `pr`. Prompt and response bodies and keys are never logged. In Actions the log is
      written to `$RUNNER_TEMP/gateway/calls.jsonl` and printed to the run's **step summary** by
      `--summarize-log` only. Table cells have newlines and control characters removed, and `|`,
      backticks, `<` and `>` escaped. The JSONL in the summary is re-encoded, not concatenated raw.
      An unexpected exception is logged as `outcome=internal_error` with no traceback (exit code 1).
      `actions/upload-artifact` is not used yet because it has not been reviewed.
3. Job B of **model-call** dispatches **test.yml** on that branch. See "Test dispatch" below.
   `test.yml` itself is unchanged: `pull_request`, `push` to `main`, and `workflow_dispatch`.
4. QA reviews. The owner decides and merges.

Exit codes: 0 ok, 1 internal error (no traceback), 2 policy refusal (FinisMundi, allowlist, secret,
rate limit, task_id, model id), 3 model/API error (including a refused redirect), 4 invalid or denied diff,
5 git/GitHub error (including `branch_exists`, `git_dirty_worktree`, a refused PR redirect, or an empty PR URL),
6 usage.

Local use:

```sh
# no network at all: no fetch, no branch, no model call
python3 tools/model_gateway.py --task-id demo-1 --task "..." --files Source/FeralAbility.cs --dry-run [--diff-file my.diff]
# token usage table from a call log
python3 tools/model_gateway.py --summarize-log .gateway/calls.jsonl
```

`--no-pr` does a real run (fetch, branch, model call, apply, commit) but does not push or open a PR.

## Branch per run, and cleanup

- Every run creates its own `model/<task_id>-<UTC timestamp>` from the latest `origin/main`, and never
  continues an earlier branch.
- If a run fails or is interrupted, the only thing it can leave behind is that one branch: it may be
  pushed without a PR if PR creation failed. The `main` branch and other branches are never touched.
  Rerunning the same `task_id` always gets a new timestamped branch.
- Cleanup (the error message prints the exact branch name):

  ```sh
  git ls-remote --heads origin 'refs/heads/model/*'   # list model branches on GitHub
  git push origin --delete model/<task_id>-<timestamp> # delete one leftover remote branch
  git branch -D model/<task_id>-<timestamp>            # delete it locally, if it exists
  ```

  Or delete the branch on GitHub under **Branches**. Then run the workflow again.

## Allowed paths (exact globs, matched to the real layout)

A model diff may only create or modify files matching these globs. Matching is case-sensitive, and `**/`
means zero or more directories. They are hard-coded in `ALLOWED_PATH_GLOBS` in `tools/model_gateway.py`;
`allowed_path_globs` in the config can only narrow them.

| Glob | What it covers |
|---|---|
| `Source/**/*.cs` | C# sources (new `.cs` files compile automatically, `EnableDefaultCompileItems=true`) |
| `Common/Defs/**/*.xml` | core Defs (loaded for 1.6 and 1.7 via `LoadFolders.xml`) |
| `Common/Patches/**/*.xml` | XML patches (no Patches folder exists in 0.9.1 yet) |
| `Common/Languages/**/*.xml` | English/Korean Keyed + DefInjected |
| `1.6/Defs/**/*.xml`, `1.6/Patches/**/*.xml`, `1.6/Languages/**/*.xml` | 1.6-only content (currently none) |
| `1.7/Defs/**/*.xml`, `1.7/Patches/**/*.xml`, `1.7/Languages/**/*.xml` | 1.7-only content (currently none) |
| `Mods/Biotech/Defs/**/*.xml`, `Mods/Biotech/Patches/**/*.xml`, `Mods/Biotech/Languages/**/*.xml` | Biotech-only content (loaded `IfModActive`) |

Paths may only use `A–Z a–z 0–9 . _ - /`, and new files must be mode `100644`.

Always denied (hard-coded, case-insensitive, checked before the allowlist):
`.github/**`, `tools/**`, `**/bin/**`, `**/obj/**`, `**/Assemblies/**`, `.git/**`, `*.csproj`, `*.sln`,
`Directory.Build.*`, `Directory.Packages.props`, `nuget.config`, `*.props`, `*.targets`, `global.json`,
`packages.lock.json`, `.gitignore`, `.gitattributes`, `.gitmodules`, `.editorconfig`, `About/**`,
`LoadFolders.xml`, `*.yml`/`*.yaml`, `*.dll`, `*.pdb`, `*.exe`, `*.sh`, `*.ps1`, `*.bat`, `*.cmd`, `*.py`,
and any path mentioning FinisMundi (each at any depth). Also denied: path traversal (`..`, `.`, empty
segments, absolute paths, backslashes, NUL), quoted or non-ASCII paths, symlinks (in the diff or on disk),
binary patches, mode changes, renames, copies and deletions.

## Tests: `.github/workflows/test.yml`

- Runs on `pull_request`, on `push` to `main` and on manual `workflow_dispatch`. It never uses
  `pull_request_target`, has `permissions: contents: read` only, and uses no secrets.
- Job matrix `rimworld: ["1.6", "1.7"]`. The check-run names are exactly **`test (1.6)`** and **`test (1.7)`**.
- Steps:
  1. Plain-git checkout of `GITHUB_SHA` (no third-party action; see "Actions" below).
  2. `actions/setup-dotnet@a98b56852c35b8e3190ac28c8c2271da59106c68 # v6.0.0` with
     `global-json-file: global.json` and `cache: false`.
  3. `dotnet restore Source/FeralInheritance.csproj --locked-mode --configfile nuget.config`. The package
     folder is in `$RUNNER_TEMP` and is never cached.
  4. `dotnet build -c Release -f net472` (1.6) or `-f net10.0` (1.7) with `--no-restore`.
  5. Check that the output folder holds only `FeralInheritance.dll`, with no reference assemblies.
  6. `tools/check_xml.py`: every tracked `*.xml` is well-formed (expat), has no DOCTYPE or ENTITY, and its
     root element matches its folder.
  7. `python3 -m unittest discover -s tools/tests -v`: gateway unit tests, with no network.
- RimWorld itself cannot run in Actions, so the tests stop at compiling and XML checks.

## NuGet and .NET conditions (from `csharp-tools-review.md`)

- `global.json` pins SDK **10.0.403** exactly (`rollForward: disable`).
- `Source/packages.lock.json` comes from `RestorePackagesWithLockFile=true`, and CI restores with `--locked-mode`.
  - net472 / RimWorld 1.6: `Krafs.Rimworld.Ref 1.6.4871`, `Lib.Harmony 2.4.2`,
    `Microsoft.NETFramework.ReferenceAssemblies 1.0.3` (+ `.net472 1.0.3`).
  - net10.0 / RimWorld 1.7: `Krafs.Rimworld.Ref 1.7.4985-unstable`, `Lib.Harmony 3.0.0-rc.3`.
- `nuget.config`:
  - nuget.org is the only source (`<clear/>`).
  - `packageSourceMapping` maps each of the four package IDs to nuget.org.
  - `trustedSigners` trusts the nuget.org repository signature, using the two certificate fingerprints
    actually found on the locked packages, with owners `krafs;pardeike;Microsoft`.
  - `signatureValidationMode` is left at its default (`accept`), because the review does not require
    `require`. `trustedSigners` is still enforced: a wrong owner list fails restore with NU3034.
- Reference assemblies are compile-only: `ExcludeAssets="runtime"`, `PrivateAssets="all"` and
  `CopyLocalLockFileAssemblies=false`. There is no `actions/cache`, no artifact upload of build output, and
  `bin/`, `obj/` and `*.dll` are git-ignored. The Harmony runtime comes from the Harmony mod
  (`brrainz.harmony`).

### Pre-release note (RimWorld 1.7)

The **1.7 build uses `Lib.Harmony 3.0.0-rc.3`, a pre-release** (a release candidate, built from the work
branch `fix/issue-783-method-generation`, not master). It is used only as a compile-time reference; the
runtime Harmony is the Harmony mod. **`Krafs.Rimworld.Ref 1.7.4985-unstable` is a pre-release too** (there
is no 1.7 stable reference package yet). Replace both when stable versions exist, and re-check the diff and
release notes on every bump.

## Actions used

| Action | Pin | Status |
|---|---|---|
| `actions/setup-dotnet` | `a98b56852c35b8e3190ac28c8c2271da59106c68` (v6.0.0, lightweight tag, checked with `git ls-remote`) | reviewed, passed with an SHA pin |
| `actions/checkout` | not used, replaced by plain `git` | not reviewed; the review only names it in its "recommended CI" summary. v6.1.0 resolves to `d23441a48e516b6c34aea4fa41551a30e30af803`, for later. |
| `actions/upload-artifact` | not used, replaced by the step summary | not reviewed. v6.0.0 resolves to `b7c566a772e6b6bfb58ed0dc250532a479d7789f`, for later. |

## Owner's manual steps (Nothing)

1. Push this tree to `https://github.com/Holic-Jin/Feral-Inheritance` (`main`).
2. Under **Settings → Branches → Add rule** (or a Ruleset) for **`main`**:
   - Require a pull request before merging.
   - Require status checks to pass, and add exactly **`test (1.6)`** and **`test (1.7)`**. They appear after
     the first workflow run on `main`.
   - Block force pushes, and block deletion.
3. Under **Settings → Environments**, create an environment named **`nvidia-gateway`**.
   - Limit **Deployment branches** to **`main`** only.
   - Add **`NVIDIA_API_KEY`** as an **environment secret** on `nvidia-gateway`. Do not add it as a
     repository secret.
   - Type it yourself; it is never stored on the shared computer.
   The gateway job sets `environment: nvidia-gateway` and still runs only when `github.ref` is
   `refs/heads/main`. This repository file does not create the environment.
4. Under **Settings → Actions → General**:
   - Set Workflow permissions to **Read repository contents**; each workflow asks for more explicitly.
   - Enable **Allow GitHub Actions to create and approve pull requests**, which the gateway needs to open PRs.
   - Consider requiring approval for workflow runs from outside collaborators (fork PRs).
5. Under **Settings → Code security**, turn on secret scanning and push protection (free for public repos).
6. Accept the NVIDIA API Trial Terms. Prompts may be used to improve NVIDIA models, so send no
   confidential data.

## Test dispatch

PRs opened with the workflow `GITHUB_TOKEN` do not start `pull_request` workflows by themselves
(GitHub's recursion guard). `workflow_dispatch` is the documented exception: a `GITHUB_TOKEN` request
that creates a workflow dispatch **does** start a run. See
[Triggering a workflow from a workflow](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).
`POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches` accepts the workflow file name
as `{workflow_id}` and requires the **Actions: write** permission
([permissions required for GitHub Apps](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps)).

`model-call.yml` sets workflow-level `permissions: {}`. The two jobs ask only for what they use:

| Job | id | Token | Secrets | What it does |
|---|---|---|---|---|
| A | `gateway` | `contents: write`, `pull-requests: write` | `NVIDIA_API_KEY` environment secret on `nvidia-gateway` only | The existing gateway (`environment: nvidia-gateway`, still `if: github.ref == refs/heads/main`). On `pr_opened` it publishes the branch name and the PR number as job outputs. A dry run, a refusal, or any run that did not open a PR leaves the branch output empty. |
| B | `dispatch-tests` | `actions: write` only | none, and no checkout | `needs: gateway`, and runs only when `success() && needs.gateway.outputs.branch != ''`. |

Job B checks the branch against `model/<task-id>-<UTC timestamp>` (`task-id` is
`^[a-z0-9][a-z0-9_-]{0,47}$`, timestamp `YYYYMMDDTHHMMSSZ`, the same shape as
`branch_name()`). Any other ref fails the job. It then calls, with `GITHUB_TOKEN` via curl:

```
POST /repos/${{ github.repository }}/actions/workflows/test.yml/dispatches
{"ref": "<that branch>"}
```

The workflow file name `test.yml` is hard-coded in that URL, so this job cannot start another
workflow. The run checks out the branch head. Because a model diff cannot touch `.github/`, the
`test.yml` on that branch is the copy inherited from `main`. The check names on that SHA stay
**`test (1.6)`** and **`test (1.7)`**.

A manual **Actions → test → Run workflow** on the branch still works. `test.yml` is otherwise
unchanged: `permissions: contents: read`, no secrets, no `pull_request_target`.

## Not in this repository

- Built DLLs and PDBs (`1.6/Assemblies/*.dll`, `1.7/Assemblies/*.dll`, `bin/`, `obj/`).
- RimWorld game files and decompiled game code.
- A LICENSE file, because the license is not decided yet.

`1.6/Assemblies/` and `1.7/Assemblies/` are kept with `.gitkeep`. Build output goes there and is git-ignored.
