# Debugging tracebacks

JavaScript bundles need source maps because many files become one file with new line numbers.
Python tracebacks don't read the file being run. They come from the code objects, which carry
their own file name and line positions. shakeout compiles every module under its original
project-relative file name, with removed code replaced by padding, so line numbers never move.

```text
Traceback (most recent call last):
  File "main.py", line 3, in <module>
    fail()
    ~~~~^^
  File "lib.py", line 8, in fail
    raise RuntimeError("boom")
RuntimeError: boom
```

That is a traceback from a single-file bundle, and it is identical to the unbundled program's.
The test suite checks this for every case, every target and every supported Python version.

## Source lines where the files don't exist

In a single-file bundle or a zipapp the original `.py` files aren't on disk. The bundle
registers each module's source with `linecache`, which is what `traceback`, `logging`,
`warnings`, `inspect.getsource`, the Lambda runtime and error trackers like Sentry read.

On Python 3.12 and earlier, the default exception printer is written in C and reads files from
disk directly. Bundles install a `sys.excepthook` that prints through `traceback` instead, but only
if your program hasn't installed its own.

## Minified bundles and `shakeout trace`

`--minify` strips comments, so source lines in tracebacks lose them. File names and line numbers
are unchanged. To see the original lines, feed the traceback to `shakeout trace` together with
the build manifest and the source tree:

```console
$ shakeout trace error.txt --manifest dist/main.manifest.json --root .
```

The same works for bundles built with `--no-embed-source`, and for logs that dropped source lines.

## Reaching removed code

If code reaches a removed name through a path analysis couldn't see, the exception carries a note:

```text
AttributeError: module 'lib' has no attribute 'hidden': removed by shakeout (unreferenced;
defined at lib.py:5). Keep it with: keep = ['lib:hidden']
```

`NameError`, `ImportError` and `ModuleNotFoundError` get similar notes. The exception type never
changes, so `except ImportError:` fallbacks keep working.
