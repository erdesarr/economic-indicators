"""Interactive live validation (``make validate``).

Prints the approval table and asks the owner y/n per source and globally.
On approval it recalibrates plausible_range, stores golden fixtures and
appends docs/APPROVALS.md. Never writes data to the production database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.prompt import Confirm  # noqa: E402

from etl.config import load_config  # noqa: E402
from etl.pipeline import local_run_date  # noqa: E402
from etl.validate import (  # noqa: E402
    build_table,
    collect_live,
    console,
    previous_values_from_db,
    raw_fragment,
    record_approval,
    save_fixtures,
    update_ranges,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate live indicators with the owner")
    parser.add_argument("--config", default="config/indicators.yaml")
    parser.add_argument("--print-only", action="store_true", help="print table and exit")
    parser.add_argument("--yes", action="store_true", help="approve non-interactively")
    args = parser.parse_args()

    out = console()
    config = load_config(args.config)
    out.print("[bold]Sondeando las fuentes en vivo…[/bold]")
    results, recorder = collect_live(config)
    run_date = local_run_date(config.settings)
    previous = previous_values_from_db(config, run_date)
    out.print(build_table(config, results, previous))

    failures = [i for i in config.indicators if not (results.get(i.slug) and results[i.slug].ok)]
    if failures:
        out.print("[red]Indicadores con error:[/red] " + ", ".join(i.slug for i in failures))
        for indicator in failures:
            out.print(f"\n[bold]Fragmento crudo de {indicator.slug}[/bold] ({indicator.url}):\n")
            out.print(
                raw_fragment(indicator.source, indicator.url, indicator.visible_label, recorder)
            )

    if args.print_only:
        return 0 if not failures else 1

    if not args.yes:
        for source_name in config.by_source():
            if not Confirm.ask(f"¿Los datos de la fuente '{source_name}' están correctos? (y/n)"):
                out.print(
                    "[yellow]Revisa los fragmentos crudos de arriba. Propón un nuevo "
                    "selector/estrategia y repite `make validate`.[/yellow]"
                )
                return 1
        if not Confirm.ask("¿Estos datos están correctos? (y/n)"):
            out.print("[yellow]Validación rechazada; no se escribió nada.[/yellow]")
            return 1

    if failures:
        out.print(
            "[red]No se puede aprobar: hay indicadores con error. "
            "Corrige y repite `make validate`.[/red]"
        )
        return 1

    config_path = Path(args.config)
    update_ranges(config_path, config, results)
    written = save_fixtures(config, results, recorder)
    record_approval(config_path, config, results, written)
    out.print(
        "[green]Aprobación registrada: plausible_range actualizado, fixtures golden "
        "guardados y docs/APPROVALS.md actualizado.[/green]"
    )
    for path in written:
        out.print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
