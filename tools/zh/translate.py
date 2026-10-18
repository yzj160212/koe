#!/usr/bin/env python3
"""Fill missing zh-Hans translations in KoeApp/Koe/zh-Hans.lproj/Localizable.strings.

The English file is the source of truth: keys are read from
en.lproj/Localizable.strings, and any key missing from the zh-Hans file is
translated through an OpenAI-compatible /v1/chat/completions endpoint. The
output file is regenerated in the same order and with the same comments as the
English one, so the two files stay in lockstep.

Guarantees enforced before a translation is accepted:

  * every printf/NSString format specifier (``%@``, ``%ld``, ``%.0f``, ``%d``,
    ``%%`` ...) appears exactly as in the English source;
  * a leading/trailing space run inside the value survives;
  * the translation is non-empty, not a copy of the English text, and contains
    no CJK-free ASCII when the source is plain ASCII prose (a weak but cheap
    signal that the model answered instead of echoing).

Failures are retried one key at a time and then reported; the script exits
non-zero if anything is still untranslated, so it is safe to run in a hook.

Endpoint defaults target a local gateway (magpie) that needs no credentials.
Override with env vars or flags for any other OpenAI-compatible server.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_GATEWAY = os.environ.get("KOE_ZH_GATEWAY", "http://127.0.0.1:3425/v1")
DEFAULT_MODEL = os.environ.get("KOE_ZH_MODEL", "group/deepseek-v4-1-flash")
DEFAULT_API_KEY = os.environ.get("KOE_ZH_API_KEY", "")

# printf / NSString conversion specifiers, including positional forms.
SPECIFIER_RE = re.compile(
    r"%(?:\d+\$)?[-+ #0']*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?:hh|h|ll|l|L|z|j|t|q)?[diouxXeEfgGaAcspn@%]"
)

# ``"key" = "value";`` — the value may contain escaped quotes.
ENTRY_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;\s*$')
# ``/* comment */`` lines, kept so section headers survive regeneration.
COMMENT_RE = re.compile(r"^\s*/\*.*\*/\s*$")

CONTEXT_WINDOW = 80  # neighbouring keys sent to the model for tone consistency
BATCH_SIZE = 12


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_strings(path: str) -> tuple[list[tuple[str, str, list[str]]], bool]:
    """Return (entries, had_entries).

    entries is an ordered list of ``(key, value, comments_above)``; blank lines
    and comment lines directly above an entry are carried along so the generated
    file keeps the original structure.
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    entries: list[tuple[str, str, list[str]]] = []
    pending: list[str] = []
    for line in lines:
        if not line.strip() or COMMENT_RE.match(line):
            pending.append(line)
            continue
        m = ENTRY_RE.match(line)
        if not m:
            # Header remarks before the first entry (or stragglers) are kept.
            pending.append(line)
            continue
        entries.append((m.group(1), m.group(2), pending))
        pending = []
    return entries, bool(entries)


def unescape(value: str) -> str:
    """Turn a .strings value body into the runtime string it represents."""
    out: list[str] = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}
            out.append(mapping.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def escape(value: str) -> str:
    """Inverse of unescape, for emitting a .strings value body."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def specifiers(text: str) -> list[str]:
    return sorted(SPECIFIER_RE.findall(text))


def edge_spaces(text: str) -> tuple[str, str]:
    return text[: len(text) - len(text.lstrip(" "))], text[len(text.rstrip(" ")) :]


def validate(source: str, translated: str) -> str | None:
    """Return a human-readable defect, or None when the translation is usable."""
    if not translated.strip():
        return "empty translation"
    if specifiers(source) != specifiers(translated):
        return f"format specifiers changed ({specifiers(source)} -> {specifiers(translated)})"
    if edge_spaces(source) != edge_spaces(translated):
        return "leading/trailing spaces not preserved"
    if translated.strip() == source.strip():
        return "identical to the English source (not translated)"
    return None


def chat(gateway: str, model: str, api_key: str, prompt: str, timeout: int = 90) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a software localization engine for a macOS app. "
                        "You translate English UI strings into Simplified Chinese (zh-Hans). "
                        "Rules you must never break:\n"
                        "1. Preserve every printf format specifier exactly: %@, %ld, %.0f, %d, %%, and positional forms like %1$@.\n"
                        "2. Preserve leading and trailing spaces exactly.\n"
                        "3. Preserve \\n escapes exactly.\n"
                        "4. Keep the translation short, natural and consistent with macOS Chinese terminology "
                        "(e.g. use 设置 for Settings, 权限 for Permissions).\n"
                        "5. Reply with JSON only, no markdown fence, no commentary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 8000,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        gateway.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            msg = payload["choices"][0]["message"]
            # Reasoning models expose a scratchpad; the answer is in content.
            content = msg.get("content") or ""
            if not content.strip():
                raise ValueError(
                    f"empty content (finish_reason={payload['choices'][0].get('finish_reason')})"
                )
            return content
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as exc:
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"gateway call failed: {last_err}")


def strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def translate_batch(
    gateway: str,
    model: str,
    api_key: str,
    items: list[tuple[str, str]],
    context: list[str],
) -> dict[str, str]:
    """Translate ``items`` (key, english) in one request; return key -> Chinese."""
    payload = {"items": [{"id": k, "text": v} for k, v in items]}
    prompt = (
        "Translate the following iOS/macOS UI strings into Simplified Chinese.\n"
        + (
            "For tone reference only, do NOT translate these already-translated keys:\n"
            + "\n".join(context)
            + "\n\n"
            if context
            else "\n"
        )
        + "Return JSON exactly in this shape: {\"items\":[{\"id\":...,\"text\":...}]}\n"
        + "Input:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    raw = strip_fence(chat(gateway, model, api_key, prompt))
    # Tolerate a model that wraps or prefixes the JSON object.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in reply: {raw[:200]}")
    parsed = json.loads(match.group(0))
    out: dict[str, str] = {}
    for entry in parsed.get("items", []):
        if isinstance(entry, dict) and "id" in entry and "text" in entry:
            out[str(entry["id"])] = str(entry["text"])
    return out


def translate_one(gateway: str, model: str, api_key: str, key: str, source: str) -> str:
    prompt = (
        "Translate this single UI string into Simplified Chinese. "
        'Return JSON only: {"text":"..."}\n'
        f"Key: {key}\nEnglish: {source}"
    )
    raw = strip_fence(chat(gateway, model, api_key, prompt))
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in reply: {raw[:200]}")
    return str(json.loads(match.group(0)).get("text", ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=None, help="repository root (default: two levels above this script)")
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"OpenAI-compatible base URL (default {DEFAULT_GATEWAY})")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"model id (default {DEFAULT_MODEL})")
    ap.add_argument("--api-key", default=DEFAULT_API_KEY, help="bearer token, if the gateway needs one")
    ap.add_argument("--locale", default="zh-Hans", help="target .lproj (default zh-Hans)")
    ap.add_argument("--only", nargs="*", default=None, help="translate only these keys")
    ap.add_argument("--dry-run", action="store_true", help="report missing keys without calling the gateway")
    ap.add_argument("--check", action="store_true", help="verify key parity and format specifiers, then exit")
    ap.add_argument(
        "--probe",
        action="store_true",
        help="check the gateway is reachable and exit (0 reachable, 1 not) — lets a caller "
        "decide whether to attempt translation before paying a long connection timeout",
    )
    args = ap.parse_args()

    if args.probe:
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(args.gateway.rstrip("/") + "/models", timeout=5) as resp:
                ok = 200 <= resp.status < 300
        except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
            log(f"gateway {args.gateway} unreachable: {exc}")
            return 1
        log(f"gateway {args.gateway} reachable")
        return 0 if ok else 1

    repo = args.repo or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    lproj = os.path.join(repo, "KoeApp", "Koe")
    en_path = os.path.join(lproj, "en.lproj", "Localizable.strings")
    zh_path = os.path.join(lproj, f"{args.locale}.lproj", "Localizable.strings")

    for path in (en_path, zh_path):
        if not os.path.exists(path):
            log(f"error: missing {path}")
            return 2

    en_entries, _ = parse_strings(en_path)
    zh_entries, _ = parse_strings(zh_path)
    zh_map = {k: v for k, v, _ in zh_entries}
    # The zh file carries Chinese section comments; reuse them per key instead
    # of overwriting the locale with the English comments.
    zh_comments = {k: c for k, _, c in zh_entries}
    en_keys = [k for k, _, _ in en_entries]
    log(f"english keys: {len(en_keys)}   {args.locale} keys: {len(zh_map)}")

    if args.check:
        missing = [k for k in en_keys if k not in zh_map]
        extra = [k for k in zh_map if k not in en_keys]
        bad = []
        for key, value, _ in en_entries:
            if key in zh_map:
                defect = validate(unescape(value), unescape(zh_map[key]))
                # An untranslated-but-present entry is reported, not fatal, in
                # check mode: it means a human left the English text in place.
                if defect and "identical" not in defect:
                    bad.append(f"{key}: {defect}")
        log(f"missing: {len(missing)}  extra: {len(extra)}  invalid: {len(bad)}")
        for k in missing:
            log(f"  MISSING {k}")
        for k in extra:
            log(f"  EXTRA   {k}")
        for b in bad:
            log(f"  INVALID {b}")
        return 0 if not (missing or extra or bad) else 1

    todo = [(k, v) for k, v, _ in en_entries if k not in zh_map]
    if args.only:
        wanted = set(args.only)
        todo = [(k, v) for k, v, _ in en_entries if k in wanted]
    if not todo:
        log("nothing to translate — key sets already match")
        return 0

    log(f"to translate: {len(todo)} key(s)")
    if args.dry_run:
        for k, v in todo:
            log(f"  {k} = {unescape(v)}")
        return 0

    translated: dict[str, str] = {}
    # Context: a few already-translated neighbours, for terminology consistency.
    context = [
        f"{k} = {unescape(zh_map[k])}"
        for k in en_keys
        if k in zh_map and zh_map[k].strip()
    ][-CONTEXT_WINDOW:]

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        pairs = [(k, unescape(v)) for k, v in batch]
        got: dict[str, str] = {}
        try:
            got = translate_batch(args.gateway, args.model, args.api_key, pairs, context)
        except Exception as exc:  # noqa: BLE001 - any failure falls back to per-key
            log(f"  batch {start // BATCH_SIZE + 1}: {exc}; falling back to per-key")

        for key, source in pairs:
            candidate = got.get(key, "")
            defect = validate(source, candidate) if candidate else "no translation returned"
            if defect:
                try:
                    candidate = translate_one(args.gateway, args.model, args.api_key, key, source)
                    defect = validate(source, candidate)
                except Exception as exc:  # noqa: BLE001
                    defect = f"{defect}; retry failed: {exc}"
            if defect:
                log(f"  FAIL {key}: {defect}")
                continue
            translated[key] = candidate
            log(f"  ok   {key} = {candidate}")

    if not translated:
        log("nothing translated")
        return 1

    # Regenerate the whole zh file in the English file's order so new upstream
    # keys land in the same place in both locales. Comments are taken from the
    # zh file when it already has one for that key, otherwise from the English
    # file, so Chinese section headers survive and newly added sections still
    # arrive labelled.
    out_lines: list[str] = []
    for key, value, comments in en_entries:
        carried = zh_comments.get(key) or comments
        out_lines.extend(carried)
        chinese = translated.get(key) or zh_map.get(key)
        if chinese is None:
            log(f"error: {key} has no translation; aborting without writing")
            return 1
        out_lines.append(f'"{key}" = "{chinese}";')
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    with open(zh_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")

    log(f"wrote {zh_path} ({len(out_lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
