#!/usr/bin/env python3
"""Compare yaspin spinners. Run: uv run spin"""

import time

import click
from yaspin import yaspin
from yaspin.spinners import Spinners

# Braille spinners (moon, dots*) keep a constant glyph width, so they usually
# look smoother. ASCII spinners can jitter more.
DEFAULT_SPINNERS = (
    "dots",
    "dots2",
    "dots12",
    "moon",
    "line",
    "pipe",
    "star",
    "bouncingBar",
)


def _spinner(name: str):
    try:
        return getattr(Spinners, name)
    except AttributeError as exc:
        raise click.BadParameter(f"unknown spinner {name!r}") from exc


def _with_interval(spinner, seconds: float):
    """Return a copy of spinner whose frame interval is seconds.

    yaspin stores the interval on the spinner in milliseconds and does not
    take an interval argument of its own.
    """
    millis = round(seconds * 1000)
    if millis < 1:
        raise click.BadParameter("must be at least 0.001 seconds")
    return spinner._replace(interval=millis)


@click.command()
@click.option(
    "--interval",
    "-i",
    type=click.FloatRange(min=0.001),
    default=0.08,
    show_default=True,
    help="Seconds between frames.",
)
@click.option(
    "--hold",
    type=click.FloatRange(min=0),
    default=3.0,
    show_default=True,
    help="Seconds to show each spinner before updating its text.",
)
@click.option(
    "--update",
    "update_for",
    type=click.FloatRange(min=0),
    default=1.0,
    show_default=True,
    help="Seconds to show each spinner after the text update.",
)
@click.option(
    "--color",
    default="cyan",
    show_default=True,
    help="Spinner color.",
)
@click.option(
    "--side",
    type=click.Choice(["left", "right"], case_sensitive=False),
    default="left",
    show_default=True,
    help="Side of the text the spinner sits on.",
)
@click.argument("names", nargs=-1, metavar="[SPINNER]...")
def main(
    interval: float,
    hold: float,
    update_for: float,
    color: str,
    side: str,
    names: tuple[str, ...],
) -> None:
    """Cycle yaspin styles and watch for blink or jitter.

    With no SPINNER names, the built-in comparison set is used.
    Press Ctrl+C to stop.
    """
    chosen = [
        (name, _with_interval(_spinner(name), interval))
        for name in (names or DEFAULT_SPINNERS)
    ]
    click.echo("Yaspin spinner comparison")
    click.echo("Each spinner runs, then its text updates, then the next one starts.")
    click.echo("Watch for blink / jitter. Braille styles usually look smoother.")
    click.echo()

    try:
        for name, spinner in chosen:
            label = f"{name:12}"
            with yaspin(
                spinner,
                text=f"Testing {label}",
                color=color,
                side=side,
            ) as sp:
                time.sleep(hold)
                sp.text = f"Updating {label}"
                time.sleep(update_for)
    except KeyboardInterrupt:
        click.echo()
        click.echo("Interrupted.")
        raise SystemExit(130) from None

    click.echo()
    click.echo("Done. Try adjusting interval or testing in a plain terminal vs IDE.")


if __name__ == "__main__":
    main()
