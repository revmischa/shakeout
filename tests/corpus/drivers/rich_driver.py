from rich.console import Console
from rich.table import Table

console = Console(width=60, color_system=None, force_terminal=False, record=True)
table = Table(title="Planets")
table.add_column("Name")
table.add_column("Moons", justify="right")
table.add_row("Earth", "1")
table.add_row("Mars", "2")
console.print(table)
console.print("[bold]bold[/bold] and [italic]italic[/italic]")
print(len(console.export_text()) > 0)
