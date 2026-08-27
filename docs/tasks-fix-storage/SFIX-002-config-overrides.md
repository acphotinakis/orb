# SFIX-002: Layered Config Override System & Config Snapshot Persistence

## Objective

Refactor `src/common/config.py` and `src/main.py` to support a **layered configuration system** (CLI > YAML > dataclass defaults), add `AppConfig.to_yaml()` for experiment snapshot persistence, remove path fields from `DataConfig`, add `is_paper`, and fix the broken CLI argument registration in `main.py`.

## Problem

1. `load_config()` accepts only a YAML path — no runtime overrides possible
2. CLI overrides in `main.py` use brittle `dataclasses.replace()` chaining but several CLI arguments (`--symbol`, `--timeframe`, `--feed`, `--start-date`, `--end-date`, `--no-plots`, `--refresh-cache`, `--log-level`) are **used in `main()` but never registered in `parse_args()`** — causes `AttributeError` at runtime
3. `validate_config()` must be called manually after `load_config()` — easy to forget
4. `DataConfig` has `raw_dir`, `processed_dir`, `plots_dir` fields that conflict with `PathManager` ownership (SFIX-001)
5. No `AppConfig.to_yaml()` method — experiments cannot save their exact config snapshot
6. `validate_config()` logs the config inline — should be a method on `AppConfig`

## Scope

### In Scope
- Add `overrides: Optional[Dict[str, Any]]` parameter to `load_config()`
- Implement `_deep_update(target, source)` recursive dict merger
- Remove `raw_dir`, `processed_dir`, `plots_dir` from `DataConfig`
- Add `is_paper: bool = True` field to `DataConfig`
- Add `AppConfig.to_yaml(path)` and improve `AppConfig.to_json()`
- Call `validate_config()` inside `load_config()` automatically
- Register all missing CLI arguments in `main.py` `parse_args()`
- Add `_build_overrides_from_args(args)` helper in `main.py`

### Out of Scope
- Environment variable override tier (future task)
- Changing strategy/execution/filters config fields

## Implementation Details

### 1. `load_config()` — add overrides parameter

```python
def load_config(
    config_path: Union[str, Path] = "config/default_config.yaml",
    overrides: Optional[Dict[str, Any]] = None,
) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(...)

    with path.open("r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    if overrides:
        _deep_update(raw, overrides)

    config = AppConfig(
        schema_version=str(raw.get("schema_version", "1.0")),
        strategy=_build_strategy(raw.get("strategy", {})),
        filters=_build_filters(raw.get("filters", {})),
        data=_build_data(raw.get("data", {})),
        execution=_build_execution(raw.get("execution", {})),
        output=_build_output(raw.get("output", {})),
    )
    validate_config(config)  # Always validate after build — callers cannot forget
    return config
```

### 2. `_deep_update()` — recursive dict merge

```python
def _deep_update(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge source into target, skipping None values."""
    for k, v in source.items():
        if v is None:
            continue
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            _deep_update(target[k], v)
        else:
            target[k] = v
    return target
```

### 3. `DataConfig` — remove path fields, add `is_paper`

```python
@dataclass(frozen=True)
class DataConfig:
    symbol: str = "SPY"
    feed: str = "sip"
    timeframe: str = "1Min"
    timezone: str = "America/New_York"
    is_paper: bool = True       # NEW: paper vs live trading mode
    # REMOVED: raw_dir, processed_dir, plots_dir — owned by PathManager
```

### 4. `AppConfig` — add `to_yaml()` and improve `to_json()`

```python
def to_yaml(self, path: Union[str, Path]) -> None:
    """Write a frozen snapshot of this config to a YAML file."""
    import dataclasses as dc
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(dc.asdict(self), fh, default_flow_style=False, sort_keys=False)

def to_json(self, indent: int = 2) -> str:
    """Return a JSON string representation of this config."""
    import json, dataclasses as dc
    return json.dumps(dc.asdict(self), indent=indent, default=str)
```

### 5. `main.py` — register all missing CLI arguments

```python
def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(...)
    parser.add_argument("--config",         type=str, default="config/default_config.yaml")
    parser.add_argument("--run-id",         type=str, default="baseline_v1")
    parser.add_argument("--symbol", "-s",   type=str, default=None, help="Override ticker (e.g. AAPL)")
    parser.add_argument("--timeframe","-tf",type=str, default=None, help="Override bar timeframe (e.g. 5Min)")
    parser.add_argument("--feed",           type=str, choices=["iex", "sip"], default=None)
    parser.add_argument("--start-date",     type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date",       type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--refresh-cache",  action="store_true", default=False)
    parser.add_argument("--no-plots",       action="store_true", default=False)
    parser.add_argument("--paper",          action="store_true", default=True)
    parser.add_argument("--log-level",      type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(args)
```

### 6. `main.py` — consolidated override builder

```python
def _build_overrides_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    """Map parsed CLI args to a nested override dict for load_config()."""
    overrides: Dict[str, Any] = {}
    if args.symbol:
        sym = args.symbol.upper()
        overrides.setdefault("strategy", {})["ticker"] = sym
        overrides.setdefault("data", {})["symbol"] = sym
    if args.timeframe:
        overrides.setdefault("data", {})["timeframe"] = args.timeframe
    if args.feed:
        overrides.setdefault("data", {})["feed"] = args.feed
    return overrides

def main() -> int:
    args = parse_args()
    setup_logging(level=args.log_level)
    try:
        overrides = _build_overrides_from_args(args)
        cfg = load_config(args.config, overrides=overrides)  # validate called inside
        pipeline = ORBPipeline(config=cfg)
        pipeline.run(
            start_date=args.start_date,
            end_date=args.end_date,
            refresh_cache=args.refresh_cache,
            generate_plots=not args.no_plots,
            run_id=args.run_id,
        )
        return 0
    except ORBBaseException as err:
        logger.error("ORB Pipeline Error: %s", err, exc_info=True)
        return 1
```

## Affected Files

| File | Change Required |
|:-----|:----------------|
| `src/common/config.py` | Add `_deep_update()`, update `load_config()`, remove path fields from `DataConfig`, add `is_paper`, add `AppConfig.to_yaml()` / `to_json()` |
| `src/main.py` | Register all CLI args, add `_build_overrides_from_args()`, simplify `main()` |
| `config/default_config.yaml` | Remove `raw_dir`, `processed_dir` from `data:` section; add `is_paper: true` |

## Acceptance Criteria

- [ ] `load_config(path, overrides={"data": {"timeframe": "5Min"}})` overrides timeframe correctly
- [ ] `AppConfig.to_yaml(path)` writes a valid YAML file that `load_config()` can reload cleanly
- [ ] `python -m src.main --symbol NVDA --timeframe 5Min` works without `AttributeError`
- [ ] `validate_config()` is always called inside `load_config()` — manual call in `main.py` removed
- [ ] `DataConfig` no longer contains `raw_dir`, `processed_dir`, or `plots_dir`
- [ ] `DataConfig.is_paper` field exists and defaults to `True`

## Dependencies

- None — can be implemented independently; SFIX-001 depends on this
