"""Merge a batch of translations into the project translation memory.

usage: 40_merge_tm.py <batch.tsv> [--config project.toml]

batch.tsv: one line per entry, "<index>\t<start of the key>\t<chinese>", index = position
in <review>/tm_todo.json (see 41_export_todo.py).  The second column is a guard against
shifted indices.  Write ∅ as the Chinese for "do not translate".
Every entry is validated (placeholders identical to the key, no bare digits).  Nothing is
written if any entry fails."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import config
from engine.tm import TM


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = config.load(a.config)
    todo = list(json.loads((cfg.review / "tm_todo.json").read_text(encoding="utf-8")))
    tm_path = cfg.tm
    tm = json.loads(tm_path.read_text(encoding="utf-8")) if tm_path.exists() else {}
    errors, new = [], {}
    for ln in Path(a.batch).read_text(encoding="utf-8-sig").splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) != 3:                       # index, start of the key (guard), chinese
            errors.append(f"line needs 3 tab-separated columns (use ∅ for 'do not translate'): {ln[:60]}")
            continue
        i, hint, zh = parts
        zh = "" if zh.strip() == "∅" else zh
        if not todo[int(i)].startswith(hint):
            errors.append(f"{i}: key does not start with '{hint}' (is: {todo[int(i)][:30]})")
            continue
        key = todo[int(i)]
        e = TM.check_entry(key, zh)
        if e:
            errors.append(f"{i}: {e} | {key[:60]} -> {zh[:60]}")
        new[key] = zh
    missing = [i for i, k in enumerate(todo) if k not in new and k not in tm]
    for e in errors:
        print("ERROR", e)
    if errors:
        sys.exit(1)
    tm.update(new)
    tm_path.parent.mkdir(parents=True, exist_ok=True)
    tm_path.write_text(json.dumps(tm, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"merged {len(new)}; still untranslated indices: {missing}")


if __name__ == "__main__":
    main()
