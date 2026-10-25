# Implementing `--no-input` for agent use

Instructions for adding an agent-friendly mode to an existing Click CLI. The
human path stays the default. Agents pass `--no-input` (also
`--non-interactive` and `-n`) and then drive the command entirely with
options and arguments.

`--no-input` is the established name from the Command Line Interface
Guidelines. `--non-interactive` and `--no-interaction` / `-n` are the same
switch in other tools. `--agent` is not a convention. `--yes` is a different
switch: it answers one confirmation, and it must not be treated as
`--no-input`.

## Outcome

After this change:

- A human running the command in a terminal gets the existing prompts,
  progress display, and prose output.
- An agent passes `--no-input` and gets no prompts, no redraws, no cursor
  changes, no emoji, and no color, with every input supplied by a flag or
  argument.
- A pipe (stdout or stdin not a terminal) never hangs on a prompt and never
  fills the capture with spinner frames, even when the flag is absent.
- A pseudo-terminal still looks interactive to `isatty()`. The flag is what
  forces the agent path there.

## Mode rule

Implement a human path and an agent path whenever the two can differ.

If some operation cannot be separated into a human-friendly mode and an
agent-friendly mode, default to the human-friendly mode. Agents are expected
to pass `--no-input`. Do not silently change the default output format, the
wording of a successful result, or the command's meaning because stdin or
stdout is a pipe, because `CI` is set, or because `isatty()` is false.

When `--no-input` is set and a command truly has no non-interactive form,
exit with usage code 2 and one sentence naming what is missing. Do not guess
a value and do not open a browser, editor, or pager.

Two behaviors are not a mode choice. They are required so a pipe cannot hang
or produce an unreadable capture, for a human script as well as an agent:

- Never wait on stdin when stdin is not a terminal.
- Never write carriage-return updates, cursor-hiding sequences, or
  clear-line sequences when the stream being written is not a terminal.

## Shared state

One program-wide state object. Every subcommand reads that object. Do not
give each subcommand its own independent meaning for the flag.

```python
from dataclasses import dataclass

@dataclass
class RunState:
    # Set only when this process was given the flag, an alias, or the env var.
    no_input: bool = False
    # None: caller did not pass --color / --no-color.
    # True / False: caller did.
    color_override: bool | None = None
```

Resolve derived behavior at the start of the command body, after parsing,
not inside scattered call sites:

```python
import os
import sys

import click

def prompts_blocked(state: RunState) -> bool:
    """True when a prompt would hang or was explicitly refused."""
    return state.no_input or not sys.stdin.isatty()

def redraws_allowed(state: RunState, stream) -> bool:
    """True only for a human terminal on this stream."""
    return not state.no_input and stream.isatty()

def color_enabled(ctx: click.Context, state: RunState, stream) -> bool:
    """Explicit --color / --no-color wins. Otherwise color is for humans."""
    if state.color_override is not None:
        return state.color_override
    if os.environ.get("NO_COLOR"):
        return False
    if state.no_input:
        return False
    return stream.isatty()
```

`NO_COLOR` follows the no-color.org rule: if the variable is present and
non-empty, color is off. An explicit `--color` overrides it.

`--no-input` turns color off so a pseudo-terminal capture does not contain
ANSI codes. It does not change the result format. JSON stays opt-in through
a separate `--format json`.

## Click: one global option, visible on every command

Click parses a group's options only when they appear before the subcommand
name. `tool --no-input sync` works. `tool sync --no-input` fails unless
`sync` itself declares the option. Agents copy flags from `tool sync --help`,
which lists only that command's options.

Register the same option on the root group and on every subcommand, including
nested groups. Each copy writes the same `RunState`. A single-command CLI
registers it once, on that command.

Record a value only when this copy of the option was actually supplied.
The other copy still runs with its default (`False` or `None`). Writing that
default would clear the flag the caller did pass.

`Context.ensure_object` walks parent contexts and returns the existing
object. Always read state with `ctx.ensure_object(RunState)` or
`ctx.find_object(RunState)`. A subcommand's own `ctx.obj` stays `None` until
something assigns it. The parent object is still the one `ensure_object`
returns.

Child contexts copy `parent.color` when they are created, which is after the
group has finished parsing. Set `ctx.color` in the eager callback of whichever
command received the color flag, and set it again from `color_enabled()` at
the start of the command body so the final resolution (`NO_COLOR`,
`--no-input`, the stream) is what `click.echo` sees.

```python
import os

import click

NO_INPUT_ENV = "NO_INPUT"


def _provided(ctx: click.Context, param: click.Parameter) -> bool:
    source = ctx.get_parameter_source(param.name)
    return source in (
        click.core.ParameterSource.COMMANDLINE,
        click.core.ParameterSource.ENVIRONMENT,
    )


def _no_input_callback(ctx: click.Context, param: click.Parameter, value: bool):
    if ctx.resilient_parsing or not _provided(ctx, param):
        return value
    state = ctx.ensure_object(RunState)
    if value:
        state.no_input = True
    return value


def _color_callback(ctx: click.Context, param: click.Parameter, value: bool):
    if ctx.resilient_parsing or not _provided(ctx, param):
        return value
    state = ctx.ensure_object(RunState)
    state.color_override = value
    ctx.color = value
    return value


def global_agent_options(command):
    """Attach --no-input and --color/--no-color. Apply to every command."""
    command = click.option(
        "--color/--no-color",
        "color_override",
        default=None,
        is_eager=True,
        expose_value=False,
        callback=_color_callback,
        help="Force color on, or off. Default: color for a human terminal.",
    )(command)
    command = click.option(
        "--no-input",
        "--non-interactive",
        "-n",
        is_flag=True,
        default=False,
        is_eager=True,
        expose_value=False,
        envvar=NO_INPUT_ENV,
        callback=_no_input_callback,
        help=(
            "Do not prompt or show animated progress. "
            "Pass every input as an option. Missing input is an error."
        ),
    )(command)
    return command
```

`is_eager=True` runs these callbacks before options that prompt during parse.
`expose_value=False` keeps command functions from growing a parameter they
do not read. `ctx.resilient_parsing` is set while Click builds help and
shell completion; those callbacks must not change behavior.

If the CLI sets `auto_envvar_prefix`, also accept the prefixed variable Click
will invent, or set `envvar` explicitly as above so the documented name stays
`NO_INPUT`.

Apply the decorator under `@click.command()` / `@click.group()` so it is
stored on the command:

```python
@click.group()
@global_agent_options
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.ensure_object(RunState)


@cli.command("sync")
@global_agent_options
@click.pass_context
def sync_cmd(ctx: click.Context) -> None:
    activate(ctx)
    ...
```

`activate` is the one place that resolves prompts, color, and redraws for
this invocation:

```python
def activate(ctx: click.Context) -> RunState:
    state = ctx.ensure_object(RunState)
    ctx.color = color_enabled(ctx, state, sys.stdout)
    return state
```

Call `activate` at the top of every command body. Library code that runs
during option callbacks needs the eager callbacks above, because `activate`
has not run yet.

## Prompts, missing values, destructive actions

Search the CLI for `click.prompt`, `click.confirm`, `click.prompt` options
(`prompt=True`), `input(`, `getpass`, menus, "press any key", `click.pause`,
`click.edit`, and `click.launch`. Route all of them through helpers that
consult `prompts_blocked`.

Missing value. When the human path would prompt, the agent path raises
`click.UsageError` naming the option to pass. Exit code is 2. Do not invent
a default the human was about to type. A real default that the command
already uses without asking can stay.

```python
def require_or_prompt(ctx, state, option, label, existing):
    if existing is not None:
        return existing
    if prompts_blocked(state):
        raise click.UsageError(
            f"Missing {option}. Pass it on the command line."
        )
    return click.prompt(label)
```

Destructive action. A missing confirmation is an error under
`prompts_blocked`. The command exits 2 and names the command-specific flag
(`--yes`, `--force`, or the flag this command already uses). Proceed only
when that flag is present.

On a human terminal with `--no-input` absent, prompt. The default answer is
no (`click.confirm(..., default=False)`). An empty Enter must not delete,
overwrite, or send.

`--yes` is per command, because only some commands are destructive. The
policy is global: `--no-input` never implies yes. A global `--yes` that
confirms every future prompt is the wrong tool.

Authentication that would open a browser or start a device-code prompt needs
a flag for the token (or a documented environment variable). With prompts
blocked and no token, exit 2 and name that flag.

## Spinners and other redraws

Redraws are progress that paints over the same line: spinners, percent bars,
`\r` updates, and "clear the line" sequences. They are display, not the
result.

Show them only when `redraws_allowed(state, sys.stderr)` is true, and write
them only to stderr. The result stays on stdout, one stable payload, with
newlines and no carriage returns.

When redraws are off, either print nothing or print one flushed status line
to stderr when a long step starts or finishes. Do not emit a frame per tick.

This covers:

- yaspin and other spinner libraries
- `click.progressbar` (it rewrites the line with `\r`; pass `hidden=True`
  when redraws are off, and point `file` at stderr when they are on)
- handmade `\r`, `\033[K`, `\033[2K`, and cursor-movement progress
- `click.clear()`

Do not run a multi-second animation, a pager, or a follow/watch loop on the
path an agent takes. `click.pause` already returns immediately when stdin or
stdout is not a terminal. Do not call `click.echo_via_pager` when
`--no-input` is set or stdout is not a terminal.

Flush every status line. `click.echo` flushes. `print` on a pipe is block
buffered and can hide the whole log until the process exits or is killed.

### yaspin

Calling yaspin on a non-terminal is not "off". In yaspin 3.5 the live frame
is `\r` plus the frame and the text, flushed on every interval. When the
stream is not a terminal, `_clear_line` still writes `\r`, a run of spaces,
and another `\r`. A captured pipe keeps every byte, so the tool result is a
stream of overwritten lines.

yaspin also does the following, which the caller has to account for:

- The default stream is stdout. That mixes progress into the result. Pass
  `stream=sys.stderr` on the human path, or do not call yaspin.
- Requesting `color` when the stream is not a terminal emits
  `UserWarning: color, on_color and attrs are not supported when output
  stream is not a TTY` on stderr. Agents treat unexpected stderr as failure.
  Do not construct a colored spinner unless `redraws_allowed` is true.
- Cursor control is `\033[?25l` (hide) and `\033[?25h` (show), and only when
  that stream reports `isatty()`. `start()` hides the cursor, then a
  `finally` shows it again as soon as the spinner thread has started.
  `stop()` shows it again. A kill in the window between hide and show, or
  any other library that hides for the whole spin, leaves the cursor hidden
  if the consumer allocated a pseudo-terminal. `SIGKILL` cannot run cleanup.
- The default `sigmap` is empty, so yaspin does not install a `SIGINT`
  handler unless the caller passes one. A custom handler has to be restored.
  yaspin restores handlers it installed only if `stop()` runs.
- Width is sampled once. A narrow pseudo-terminal can raise `ValueError`
  ("Terminal size … is too small") while composing a frame.

The agent path and any non-terminal stream must skip the yaspin call. There
is no yaspin argument that disables the redraw loop.

## Visual noise

What pollutes a captured tool result is cursor hiding, color and other ANSI
codes, and `\r` updates. Emoji is a separate problem: agents mis-handle
emoji graphemes in CLI output (zero-width joiners, variation selectors,
width). Demojize them.

Demojize means replace each emoji grapheme with a colon shortcode
(`:warning:`), not delete it and not leave the raw character.
`emoji.demojize` does this, including joined sequences. Apply it to text the
CLI composes: status lines, errors, prompts that became errors, and other
chrome. Apply it when `state.no_input` is true.

Leave payload bytes unchanged when the command's job is to print the user's
data (file contents, a JSON field the user asked for). Demojizing that data
would change the result. A sentence the CLI itself wrote is chrome.

Also drop, on the agent path and on any non-terminal stream:

- the terminal bell (`\a`)
- screen clears and cursor addressing
- Braille or ASCII spinner frames
- styled progress glyphs

Write CLI chrome through one helper so a later `print` cannot bypass the
rules:

```python
def emit(message: str, *, err: bool, state: RunState) -> None:
    text = emoji.demojize(message) if state.no_input else message
    click.echo(text, err=err)
```

`click.echo` strips ANSI when its destination is not a terminal, unless
`color=True` or `ctx.color` is `True`. `ctx.color = False` strips even on a
pseudo-terminal. Set `ctx.color` from `color_enabled()`. That covers
`click.style` and `click.secho`. It does not cover a library that writes ANSI
with `sys.stdout.write`. Those call sites go through `emit`, or they stay
behind `redraws_allowed` / `color_enabled`.

## Stdout, stderr, pipes, pseudo-terminals

| Kind of text | Stream | When it appears |
| --- | --- | --- |
| The result | stdout | Always, once, stable |
| Logs, progress, warnings | stderr | Progress only if redraws are allowed; otherwise one line or silence |
| Errors | stderr | One message, then a non-zero exit |
| `--help` | stdout | Click already does this, exit 0 |

The result is plain text or, when the caller passed `--format json`, one
JSON document. `--no-input` does not switch the format. A pipe does not
switch the format. A human who pipes the command still gets the human result,
without spinner frames mixed in.

Do not put logs on stdout. Do not put the result on stderr.

A pipe makes `isatty()` false. A pseudo-terminal makes `isatty()` true even
though the reader is an agent. That is why redraws and color follow both the
stream and `--no-input`, and why prompts follow both stdin and `--no-input`.

Expected conditions must not warn. A `UserWarning` on stderr for "this is
not a terminal" is an ordinary agent failure. Avoid the call that warns.

## Exit codes and errors

| Situation | Code | Where |
| --- | --- | --- |
| Finished, result on stdout | 0 | stdout |
| `--help` | 0 | stdout |
| Bad flags, missing option, missing `--yes`, unknown command | 2 | stderr via `click.UsageError` |
| Runtime failure | 1 | stderr via `click.ClickException` |
| Interrupt that Click handles (`Abort`) | 1 | stderr, the text `Aborted!` |
| Broken pipe | 1 | Click handles `EPIPE` | 
| `SIGTERM` after terminal cleanup | 143 | optional one-line stderr |

Exit 0 means the operation finished and the result is on stdout. A partial
run, a skipped destructive action, or a missing input is not 0.

One error line. No traceback unless the user passed an existing verbose or
debug flag. The line names the flag to pass when the failure is missing
input.

Click's `main` turns `KeyboardInterrupt` and `EOFError` into `Abort`, prints
`Aborted!` to stderr, and exits 1. A `finally` inside the command still runs
before that. Use it for terminal cleanup, then let the interrupt propagate.
Do not catch it and exit 0.

## Cleanup on kill

Any human-path code that hides the cursor or switches the terminal into raw
mode restores it on the way out:

- `try` / `finally` around the spinner or progress display
- `atexit` handler that sends cursor-show (`\033[?25h`) if this process hid
  the cursor
- `SIGTERM` handler that runs the same restore, then exits 143

`SIGINT` is already turned into `KeyboardInterrupt` by Python and then into
Click's `Abort`. The `finally` block is the restore for that path.

`SIGKILL` runs no handler and no `finally`. Agent harnesses often `SIGTERM`,
wait, then `SIGKILL` on timeout. The agent path must not hide the cursor and
must not switch the terminal mode, so a kill cannot leave the consumer's
pseudo-terminal unusable.

If a spinner thread is running, `stop()` it and join it in the `finally`
before restoring the cursor, so a late frame does not land after the restore.

## The contract an agent consumes

Check the CLI against this list. `--no-input` plus color handling is the
mechanism. These are the properties the result has to have.

0. The command does not wait. Prompts, pagers, editors, browsers, and
   follow/watch mode are off when `prompts_blocked` is true. A missing value
   is usage code 2 and names the option.
1. Stdout is one stable payload. Plain text stays the default. `--format json`
   is how the caller opts into a machine-readable document. Keys and ordering
   are stable. Timestamps and progress are not inside the result unless the
   command's data includes them on purpose.
2. Stderr is diagnostics only. Progress is either a human terminal redraw or
   absent. Warnings are for unexpected conditions.
3. Exit codes match the table above. The agent can branch on the code without
   parsing prose.
4. The run is bounded. No default animation, no sleep-for-display, no open
   ended tail. Status lines are flushed so a timeout still returns a partial
   log.
5. Help works non-interactively, exits 0, and lists every flag the agent must
   pass. See the next section.
6. Color, cursor motion, and `\r` updates are absent under `--no-input` and
   absent when the destination is not a terminal. Emoji in CLI chrome is
   demojized under `--no-input`.
7. Destructive work requires its own explicit flag. The default is not yes.

## Help text

Help is for both readers. A human has to understand it. An agent has to find
the flags.

Write the option's `help` as one or two plain sentences: what changes, and
that missing input becomes an error. That text is the right length for a
human scanning `--help`. Put a fully spelled invocation in the command
docstring's examples, with the values an agent would pass.

Keep the human description first. State that the terminal form asks for
missing values. Then show the flagged form. Do not replace the description
with a note about agents.

Keep emoji out of help text always, so the same bytes are shown with and
without `--no-input`.

`--help` must not prompt, must not start a spinner, and must not require
credentials. Click already exits 0 and writes help to stdout when parsing
succeeds. Do not run `activate` side effects at import time or in an eager
callback that ignores `ctx.resilient_parsing`.

Because the decorator is on each subcommand, `tool sub --help` shows
`--no-input`, `--non-interactive`, `-n`, and `--color` / `--no-color`. The
root `tool --help` shows them too.

## Work order

0. Add `RunState`, the three resolvers, `global_agent_options`, and
   `activate`.
1. Decorate the root and every subcommand. Confirm both flag positions parse:
   `tool --no-input sub` and `tool sub --no-input`, plus `-n` and
   `--non-interactive`.
2. Replace direct prompts and confirms with the helpers. Add or reuse a
   per-command `--yes` for destructive commands. Default the human confirm
   to no.
3. Gate every spinner, progress bar, pager, and `\r` writer on
   `redraws_allowed` for stderr. Point any remaining human progress at
   stderr. Remove yaspin calls from the other path rather than passing a
   non-terminal stream into yaspin.
4. Route CLI chrome through `emit`. Demojize when `state.no_input`. Set
   `ctx.color` from `color_enabled`. Honor `NO_COLOR` and an explicit
   `--color`.
5. Make stdout the result only. Move logs and warnings to stderr. Leave the
   success format unchanged unless the caller passed `--format json`.
6. Add `finally` and `SIGTERM` cleanup for any remaining cursor or raw-mode
   change.
7. Re-read each command's help. The human task is still the first sentence.
   The flagged example is present. The option help says missing input is an
   error.

## Checks

Run these against the built command. A pseudo-terminal check matters, because
the pipe checks do not catch a harness that allocates a tty.

0. `tool --help` and `tool sub --help` both list `--no-input`,
   `--non-interactive`, `-n`, and `--color` / `--no-color`. Exit 0. Help is
   on stdout.
1. `tool sub --no-input`, `tool --no-input sub`, `tool sub -n`, and
   `tool sub --non-interactive` all set the same state. `NO_INPUT=1` does too.
2. With the flag, a missing value that used to prompt exits 2. Stderr names
   the option. Stdout has no partial result.
3. A destructive command with the flag and without `--yes` exits 2 and does
   not perform the action. With `--yes`, it does. On a terminal without the
   flag, Enter at the confirm does not perform the action.
4. Piped stdout contains no `\r`, no `\033[`, and no spinner frames. Stderr
   has no `UserWarning` about a missing terminal. Exit 0 still returns the
   human result format.
5. Under a pseudo-terminal, the same command without the flag may prompt and
   may colorize. With `--no-input`, it does not prompt, does not hide the
   cursor, does not write `\r` or color, and demojizes CLI chrome.
6. `--no-color` strips ANSI on a terminal. `--color` keeps ANSI on a pipe.
   `NO_COLOR=1` strips ANSI unless `--color` is also present.
7. `SIGTERM` during a human spinner restores the cursor. The agent path never
   writes `\033[?25l`.
