"""Click CLI for pmetal-midi."""

from __future__ import annotations

import logging
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from . import __version__
from .config import AppConfig
from .merger import MidiMerger
from .orchestrator import MidiOrchestrator, ProcessingState, WorkflowConfig
from .quality_analyzer import QualityAnalyzer

console = Console()


def _setup_logging(verbose: bool, log_dir: Path | None = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "processing.log"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """Power Metal MIDI Hybridization System."""


@cli.command()
@click.argument("flat_midi", type=click.Path(exists=True, path_type=Path))
@click.argument("expressive_midi", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output directory")
@click.option("-a", "--audio", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("-c", "--config", "config_path", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("-t", "--tracks", multiple=True, type=int, help="Track indices to process")
@click.option("-v", "--verbose", is_flag=True)
@click.option("--no-retry", is_flag=True, help="Disable auto-retry")
def merge(
    flat_midi: Path,
    expressive_midi: Path,
    output: Path | None,
    audio: Path | None,
    config_path: Path | None,
    tracks: tuple[int, ...],
    verbose: bool,
    no_retry: bool,
) -> None:
    """Merge flat MIDI with expressive MIDI to create a hybrid file."""
    output_dir = output or flat_midi.parent / "output"
    _setup_logging(verbose, output_dir / "logs" if output else None)

    console.print(Panel.fit(
        f"[bold]Flat MIDI:[/bold] {flat_midi}\n"
        f"[bold]Expressive MIDI:[/bold] {expressive_midi}\n"
        f"[bold]Output:[/bold] {output_dir}",
        title="pmetal-midi merge",
    ))

    wf_config = WorkflowConfig(
        flat_midi_path=flat_midi,
        expressive_midi_path=expressive_midi,
        output_dir=output_dir,
        audio_path=audio,
        config_path=config_path,
        target_tracks=list(tracks) or None,
        auto_retry=not no_retry,
        verbose=verbose,
    )

    orchestrator = MidiOrchestrator(wf_config)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing...", total=None)

        def _on_state(state: ProcessingState, msg: str) -> None:
            progress.update(task, description=msg or state.name)

        orchestrator.on_state_change(_on_state)
        result = orchestrator.run()

    if result.state == ProcessingState.DONE:
        console.print(f"\n[green]Success![/green] Output: {result.output_path}")
        console.print(f"Quality score: {result.quality_score:.2f}")
        if result.processing_stats:
            console.print(f"Processing time: {result.processing_stats.total_duration:.1f}s")
        if result.suggestions:
            console.print("\n[yellow]Suggestions:[/yellow]")
            for s in result.suggestions:
                console.print(f"  - {s}")
    else:
        console.print(f"\n[red]Failed[/red]")
        for e in result.errors:
            console.print(f"  {e}")


@cli.command()
@click.argument("midi_file", type=click.Path(exists=True, path_type=Path))
@click.option("-v", "--verbose", is_flag=True)
def analyze(midi_file: Path, verbose: bool) -> None:
    """Analyze MIDI file quality."""
    _setup_logging(verbose)
    qa = QualityAnalyzer()
    report = qa.analyze(midi_file)

    table = Table(title=f"Quality Report: {midi_file.name}")
    table.add_column("Metric", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Status", justify="center")
    for metric, score in report.metrics.items():
        status = "[green]PASS[/green]" if score >= 0.70 else "[red]FAIL[/red]"
        table.add_row(metric, f"{score:.2f}", status)
    table.add_row("OVERALL", f"{report.overall_score:.2f}",
                   "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]",
                   style="bold")
    console.print(table)

    if report.suggestions:
        console.print("\n[yellow]Suggestions:[/yellow]")
        for s in report.suggestions:
            console.print(f"  - {s}")


@cli.command()
@click.argument("audio_file", type=click.Path(exists=True, path_type=Path))
@click.option("--tempo", type=float, default=None, help="Known tempo in BPM")
@click.option("-v", "--verbose", is_flag=True)
def extract(audio_file: Path, tempo: float | None, verbose: bool) -> None:
    """Extract audio features (transients, tempo, spectral peaks) from WAV/FLAC/MP3."""
    _setup_logging(verbose)
    from .analyzer import AudioAnalyzer

    console.print(f"Analysing: {audio_file}")
    analyzer = AudioAnalyzer()
    features = analyzer.analyze(audio_file, tempo_bpm=tempo)

    console.print(Panel.fit(
        f"[bold]Tempo:[/bold] {features.tempo_bpm:.1f} BPM\n"
        f"[bold]Duration:[/bold] {features.duration_seconds:.1f}s\n"
        f"[bold]Transients:[/bold] {len(features.transients)}\n"
        f"[bold]Spectral peaks:[/bold] {len(features.spectral_peaks)}\n"
        f"[bold]Beats:[/bold] {len(features.beat_times)}",
        title="Audio Features",
    ))

    if features.transients:
        table = Table(title="First 10 Transients")
        table.add_column("Time (s)", justify="right")
        table.add_column("Ticks", justify="right")
        table.add_column("Strength", justify="right")
        for t in features.transients[:10]:
            table.add_row(
                f"{t.time_seconds:.3f}",
                str(t.time_ticks),
                f"{t.strength:.2f}",
            )
        console.print(table)


@cli.command()
@click.argument("midi_file", type=click.Path(exists=True, path_type=Path))
def info(midi_file: Path) -> None:
    """Show MIDI file information."""
    import mido

    midi = mido.MidiFile(str(midi_file))
    console.print(Panel.fit(
        f"[bold]File:[/bold] {midi_file.name}\n"
        f"[bold]Type:[/bold] {midi.type}\n"
        f"[bold]Ticks/beat:[/bold] {midi.ticks_per_beat}\n"
        f"[bold]Tracks:[/bold] {len(midi.tracks)}",
        title="MIDI Info",
    ))

    table = Table(title="Tracks")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Notes", justify="right")
    table.add_column("Events", justify="right")

    from .utils import extract_notes, get_track_name
    for i, track in enumerate(midi.tracks):
        name = get_track_name(track)
        notes = extract_notes(track)
        table.add_row(str(i), name or "(unnamed)", str(len(notes)), str(len(track)))
    console.print(table)


@cli.group()
def config() -> None:
    """Configuration management commands."""


@config.command("show")
@click.option("-c", "--config-file", type=click.Path(exists=True, path_type=Path), default=None)
def config_show(config_file: Path | None) -> None:
    """Show current configuration."""
    if config_file:
        cfg = AppConfig.load(config_file)
    else:
        cfg = AppConfig.default()
    console.print(Panel(cfg.dump_yaml(), title="Configuration"))


@config.command("validate")
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
def config_validate(config_file: Path) -> None:
    """Validate a YAML configuration file."""
    try:
        cfg = AppConfig.load(config_file)
        console.print(f"[green]Valid configuration[/green]: {config_file}")
        console.print(f"  Version: {cfg.version}")
        console.print(f"  Tracks defined: {len(cfg.tracks)}")
        console.print(f"  Merger window: {cfg.merger.matching_window_ticks}")
        console.print(f"  Quality threshold: {cfg.quality.min_overall_score}")
    except Exception as e:
        console.print(f"[red]Invalid configuration[/red]: {e}")


if __name__ == "__main__":
    cli()
