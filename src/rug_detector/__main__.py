"""CLI: `python -m rug_detector <command>`"""

from __future__ import annotations

import json
import logging

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import features as feat_mod
from . import label as label_mod
from . import score as score_mod
from .config import get_settings
from .db import run_sql_file

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    # httpx and httpcore log full request URLs at INFO. Etherscan's API takes
    # the API key as a query parameter, so its key would leak into terminal
    # output and any log files / transcripts. Pin third-party HTTP loggers
    # to WARNING regardless of our verbose flag.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@click.group()
@click.option("--verbose", "-v", is_flag=True)
def cli(verbose: bool) -> None:
    """rug-detector: pre-launch rug-pull risk classifier."""
    _setup_logging(verbose)


@cli.command()
def init() -> None:
    """Create the DuckDB schema."""
    settings = get_settings()
    run_sql_file(settings.sql_dir / "schema.sql")
    console.print("[green]✓[/green] Schema initialized at", settings.db_path)


@cli.command()
@click.option("--version", type=click.Choice(["v2", "v3", "both"]), default="both")
def etl(version: str) -> None:
    """Pull pools, tokens, and events into the local DB."""
    from .etl import runner
    runner.init_schema()
    if version in ("v2", "both"):
        n = runner.load_pools("v2")
        console.print(f"Loaded {n} V2 pools")
    if version in ("v3", "both"):
        n = runner.load_pools("v3")
        console.print(f"Loaded {n} V3 pools")
    n = runner.load_tokens()
    console.print(f"Loaded {n} tokens")
    n = runner.load_pool_events()
    console.print(f"Loaded {n} pool events")
    n = runner.load_lp_transfers()
    console.print(f"Loaded {n} LP transfers")


@cli.command()
def label() -> None:
    """Apply the operational definition to produce labels."""
    stats = label_mod.build_labels()
    console.print(json.dumps(stats, indent=2, default=str))


@cli.command()
def features() -> None:
    """Build the feature table."""
    n = feat_mod.build_features()
    console.print(f"[green]✓[/green] Features built for {n} tokens")


@cli.command()
def train() -> None:
    """Train and evaluate the classifier(s)."""
    from . import model as model_mod
    metrics = model_mod.run_training()
    console.print_json(json.dumps(metrics, indent=2, default=str))


@cli.command(name="score")
@click.argument("address")
@click.option("--model", "model_path", type=click.Path(), default=None)
def score_cmd(address: str, model_path: str | None) -> None:
    """Score a single token address."""
    from pathlib import Path
    result = score_mod.score_token(address, model_path=Path(model_path) if model_path else None)

    console.print()
    console.print(f"[bold]Token:[/bold]      {result.token_address}")
    console.print(f"[bold]Risk score:[/bold] {result.risk_score:.3f}  (decile {result.decile}/10)")
    console.print()

    table = Table(title="Top contributing features", show_header=True, header_style="bold")
    table.add_column("Feature")
    table.add_column("Contribution", justify="right")
    for name, val in result.top_features:
        sign = "+" if val >= 0 else ""
        table.add_row(name, f"{sign}{val:.3f}")
    console.print(table)


@cli.command(name="pipeline")
@click.argument("action", type=click.Choice(["run-all"]))
def pipeline(action: str) -> None:
    """Run the full pipeline end-to-end."""
    if action == "run-all":
        from .etl import runner
        from . import model as model_mod

        console.rule("[bold]1/5 ETL[/bold]")
        runner.init_schema()
        runner.load_pools("v2")
        runner.load_pools("v3")
        runner.load_tokens()
        runner.load_pool_events()
        runner.load_lp_transfers()

        console.rule("[bold]2/5 Label[/bold]")
        console.print(label_mod.build_labels())

        console.rule("[bold]3/5 Features[/bold]")
        feat_mod.build_features()

        console.rule("[bold]4/5 Train[/bold]")
        metrics = model_mod.run_training()
        console.print_json(json.dumps(metrics, indent=2, default=str))

        console.rule("[bold]5/5 Done[/bold]")


if __name__ == "__main__":
    cli()
