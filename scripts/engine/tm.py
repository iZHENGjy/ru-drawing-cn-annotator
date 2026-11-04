"""Translation memory with number placeholders.

Key   = normalised Russian text, every number replaced by ⟨1⟩, ⟨2⟩ ...
Value = Chinese text that must contain exactly the same placeholders.
At render time the original numbers are substituted back, so numbers can never
be altered by translation.  Decimal commas become points (except inside codes
such as 2,5-К-1).
"""
from __future__ import annotations
import json, re
from pathlib import Path

NUM = re.compile(r"\d+(?:[.,]\d+)*")
PH = re.compile(r"⟨(\d+)⟩")


def join_lines(text: str) -> str:
    text = re.sub(r"[-]", "", text)        # GOST-font private symbols (weld leg mark)
    lines = [l.strip() for l in text.split("\n")]
    out = ""
    for l in lines:
        if not out:
            out = l
        elif re.search(r"[а-яa-z]-$", out) and re.match(r"^[а-яa-z]", l):   # Приме- / чание
            out = out[:-1] + l
        else:
            out += " " + l
    return re.sub(r"\s+", " ", out).strip()


def split_affixes(s: str):
    """Strip bullet prefix and trailing separators; return (prefix, core, suffix)."""
    pre = ""
    m = re.match(r"^([-–—•]\s*)", s)
    if m:
        pre, s = "- ", s[m.end():]
    suf = ""
    m = re.search(r"\s*([/;:,]+)\s*$", s)
    if m:
        suf, s = m.group(1), s[:m.start()]
    return pre, s.strip(), suf


def make_key(core: str):
    nums = NUM.findall(core)
    i = 0
    def rep(m):
        nonlocal i
        i += 1
        return f"⟨{i}⟩"
    return NUM.sub(rep, core), nums


def fmt_num(n: str, after: str) -> str:
    """decimal comma -> point, except in codes like 2,5-К-1 (comma number followed by -letter)"""
    if re.fullmatch(r"\d+,\d+", n) and not re.match(r"^-[A-Za-zА-Яа-яЁё]", after):
        return n.replace(",", ".")
    return n


def load_json(path: Path) -> dict[str, str]:
    path = Path(path)
    d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {k: v for k, v in d.items() if not k.startswith("_")}      # "_comment" keys


class TM:
    """Translation memory.  ``path`` is the project TM (written by 40_merge_tm.py);
    ``base`` are read-only glossaries underneath it (project entries win)."""
    def __init__(self, path: Path, base: list[Path] = ()):
        self.path = Path(path)
        self.data: dict[str, str] = {}
        amb = []
        for p in [*base, self.path]:
            d = load_json(p)
            amb.append(d.pop("@ambiguous", ""))
            self.data.update(d)
        self.ambiguous = set(a for s in amb for a in s.split("‖") if a)
        self.missing: dict[str, str] = {}
        self.missing_ctx: dict[str, set] = {}
        self.page = 0

    def translate(self, ru_text: str, ctx: str = "body"):
        """Return (chinese or None, key).
        Context-specific entries '@tb|key' / '@body|key' win over the plain key.
        Keys listed in '@ambiguous' MUST have a context-specific entry."""
        s = join_lines(ru_text)
        pre, core, suf = split_affixes(s)
        key, nums = make_key(core)
        ckey = f"@{ctx}|{key}"
        if ckey in self.data:
            val = self.data[ckey]
        elif key in self.ambiguous:
            val = None
            key = ckey
        else:
            val = self.data.get(key)
        if val is None:
            self.missing.setdefault(key, core)
            self.missing_ctx.setdefault(key, set()).add(self.page)
            return None, key
        if val == "":
            return "", key
        # substitute numbers
        def rep(m):
            k = int(m.group(1)) - 1
            after = ""
            pos = key.find(f"⟨{k + 1}⟩")
            if pos >= 0:
                after = key[pos + len(f"⟨{k + 1}⟩"):pos + len(f"⟨{k + 1}⟩") + 2]
            return fmt_num(nums[k], after)
        out = PH.sub(rep, val)
        # frost resistance F₁200 is extracted as 'F1200' / 'F1 200'
        if re.search(r"F\s?1\s?200", core) or re.search(r"F₁", core):
            out = re.sub(r"F1 ?(\d{3})\b", r"F₁\1", out)
        sfx = {";": "；", ":": "：", "/": "", ",": "，"}.get(suf[-1:] if suf else "", "")
        return (pre + out + sfx), key

    def translate_lines(self, lines: list[str], ctx: str = "body"):
        """Split a multi-line run into numbered clauses / bullet items and translate each.
        Returns (chinese joined by newlines or None if any clause is missing, keys)."""
        clauses, cur = [], []
        for l in lines:
            l = l.strip()
            starts = re.match(r"^(\d{1,2}(\.\d{1,2})*\.?\s+[А-ЯЁA-Z«\"]|-\s)", l) is not None
            if starts and cur:
                clauses.append(cur); cur = []
            cur.append(l)
        if cur:
            clauses.append(cur)
        outs, keys, ok = [], [], True
        for c in clauses:
            text = "\n".join(c)
            m = re.match(r"^(\d{1,2}(?:\.\d{1,2})*\.?)\s+(?=[А-ЯЁA-Z«\"])", text)
            num = ""
            if m and (len(clauses) > 1 or "." in m.group(1) or True):
                num, text = m.group(1) + " ", text[m.end():]
            zh, key = self.translate(text, ctx)
            keys.append(key)
            if zh is None:
                ok = False; continue
            outs.append(num + zh)
        return ("\n".join(o for o in outs if o) if ok else None), keys

    @staticmethod
    def check_entry(key: str, val: str) -> str | None:
        if key.startswith("@ambiguous") or val == "":
            return None
        a = sorted(PH.findall(key)); b = sorted(PH.findall(val))
        if a != b:
            return f"placeholder mismatch {a} vs {b}"
        # month names (декабря -> 12月) are the only digits allowed that the key does not carry
        if NUM.search(re.sub(r"\d{1,2}(—\d{1,2})?月", "", PH.sub("", val))):
            return "bare digits in value (use placeholders)"
        return None
