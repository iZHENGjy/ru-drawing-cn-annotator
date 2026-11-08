"""Layered translation memory for the scanned route.

    glossary (public, repo)  <  shared (private TMs of other volumes)  <  project (this run)

Lookup: exact fold of the key in any layer (project first), then fuzzy (letters nearly equal,
number signature identical — see segments.similar).  Keys carry ⟨n⟩ placeholders; values must
carry the same placeholders (engine/tm.py rules), so a number can never be altered by translation.

Project TM file (``[tm] project``, default ``tm/project.json`` next to project.toml)::

    {"_format": "rudraw-tm/1",
     "entries": {"<fold>": {"key": "Изделие закладное МН⟨1⟩", "zh": "预埋件 МН⟨1⟩",
                            "action": "translate|keep|skip", "src": "llm|tm|human|rule|oracle",
                            "model": "sonnet", "conf": "high|low", "page": 5, "n": 12}}}

Shared TMs may be in this format or a flat ``{key: zh}`` dict (engine/tm.py format).
``misses(segs)`` -> items for the LLM task ``phrase_translate``; ``ingest(results)`` stores answers.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from engine import tm as T
from scan import segments as S

REPO = Path(__file__).resolve().parents[2]
DEFAULT_GLOSSARY = REPO / "glossary" / "gost_general.json"
DEFAULT_GLOSSARIES = [DEFAULT_GLOSSARY, REPO / "glossary" / "spds_sheet_frame.json"]   # public base layers
MARK = re.compile(r"(^|[^а-яa-z])[бвпркмфс][мm](?=\s?⟨)")      # Км⟨1⟩, Бм (BM -> вм)
ABBR = re.compile(r"(^|[^а-я])(п\.м|м\.п|(см|ед|кг|отм|поз|прим|зам|изм|разд|табл|ч|л|т)\.)")   # п.м = 延米
CJK = re.compile(r"[一-鿿]")
PH = T.PH
SPEC = re.compile(r"гост|gost|ту\b|сп\b|снип|ост\b|din\b|en\b|iso\b|a\d{3}c?|а\d{3}с?|с\d{3}|l=|мм|mm|кг|kg|шт|pcs|sp\b"
                  r"|[a-zа-я]{1,2}\d", re.I)


@dataclass
class Hit:
    zh: str
    action: str          # translate | keep | skip
    layer: str           # project | shared | glossary
    src: str             # llm | tm | human | rule | oracle | glossary
    conf: str            # high | low
    fuzzy: bool
    key: str             # the key that matched (its placeholders define the numbers)
    bare: bool = False   # value carries digits that are not placeholders (never substituted)


def _abs(root: Path, p) -> Path:
    p = Path(str(p).replace("<repo>", str(REPO)))
    return p if p.is_absolute() else root / p


def _gloss_key(k: str) -> str:
    """Glossary keys are already normalised; numbers may still be plain digits."""
    if "⟨" not in k:
        k = T.make_key(S.norm(k))[0]
    return k


class TMStore:
    def __init__(self, cfg: dict | None = None, root: Path | None = None, names=(), skip_regex: str = ""):
        cfg = cfg or {}
        self.root = Path(root or Path.cwd())
        self.path = _abs(self.root, cfg.get("project", "tm/project.json"))
        gl = [_abs(self.root, g) for g in cfg.get("glossaries", [])]
        for i, dg in enumerate(g for g in DEFAULT_GLOSSARIES if g.exists()):
            if dg not in gl:
                gl.insert(i, dg)
        # layer -> {fold: entry}; contexts '@tb|' / '@body|' stored under ('tb'|'body', fold)
        self.layers: dict[str, dict] = {"glossary": {}, "shared": {}, "project": {}}
        self.ambiguous: set[str] = set()
        self._pending: dict[str, dict] = {}        # item id -> item (from the last misses())
        self._fz: dict[str, dict] = {}             # fuzzy index cache: layer -> sig -> [(skel, fold)]
        self._termc = None                         # term candidates cache
        for g in gl:
            self._load_flat(g, "glossary", "glossary")
        for sh in cfg.get("shared", []):
            self._load_any(_abs(self.root, sh), "shared")
        self._load_any(self.path, "project")
        self.names = [S.skeleton(n) for n in names if len(S.skeleton(n)) >= 4]
        self.skip = re.compile(skip_regex, re.I) if skip_regex else None

    # ------------------------------------------------------------ loading / saving
    def _put(self, layer, k, e, ctx=None):
        f = S.fold(k)
        self.layers[layer][(ctx, f) if ctx else f] = dict(e, key=k)
        self._fz.pop(layer, None)
        self._termc = None

    def _load_flat(self, path: Path, layer: str, src: str):
        if not path.exists():
            return
        d = T.load_json(path)
        amb = d.pop("@ambiguous", "")
        self.ambiguous |= {S.fold(_gloss_key(a)) for a in amb.split("‖") if a}
        for k, v in d.items():
            ctx = None
            m = re.match(r"^@(tb|body)\|(.*)$", k)
            if m:
                ctx, k = m.group(1), m.group(2)
            if not isinstance(v, str):
                continue
            self._put(layer, _gloss_key(k), dict(zh=v, action="translate" if v else "skip", src=src,
                                                 conf="high"), ctx)

    def _load_any(self, path: Path, layer: str):
        if not path.exists():
            return
        d = json.loads(path.read_text(encoding="utf-8"))
        if "entries" in d:
            for f, e in d["entries"].items():
                self.layers[layer][f] = dict(e)
            self._fz.pop(layer, None); self._termc = None
        else:
            self._load_flat(path, layer, "tm")

    def save(self, path: Path | None = None):
        p = Path(path or self.path); p.parent.mkdir(parents=True, exist_ok=True)
        ent = {f: e for f, e in sorted(self.layers["project"].items()) if isinstance(f, str)}
        p.write_text(json.dumps({"_format": "rudraw-tm/1", "entries": ent}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
        return p

    def add(self, key: str, zh: str, action: str = "translate", src: str = "human", conf: str = "high",
            page=None, model: str = "", n: int = 1, extra: dict | None = None):
        e = dict(zh=zh if action == "translate" else "", action=action, src=src, conf=conf, n=n,
                 ts=time.strftime("%Y-%m-%d"))
        if model:
            e["model"] = model
        if page is not None:
            e["page"] = page
        if extra:
            e.update(extra)
        old = self.layers["project"].get(S.fold(key))
        if old and old.get("src") == "human" and src != "human":
            return old                              # a human decision is never overwritten
        self._put("project", key, e)
        return e

    # ------------------------------------------------------------ lookup
    def _fuzzy_index(self, layer):
        if layer not in self._fz:
            idx: dict[str, list] = {}
            for f, e in self.layers[layer].items():
                if isinstance(f, tuple):
                    continue
                k = e["key"]
                idx.setdefault(S.sig(k), []).append((S.skeleton(k), f))
            self._fz[layer] = idx
        return self._fz[layer]

    def _hit(self, layer, e, fuzzy):
        zh = e.get("zh") or ""
        bare = bool(zh) and bool(T.NUM.search(re.sub(r"\d{1,2}月", "", PH.sub("", zh))))
        return Hit(zh=zh, action=e.get("action", "translate" if zh else "skip"), layer=layer,
                   src=e.get("src", layer), conf=e.get("conf", "high"), fuzzy=fuzzy, key=e["key"], bare=bare)

    def lookup(self, key: str, ctx: str = "body", fuzzy: bool = True) -> Hit | None:
        f = S.fold(key)
        for layer in ("project", "shared", "glossary"):
            L = self.layers[layer]
            e = L.get((ctx, f))
            if e is None and f in self.ambiguous and layer == "glossary":
                continue
            amb = e is None and f in self.ambiguous
            e = e or L.get(f)
            if e is not None:
                h = self._hit(layer, e, False)
                if amb or (f in self.ambiguous and layer != "glossary"):
                    h.conf = "low"                      # context-dependent term (Лист: 页次 / 钢板)
                return h
        if not fuzzy:
            return None
        sk, sg = S.skeleton(key), S.sig(key)
        if len(sk) < 5:
            return None
        for layer in ("project", "shared", "glossary"):
            for sk2, f2 in self._fuzzy_index(layer).get(sg, []):
                if S.similar(sk, sk2):
                    return self._hit(layer, self.layers[layer][f2], True)
        return None

    # ------------------------------------------------------------ rules (no LLM)
    def rule(self, key: str, sample: str = "") -> str | None:
        """Decisions code can make: names / project skip rule -> keep; specification strings
        (ГОСТ ..., Ø12 А500С L=1200, sheet codes) -> keep."""
        sk = S.skeleton(key)
        for n in self.names:
            i = name_at(n, sk)
            if i is not None and len(sk) - len(n) < 3:     # the text is (mostly) the name
                return "keep"
        if self.skip and (self.skip.search(sample or key) or self.skip.search(key)):
            return "skip"
        f = S.fold(key)
        if MARK.search(f) or ABBR.search(f):
            return None                               # member marks (柱 Км⟨1⟩), abbreviations (п.м.)
        if not re.findall(r"[а-я]{3,}", SPEC.sub(" ", f)):
            return "keep"
        return None

    # ------------------------------------------------------------ LLM hand-off
    def misses(self, segs: "S.Segments", rules: bool = True) -> list[dict]:
        """Needed segments without a TM hit -> phrase_translate items
        {id, key, sample, ctx:[above, below], n} (+ '_terms' used by batches())."""
        items = []
        for g in segs.segs:
            if not g.get("need", True):
                continue
            if any(self.lookup(v["key"]) for v in g["variants"]):
                continue
            if rules:
                r = self.rule(g["key"], g["sample"])
                if r:
                    self.add(g["key"], "", action=r, src="rule", page=g["occ"][0]["pg"] if g["occ"] else None, n=g["n"])
                    continue
            o = next((o for o in g["occ"] if o["src"] == "tess"), g["occ"][0] if g["occ"] else {})
            it = dict(id=g["id"], key=g["key"], sample=g["sample"], ctx=[o.get("above", ""), o.get("below", "")],
                      n=g["n"])
            it["_terms"] = self.terms(g["key"])
            it["_page"] = o.get("pg")
            items.append(it)
            self._pending[it["id"]] = it
        return items

    def terms(self, key: str, limit: int = 6) -> list[dict]:
        """Known translations of words / short phrases inside ``key`` (keeps terms consistent)."""
        f = S.fold(key)
        if self._termc is None:
            self._termc = [(layer, f2, e) for layer in ("project", "shared", "glossary")
                           for f2, e in self.layers[layer].items()
                           if not isinstance(f2, tuple) and e.get("zh") and e.get("action", "translate") == "translate"
                           and len(re.findall(r"[а-я]", f2)) >= 4 and f2.count(" ") <= 3]
        out, seen = [], set()
        for layer, f2, e in self._termc:
                if f2 in seen or f2 == f or f2 not in f:
                    continue
                if re.search(r"(^|[ /(\"])" + re.escape(f2) + r"($|[ /,.;:)\"])", f):
                    out.append(dict(ru=e["key"], zh=e["zh"])); seen.add(f2)
                    if len(out) >= limit:
                        return out
        return out

    def batches(self, items: list[dict], size: int = 60, background: str = ""):
        """-> (payloads, ids) for llm.Session.run('phrase_translate', payloads, ids)."""
        payloads, ids = [], []
        for i in range(0, len(items), size):
            chunk = items[i:i + size]
            terms, seen = [], set()
            for it in chunk:
                for t in it.get("_terms", []):
                    if t["ru"] not in seen:
                        seen.add(t["ru"]); terms.append(t)
            data = dict(items=[{k: v for k, v in it.items() if not k.startswith("_")} for it in chunk],
                        terms=terms[:120])
            if background:
                data["background"] = background
            payloads.append(dict(data=data, images=[]))
            ids.append(f"tr{i // size + 1:03d}")
        return payloads, ids

    def ingest(self, results, model: str = "", items: list[dict] | None = None) -> list[str]:
        """Store phrase_translate answers.  ``results``: llm Results (``.output['items']``), outputs
        (dicts with 'items') or a flat list of {id, action, zh, conf}.  Invalid answers are not
        stored; their errors are returned."""
        pend = dict(self._pending)
        for it in items or []:
            pend[it["id"]] = it
        flat = []
        for r in results:
            out = getattr(r, "output", r)
            if getattr(r, "ok", True) is False or out is None:
                continue
            if isinstance(out, dict) and "items" in out:
                flat += out["items"]
            elif isinstance(out, dict):
                flat.append(out)
        errs = []
        for a in flat:
            it = pend.get(a.get("id"))
            if it is None:
                errs.append(f"{a.get('id')}: unknown id"); continue
            act = a.get("action", "translate")
            zh = (a.get("zh") or "").strip()
            if act not in ("translate", "keep", "skip"):
                errs.append(f"{a['id']}: bad action {act}"); continue
            if act == "translate":
                e = validate(it["key"], zh)
                if e:
                    errs.append(f"{a['id']}: {e}"); continue
            self.add(it["key"], zh, action=act, src="llm", conf=a.get("conf", "high"), page=it.get("_page"),
                     model=model, n=it.get("n", 1))
            self._pending.pop(a["id"], None)
        return errs


def name_at(n: str, sk: str):
    """Position of name skeleton ``n`` inside text skeleton ``sk`` allowing one OCR letter error
    for names of ≥5 letters (Бописова for Борисова), else None."""
    L = len(n)
    for i in range(0, len(sk) - L + 1):
        w = sk[i:i + L]
        if sum(a != b for a, b in zip(w, n)) <= (1 if L >= 5 else 0):
            return i
    return None


def validate(key: str, zh: str) -> str | None:
    """Same rules as the LLM validator: placeholders, no bare digits, no stray Cyrillic."""
    if not zh:
        return "empty zh for action translate"
    e = T.TM.check_entry(key, zh)
    if e:
        return e
    cyr_words = re.findall(r"[А-Яа-яЁё][А-Яа-яЁё.\-]*", PH.sub(" ", zh))
    kf = S.fold(key)
    bad = [w for w in cyr_words if S.fold(w) not in kf]
    if bad:
        return f"Cyrillic not copied from the key: {' '.join(bad[:3])}"
    if not CJK.search(zh):
        return "no Chinese in zh"
    return None
