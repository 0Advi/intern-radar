#!/usr/bin/env python3
"""Tier-1 model client: cheap / free OpenAI-compatible APIs for bulk work on PUBLIC text.

Providers are tried in the order of tools/llm_config.json (DeepSeek → Gemini → Cerebras → OpenRouter);
a provider without a key, over its daily cap, or failing is skipped. Claude stays the expert tier: this
client never receives Aditya's profile, resume or emails (see `public_text_only`).

Use from Python:
    from llm import ask_json
    out = ask_json(system, user_text, schema={"role": str, "stipend": (str, type(None))}, task="extract")
    # -> dict that matches the schema, plus "_provider" / "_model"; raises LLMError if every provider fails

Command line:
    python3 tools/llm.py --check                 # which keys are set; one tiny test call per provider
    python3 tools/llm.py --set-key deepseek      # stores a key in the macOS Keychain (typed, never echoed)
    python3 tools/llm.py --models deepseek       # list model ids the provider offers (to pin in the config)
    python3 tools/llm.py --usage                 # today's Tier-1 calls and tokens per provider
    python3 tools/llm.py --selftest              # offline test of fallback, validation, cache, privacy guard

Keys are looked up in this order: environment variable (DEEPSEEK_API_KEY …), the project's `.env` file
(git-ignored, chmod 600), then the macOS Keychain item `internradar-<provider>`. Never paste keys in chat.
"""
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((Path(__file__).resolve().parent / "llm_config.json").read_text())
DATA = ROOT / "data"
CACHE = DATA / "llm_cache"
COUNTS = DATA / "llm_counts.json"
USAGE = DATA / "usage.jsonl"

# Words that mean the text is about Aditya rather than a public posting. If any appear, refuse to send.
PRIVATE_MARKERS = [r"Aditya Kumar Singh", r"github\.com/0Advi", r"RetailExpansion", r"BioVeritas",
                   r"Faultline", r"SkyWay"]


def _personal_markers():
    """Email addresses and phone numbers from the git-ignored profile files, so they never live in code."""
    out = []
    for name in ("profile.md", "resume/base.json"):
        try:
            text = (ROOT / name).read_text()
        except OSError:
            continue
        for email in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
            out.append(re.escape(email.split("@")[0]))
        for phone in re.findall(r"\+?\d[\d ]{8,}\d", text):
            digits = re.sub(r"\D", "", phone)[-10:]
            if len(digits) == 10:
                out.append(r"\s?".join([digits[:5], digits[5:]]))
    return out


PRIVATE_MARKERS += [m for m in dict.fromkeys(_personal_markers()) if m not in PRIVATE_MARKERS]


class LLMError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------ keys

def _dotenv():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_key(name):
    var = f"{name.upper()}_API_KEY"
    if os.environ.get(var):
        return os.environ[var]
    env = _dotenv()
    if env.get(var):
        return env[var]
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", f"internradar-{name}", "-w"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def set_key(name):
    key = getpass.getpass(f"Paste the {name} API key (not shown): ").strip()
    if not key:
        print("nothing stored")
        return 1
    subprocess.run(["security", "add-generic-password", "-U", "-a", os.environ.get("USER", "radar"),
                    "-s", f"internradar-{name}", "-w", key], check=True)
    print(f"stored in Keychain as internradar-{name}")
    return 0


# ------------------------------------------------------------------------------- bookkeeping

def _today():
    return dt.date.today().isoformat()


def _counts():
    try:
        c = json.loads(COUNTS.read_text())
    except (OSError, ValueError):
        c = {}
    if c.get("date") != _today():
        c = {"date": _today(), "requests": {}, "tokens": {}}
    return c


def _bump(provider, tokens):
    c = _counts()
    c["requests"][provider] = c["requests"].get(provider, 0) + 1
    c["tokens"][provider] = c["tokens"].get(provider, 0) + tokens
    DATA.mkdir(exist_ok=True)
    tmp = COUNTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(c))
    tmp.replace(COUNTS)


def _log(entry):
    DATA.mkdir(exist_ok=True)
    with open(USAGE, "a") as f:
        f.write(json.dumps({"time": dt.datetime.now().isoformat(timespec="minutes"), "tier": 1, **entry}) + "\n")


_last_call = {}


def _pace(provider, rpm):
    gap = 60.0 / max(rpm, 1)
    wait = _last_call.get(provider, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call[provider] = time.time()


# ----------------------------------------------------------------------------- validation

def public_text_only(*texts):
    """Raise if any text looks like it contains Aditya's own details (Tier 1 gets public text only)."""
    blob = "\n".join(t for t in texts if t)
    for pat in PRIVATE_MARKERS:
        if re.search(pat, blob, re.I):
            raise LLMError(f"refused: text contains personal details ({pat}); keep this for Claude")


def check_schema(obj, schema, path=""):
    """schema: {key: type | tuple of types | [type] | nested dict}. Returns a list of problems."""
    problems = []
    if not isinstance(obj, dict):
        return [f"{path or 'output'} is not an object"]
    for key, want in schema.items():
        if key not in obj:
            problems.append(f"missing {path}{key}")
            continue
        val = obj[key]
        if isinstance(want, dict):
            problems += check_schema(val, want, f"{path}{key}.")
        elif isinstance(want, list):
            if not isinstance(val, list):
                problems.append(f"{path}{key} should be a list")
            elif want and isinstance(want[0], dict):
                for i, item in enumerate(val):
                    problems += check_schema(item, want[0], f"{path}{key}[{i}].")
            elif want and not all(isinstance(v, want[0]) for v in val):
                problems.append(f"{path}{key} has items of the wrong type")
        elif not isinstance(val, want):
            problems.append(f"{path}{key} has type {type(val).__name__}")
    return problems


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def quote_ok(quote, source):
    """True if `quote` appears in `source` (whitespace and case ignored). Used to drop invented evidence."""
    q = _norm(quote)
    return bool(q) and len(q) >= 4 and q in _norm(source)


def parse_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


# -------------------------------------------------------------------------------- transport

def _http(provider, key, messages, json_mode):
    body = {"model": provider["model"], "messages": messages, "temperature": 0}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        provider["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "intern-radar/1.0"})
    with urllib.request.urlopen(req, timeout=CONFIG.get("timeout_s", 60)) as r:
        d = json.load(r)
    text = d["choices"][0]["message"]["content"]
    u = d.get("usage") or {}
    return text, int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))


TRANSPORT = _http  # replaced in the self-test


def ask_json(system, user_text, schema=None, task="tier1", providers=None, cache=True):
    """Ask Tier 1 for a JSON object. Validates against `schema`, retries once per provider on bad output,
    then falls through to the next provider. Raises LLMError when every provider fails."""
    public_text_only(system, user_text)
    limit = CONFIG.get("max_input_chars", 24000)
    if len(user_text) > limit:
        user_text = user_text[:limit] + "\n[truncated]"
    key_src = json.dumps([system, user_text, schema and sorted(schema)], ensure_ascii=False)
    h = hashlib.sha256(key_src.encode()).hexdigest()[:24]
    cpath = CACHE / f"{h}.json"
    if cache and cpath.exists():
        try:
            return json.loads(cpath.read_text())
        except ValueError:
            pass

    errors = []
    counts = _counts()
    for p in providers or CONFIG["providers"]:
        name = p["name"]
        key = get_key(name)
        if not key:
            errors.append(f"{name}: no key")
            continue
        if counts["requests"].get(name, 0) >= p.get("daily_requests", 10 ** 9):
            errors.append(f"{name}: daily cap reached")
            continue
        messages = [{"role": "system", "content": system + "\nReply with one JSON object only."},
                    {"role": "user", "content": user_text}]
        for attempt in range(2):
            try:
                _pace(name, p.get("rpm", 30))
                text, tin, tout = TRANSPORT(p, key, messages, p.get("json_mode", True))
                _bump(name, tin + tout)
                _log({"task": task, "provider": name, "model": p["model"], "in": tin, "out": tout})
                obj = parse_json(text)
                bad = check_schema(obj, schema) if schema else []
                if bad:
                    raise ValueError("schema: " + "; ".join(bad[:5]))
                obj["_provider"], obj["_model"] = name, p["model"]
                if cache:
                    CACHE.mkdir(parents=True, exist_ok=True)
                    cpath.write_text(json.dumps(obj, ensure_ascii=False))
                return obj
            except urllib.error.HTTPError as e:
                errors.append(f"{name}: HTTP {e.code}")
                if e.code in (401, 403, 404):
                    break                      # bad key or model id: next provider
                time.sleep(3 * (attempt + 1))  # 429 / 5xx: back off once, then next provider
            except (ValueError, KeyError, IndexError) as e:
                errors.append(f"{name}: bad output ({str(e)[:80]})")
                messages.append({"role": "user", "content": "That was not valid. Return only the JSON object "
                                 "with exactly the requested keys."})
            except (urllib.error.URLError, OSError) as e:
                errors.append(f"{name}: {type(e).__name__}")
                break
    _log({"task": task, "provider": "none", "error": "; ".join(errors)[:300]})
    raise LLMError("all Tier-1 providers failed: " + "; ".join(errors))


# ------------------------------------------------------------------------------------- CLI

def check():
    print(f"{'provider':<11} {'key':<5} result")
    for p in CONFIG["providers"]:
        key = get_key(p["name"])
        if not key:
            print(f"{p['name']:<11} {'no':<5} add with: python3 tools/llm.py --set-key {p['name']}")
            continue
        try:
            obj = ask_json("You are a test.", 'Return {"ok": true} for this posting text: "Intern, Bengaluru".',
                           schema={"ok": bool}, task="check", providers=[p], cache=False)
            print(f"{p['name']:<11} {'yes':<5} OK ({p['model']})")
        except LLMError as e:
            print(f"{p['name']:<11} {'yes':<5} FAILED: {str(e)[:150]}")


def models(name):
    p = next((x for x in CONFIG["providers"] if x["name"] == name), None)
    key = p and get_key(name)
    if not key:
        print("unknown provider or no key")
        return 1
    req = urllib.request.Request(p["base_url"].rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    for m in d.get("data", []):
        print(m.get("id"))
    return 0


def usage():
    c = _counts()
    print(f"Tier-1 usage on {c['date']}:")
    for name in sorted(set(c["requests"]) | set(c["tokens"])):
        print(f"  {name:<11} {c['requests'].get(name, 0):>5} requests  {c['tokens'].get(name, 0):>9} tokens")
    if not c["requests"]:
        print("  none yet")


def selftest():
    global TRANSPORT, CACHE, COUNTS, USAGE
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    CACHE, COUNTS, USAGE = tmp / "cache", tmp / "counts.json", tmp / "usage.jsonl"
    calls = []

    def fake(p, key, messages, json_mode):
        calls.append(p["name"])
        if p["name"] == "a":
            raise urllib.error.HTTPError("x", 503, "busy", {}, None)
        if p["name"] == "b" and len([c for c in calls if c == "b"]) == 1:
            return "not json at all", 10, 2
        return '```json\n{"role": "SDE Intern", "quote": "SDE Intern in Bengaluru"}\n```', 10, 5

    TRANSPORT = fake
    os.environ["A_API_KEY"] = os.environ["B_API_KEY"] = "k"
    provs = [{"name": "a", "base_url": "x", "model": "m", "rpm": 6000},
             {"name": "b", "base_url": "x", "model": "m", "rpm": 6000},
             {"name": "nokey", "base_url": "x", "model": "m", "rpm": 6000}]
    provs = [provs[2], provs[0], provs[1]]
    bad = []
    out = ask_json("sys", "posting: SDE Intern in Bengaluru", {"role": str, "quote": str}, "t", provs)
    if out.get("_provider") != "b" or out["role"] != "SDE Intern":
        bad.append(f"fallback/retry: {out}")
    n = len(calls)
    ask_json("sys", "posting: SDE Intern in Bengaluru", {"role": str, "quote": str}, "t", provs)
    if len(calls) != n:
        bad.append("cache not used")
    try:
        ask_json("sys", "Aditya Kumar Singh built RetailExpansion", {"role": str}, "t", provs)
        bad.append("privacy guard did not refuse")
    except LLMError:
        pass
    if check_schema({"a": 1, "b": [{"c": "x"}]}, {"a": int, "b": [{"c": str}]}):
        bad.append("schema false negative")
    if not check_schema({"a": "1"}, {"a": int, "z": str}):
        bad.append("schema false positive")
    if not quote_ok("SDE  intern in", "We need an SDE Intern in Bengaluru") or quote_ok("PPO of 12 LPA", "No PPO."):
        bad.append("quote check")
    try:
        ask_json("sys", "x", {"role": str}, "t", [provs[0]])
        bad.append("no-key provider did not raise")
    except LLMError:
        pass
    print("selftest:", "all checks pass" if not bad else f"FAILED: {bad}")
    return 1 if bad else 0


def main():
    a = sys.argv[1:]
    if "--selftest" in a:
        return selftest()
    if "--check" in a:
        return check()
    if "--usage" in a:
        return usage()
    if "--set-key" in a:
        return set_key(a[a.index("--set-key") + 1])
    if "--models" in a:
        return models(a[a.index("--models") + 1])
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
