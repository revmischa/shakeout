# Pragmas

Comments on the first line of a top-level statement (or of its decorators).

| Pragma | Effect |
|---|---|
| `# shakeout: keep` | Always keep this statement when its module is bundled, and treat it as a side effect, so the module is bundled whenever it is imported |
| `# shakeout: pure` | Treat this statement as side-effect free, so it is removable when unused, even if it calls unknown functions |

```python
@app.route("/health")  # registration decorators are kept automatically
def health(): ...


def on_load():  # shakeout: keep
    ...


SETTINGS = load_settings()  # shakeout: pure
```

`__future__` imports, module docstrings and main guards ignore `pure`.
