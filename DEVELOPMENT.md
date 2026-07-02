# Development Setup

## Running Tests Without __pycache__

The harness prevents `__pycache__` directories from polluting the source tree by using:

```bash
export PYTHONDONTWRITEBYTECODE=1
```

### Quick Setup

Source the dev environment before running tests:

```bash
source .env.dev
pytest tests/
```

Or use make (recommended):

```bash
make verify      # Run the full workflow-constitution suite
make test        # Alias for verify
make check-catalog  # Just schema/template catalog
```

### How It Works

1. **pytest.ini** — Configured with `-p no:cacheprovider` to disable pytest's cache plugin
2. **Makefile** — All `pytest` invocations set `PYTHONDONTWRITEBYTECODE=1`
3. **.env.dev** — Source this for local development to set both flags

### Why This Matters

- `__pycache__/` directories contain bytecode (`.pyc` files) Python generates for performance
- They appear *anywhere* your code is imported, making them hard to control
- Setting `PYTHONDONTWRITEBYTECODE=1` tells Python: "Don't write bytecode, use in-memory cache instead"
- This is safe for development; pytest runs just as fast, and the source tree stays clean

### .gitignore

`__pycache__/` is already in `.gitignore`, but preventing creation is cleaner than relying on gitignore.

### Running Without the .env.dev Setup

If you forget to source `.env.dev`, you'll see `__pycache__/` directories appear. Just clean them:

```bash
find . -type d -name __pycache__ -exec rm -rf {} +
```

But don't commit them — they're already gitignored.
