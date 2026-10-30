# Installation

shakeout is a command-line tool with no runtime dependencies. It needs CPython 3.10 or newer.

=== "uvx (no install)"

    ```console
    $ uvx shakeout --version
    ```

=== "uv tool"

    ```console
    $ uv tool install shakeout
    $ shakeout --version
    ```

=== "pipx"

    ```console
    $ pipx install shakeout
    ```

!!! tip "Build with the interpreter you deploy on"
    shakeout locates the standard library and third-party packages the way the running
    interpreter would. To target a specific version, run it with that interpreter, for example
    `uvx --python 3.13 shakeout ...`. For third-party dependencies, `--uv` installs the locked
    versions for a chosen `--python-version` and `--platform`. See
    [Dependencies and uv](../guides/dependencies.md).

## uv integration

uv has no plugin interface, so shakeout runs as a separate tool:

- it reads its settings from `[tool.shakeout]` in the same `pyproject.toml` uv uses;
- it uses uv to install the dependencies pinned in `uv.lock` when you pass `--uv`.
