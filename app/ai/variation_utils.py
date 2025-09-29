import re, json, hashlib
FRACTION_RE = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")

def extract_used_fractions(items: list[dict]) -> list[tuple[int,int]]:
    out = []
    dump = json.dumps(items, ensure_ascii=False)
    for a,b in FRACTION_RE.findall(dump):
        out.append((int(a), int(b)))
    return out

def signature_of_numbers(items: list[dict]) -> str:
    nums = sorted(extract_used_fractions(items))
    raw = json.dumps(nums)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()