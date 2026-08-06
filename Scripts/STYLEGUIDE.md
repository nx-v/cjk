# Scripts style guide

Conventions for Python under `Scripts/`, excluding `Scripts/kage/renderer/` (ported upstream code; leave its local style alone unless intentionally aligning a touched region).

## Prefer `match` trees for discriminants

Use `match` / `case` when branching on a shared discriminant (enum members, string tags, small integer codes, command letters, verb kinds, and similar), instead of a long `if` / `elif` chain:

```python
match verb:
    case pathops.PathVerb.MOVE: path.moveTo(*pts[0])
    case pathops.PathVerb.LINE: path.lineTo(*pts[0])
    case pathops.PathVerb.CLOSE: path.close()
    case _: raise ValueError(verb)
```

Combine or-patterns when arms share logic (`case "Z" | "z": ...`). Use a guarded case when needed (`case "matrix" if len(nums) >= 6:`). Keep a normal indented `case` body when that arm needs multiple statements.

Do **not** force `match` for unrelated early-exit guards (`if x is None: return`); those stay as inline `if`s.
