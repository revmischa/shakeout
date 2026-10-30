import click
from click.testing import CliRunner


@click.group()
def cli():
    """Tool."""


@cli.command()
@click.option("--count", default=2, type=int)
@click.argument("name")
def hello(count, name):
    for _ in range(count):
        click.echo(f"hi {name}")


result = CliRunner().invoke(cli, ["hello", "--count", "3", "bob"])
print(result.exit_code, result.output, end="")
print(CliRunner().invoke(cli, ["--help"]).output.splitlines()[0])
print(CliRunner().invoke(cli, ["hello"]).exit_code)
