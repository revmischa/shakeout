# Command-line tools

## One file

The default `single` target writes one `.py` that contains a small importer and every bundled
module:

```console
$ shakeout build tool.py -o dist
$ scp dist/tool.py server: && ssh server python3 tool.py --help
```

It needs only a Python interpreter and the standard library on the target machine. Code that
imports compiled third-party extensions can't live in a single file. Use `tree` or `pyz`, or
[`include`](dependencies.md) pure-Python packages so they are bundled.

## Zipapp

```console
$ shakeout build tool.py -t pyz -o dist
$ ./dist/tool.pyz --help
```

`pyz` writes a [zipapp](https://docs.python.org/3/library/zipapp.html) with a shebang. Sources
inside the archive are registered with `linecache`, so warnings and tracebacks show code lines,
which plain zipapps do not.

## Smaller still

`--minify` drops comments and blank lines but keeps every remaining line at its original line
number. `--strip-docstrings` also removes docstrings, like `python -OO`. Only use it when nothing
reads `__doc__`: argparse and click help texts often do.
