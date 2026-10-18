#!/usr/bin/env python3
"""Rebuild the Simplified Chinese fork branch on top of the newest upstream tag.

Branch model
------------

    fork/main   this tooling (tools/zh/*) — never rebuilt, never touched
    fork/zh     one release tag + a single localization commit

``zh`` is always built from scratch on the chosen tag, so its history stays
linear and the only thing that can conflict is the code-side patch
(``localize.patch``): the call sites where a hardcoded English literal became
``KoeLocalizedString(...)``. The catalogue itself
(``zh-Hans.lproj/Localizable.strings``) is regenerated from the English file by
``translate.py``, which is why it can never conflict — it is pure key/value data
in the same key order as the source locale.

Because the tooling lives on ``main`` and ``zh`` is rebuilt from an upstream tag,
the build happens in a throwaway git worktree outside your checkout: your
working tree, ``main``, and any in-progress edits are untouched.

Usage
-----

    tools/zh/sync.py                 # newest upstream tag -> fork/zh
    tools/zh/sync.py --tag v1.0.34   # pin an explicit tag
    tools/zh/sync.py --no-push       # build locally, inspect, then push later

Exit codes: 0 nothing to do or success, 1 conflict/failure, 2 bad environment.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[2]
PATCH_REL = "tools/zh/localize.patch"
TRANSLATE_REL = "tools/zh/translate.py"
BRANCH = "zh"
UPSTREAM_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# The build workflow lives on main with the rest of the tooling and is copied
# into the rebuilt zh branch, because a push only triggers workflows that exist
# in the pushed ref.
WORKFLOW_SRC_REL = ".github/workflows/zh-release.yml"
WORKFLOW_DST_REL = ".github/workflows/zh-release.yml"

# The catalogue this fork carries. It is written to the *app repo* as plain
# data: the fork's Chinese coverage is a superset of upstream's, so it is
# carried here and merged with whatever the new upstream tag added. Without this
# step a rebuilt branch would fall back to upstream's own (much smaller)
# catalogue and the app would show English text again.
CARRIER_EN_REL = "tools/zh/en.strings"
CARRIER_ZH_REL = "tools/zh/zh-Hans.strings"
APP_EN_REL = "KoeApp/Koe/en.lproj/Localizable.strings"
APP_ZH_REL = "KoeApp/Koe/zh-Hans.lproj/Localizable.strings"

# Files the localization touches, in the order used to regenerate the patch.
PATCH_TARGETS = [
    "KoeApp/Koe/Overlay/SPOverlayPanel.m",
    "KoeApp/Koe/Settings/SPSettingsSidebar.m",
    "KoeApp/Koe/SetupWizard/SPSetupWizardWindowController.m",
    "KoeApp/Koe/StatusBar/SPStatusBarManager.m",
]


def log(msg: str = "") -> None:
    print(msg, flush=True)


def run(args: list[str], cwd: Path, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd),
        check=check,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def tag_sort_key(tag: str) -> tuple[int, int, int]:
    m = UPSTREAM_TAG_RE.match(tag)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def conflicted_files(worktree: Path) -> list[str]:
    out = run(["git", "status", "--porcelain"], worktree).stdout
    return [
        line[3:].strip()
        for line in out.splitlines()
        if line[:2].strip() in {"U", "AA", "DU", "UD", "AU", "UA", "DD"}
    ]


def print_conflict_help(worktree: Path, tag: str, conflicted: list[str]) -> None:
    log()
    log(f"CONFLICT: {PATCH_REL} does not apply cleanly to {tag}.")
    for name in conflicted:
        log(f"  conflicted: {name}")
    log()
    log("An upstream change touched a line the localization rewrites. Resolve by hand:")
    log(f"  1. cd {worktree}   (the tag is checked out detached)")
    log("  2. In each conflicted file keep the KoeLocalizedString(...) call where the")
    log("     meaning is unchanged; if upstream introduced a *new* literal, add a new")
    log("     key to KoeApp/Koe/en.lproj/Localizable.strings and use it there.")
    log("  3. Re-run tools/zh/translate.py to fill the new key (it validates format")
    log("     specifiers and aborts if the catalogue cannot be rebuilt).")
    log("  4. Regenerate the patch and re-run the sync:")
    log(f"       git diff {tag} -- " + " \\\n         ".join(PATCH_TARGETS))
    log(f"       > {PATCH_REL}")
    log("       tools/zh/sync.py --force")


def load_translate_module(repo: Path):
    """Import tools/zh/translate.py so its parser/serializer is reused as-is."""
    sys.path.insert(0, str(repo / "tools" / "zh"))
    import translate  # type: ignore[import-not-found]

    return translate


def merge_catalogue(repo: Path, worktree: Path, tag: str) -> tuple[int, int]:
    """Write this fork's catalogue into the rebuilt tag, keeping upstream's additions.

    Returns (total_keys, newly_added_keys).

    The fork's Chinese coverage is a superset of upstream's, so the carried
    catalogue cannot simply overwrite the tag's file (that would drop whatever
    new strings the tag introduced) nor be overwritten by it (that would drop
    the fork's translations). Upstream's English file is authoritative for keys
    it defines; keys only this fork knows are appended under their own heading.
    """
    translate = load_translate_module(repo)

    carrier_en = parse_text(translate, repo / CARRIER_EN_REL)
    carrier_zh = parse_text(translate, repo / CARRIER_ZH_REL)
    upstream_en = parse_text(translate, worktree / APP_EN_REL)

    upstream_keys = [k for k, _, _ in upstream_en]
    carrier_keys = [k for k, _, _ in carrier_en]
    extra = [k for k in carrier_keys if k not in set(upstream_keys)]
    removed = [k for k in upstream_keys if k not in set(carrier_keys)]

    # English: upstream verbatim, then this fork's additional keys.
    upstream_text = (worktree / APP_EN_REL).read_text(encoding="utf-8").rstrip("\n")
    extra_lines: list[str] = []
    if extra:
        extra_lines.append("")
        extra_lines.append(f"/* ─── Added by this fork's Simplified Chinese localization ─── */")
        for key in extra:
            _, value, comments = next(e for e in carrier_en if e[0] == key)
            extra_lines.extend(comments)
            extra_lines.append(f'"{key}" = "{value}";')
    while extra_lines and not extra_lines[-1].strip():
        extra_lines.pop()
    merged_en_keys = upstream_keys + extra

    (worktree / APP_EN_REL).write_text(
        upstream_text + "\n" + "\n".join(extra_lines) + "\n", encoding="utf-8"
    )

    # Chinese: this fork's translations, filtered to the merged key set. Keys the
    # new tag added have no translation yet and are left for translate.py.
    zh_by_key = {k: (v, c) for k, v, c in carrier_zh}
    zh_lines: list[str] = []
    for key in merged_en_keys:
        if key not in zh_by_key:
            continue
        value, comments = zh_by_key[key]
        zh_lines.extend(comments)
        zh_lines.append(f'"{key}" = "{value}";')
    (worktree / APP_ZH_REL).write_text("\n".join(zh_lines) + "\n", encoding="utf-8")

    missing = len(merged_en_keys) - sum(1 for k in merged_en_keys if k in zh_by_key)
    log(f"catalogue merged: {len(merged_en_keys)} keys total, "
        f"{len(extra)} fork-only, {len(removed)} dropped upstream, {missing} to translate")
    return len(merged_en_keys), missing


def parse_text(translate, path: Path) -> list[tuple[str, str, list[str]]]:
    entries, ok = translate.parse_strings(str(path))
    if not ok:
        raise RuntimeError(f"could not parse {path}")
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(REPO_DEFAULT), help="fork checkout root (default: this repo)")
    ap.add_argument("--tag", default=None, help="upstream tag to build on (default: newest release tag)")
    ap.add_argument("--upstream", default="origin", help="remote holding upstream (default origin)")
    ap.add_argument("--target", default="fork", help="remote to push the zh branch to (default fork)")
    ap.add_argument("--worktree", default=None, help="scratch worktree path (default: a temp dir)")
    ap.add_argument("--skip-translate", action="store_true", help="do not fill newly added keys")
    ap.add_argument("--no-push", action="store_true", help="build the branch but do not push")
    ap.add_argument("--force", action="store_true", help="rebuild even when the zh branch already matches the target tag")
    ap.add_argument("--keep-worktree", action="store_true", help="leave the scratch worktree in place")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        log(f"error: {repo} is not a git checkout")
        return 2

    for rel in (PATCH_REL, TRANSLATE_REL):
        if not (repo / rel).exists():
            log(f"error: {rel} is missing from {repo}")
            log("hint: this tooling belongs on fork/main; are you on the right branch?")
            return 2

    # Only the tracked tooling may be dirty; unrelated edits elsewhere are fine
    # because the build happens in a separate worktree.
    touched = run(["git", "status", "--porcelain", "--", "tools/zh"], repo).stdout.strip()
    if touched:
        log("error: tools/zh has uncommitted changes; commit them first so the rebuild")
        log("       can read a known-good patch:")
        for line in touched.splitlines():
            log(f"  {line}")
        return 2

    log(f"fetching {args.upstream} …")
    run(["git", "fetch", args.upstream, "--tags", "--prune"], repo)

    tags = [
        t.strip()
        for t in run(["git", "tag", "--list"], repo).stdout.splitlines()
        if UPSTREAM_TAG_RE.match(t.strip())
    ]
    if args.tag:
        if args.tag not in tags:
            log(f"error: tag {args.tag} not found after fetching")
            return 1
        target_tag = args.tag
    elif tags:
        target_tag = max(tags, key=tag_sort_key)
    else:
        log("error: no upstream release tag found")
        return 2

    # The zh branch is always "<tag> + one localization commit", so which tag it
    # was built on is derivable from the branch itself — no state file needed.
    synced = None
    branch_sha = run(["git", "rev-parse", "--verify", f"refs/heads/{BRANCH}"], repo, check=False)
    if branch_sha.returncode == 0:
        parent = run(["git", "rev-parse", f"refs/heads/{BRANCH}^"], repo, check=False)
        if parent.returncode == 0:
            sha = parent.stdout.strip()
            for tag in tags:
                if run(["git", "rev-parse", tag], repo).stdout.strip() == sha:
                    synced = tag
                    break
            if synced is None:
                synced = f"<unknown:{sha[:9]}>"

    log(f"newest upstream tag: {target_tag}   current {BRANCH} built on: {synced or '(branch absent)'}")

    if not args.force and synced == target_tag:
        log("nothing to do — already in sync")
        return 0
    if not args.force and synced and tag_sort_key(target_tag) < tag_sort_key(synced):
        log(f"error: {target_tag} is older than the current {BRANCH} base {synced}; refusing to go backwards")
        return 1

    patch = (repo / PATCH_REL).read_text(encoding="utf-8")

    # A detached scratch worktree keeps the user's checkout (and main) pristine.
    worktree = Path(args.worktree) if args.worktree else Path(tempfile.mkdtemp(prefix="koe-zh-build-"))
    if worktree.exists() and any(worktree.iterdir()):
        log(f"error: worktree path {worktree} is not empty")
        return 2
    worktree.parent.mkdir(parents=True, exist_ok=True)

    log(f"creating scratch worktree at {worktree}")
    added = run(["git", "worktree", "add", "--detach", "-f", str(worktree), target_tag], repo, check=False)
    if added.returncode != 0:
        log("error: could not create the scratch worktree")
        log(added.stderr.strip())
        return 1

    try:
        log(f"applying {PATCH_REL} …")
        applied = run(["git", "apply", "--3way", "--whitespace=nowarn", "-"], worktree, check=False, input_text=patch)
        if applied.returncode != 0:
            print_conflict_help(worktree, target_tag, conflicted_files(worktree))
            if not args.keep_worktree:
                log()
                log("(scratch worktree kept for you to resolve in, then removed on the next run)")
                args.keep_worktree = True
            return 1
        log("localization patch applied cleanly")

        # Merge this fork's catalogue in before translating: it carries the
        # Chinese coverage upstream does not have, and merging first means a new
        # upstream key is the only thing translate.py has left to do.
        try:
            merge_catalogue(repo, worktree, target_tag)
        except Exception as exc:  # noqa: BLE001 - a missing carrier is fatal, report clearly
            log(f"error: could not merge the localization catalogue: {exc}")
            return 1

        # The zh branch is rebuilt from an upstream tag, which does not contain
        # this fork's workflow. A push only starts workflows defined in the ref
        # that was pushed, so the workflow has to be copied into the branch or
        # the sync would never trigger a build.
        workflow_src = repo / WORKFLOW_SRC_REL
        if not workflow_src.exists():
            log(f"error: {WORKFLOW_SRC_REL} is missing; cannot wire up the build")
            return 1
        workflow_dst = worktree / WORKFLOW_DST_REL
        workflow_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workflow_src, workflow_dst)
        log(f"installed workflow at {WORKFLOW_DST_REL}")

        if not args.skip_translate:
            log("filling missing translations …")
            proc = subprocess.run(
                [sys.executable, str(repo / TRANSLATE_REL), "--repo", str(worktree)],
                cwd=str(repo),
                text=True,
            )
            if proc.returncode != 0:
                log("error: translation failed; nothing committed")
                return 1

        log("checking locale parity …")
        chk = subprocess.run(
            [sys.executable, str(repo / TRANSLATE_REL), "--repo", str(worktree), "--check"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        log(chk.stdout.strip())
        if chk.returncode != 0:
            log("error: the two locale files disagree; the app would show raw keys")
            return 1

        run(["git", "add", "-A"], worktree)
        if not run(["git", "status", "--porcelain"], worktree).stdout.strip():
            log("error: the patch produced no changes — nothing to commit")
            return 1

        version = target_tag.lstrip("v")
        run(
            [
                "git",
                "commit",
                "-qm",
                f"chore(zh): Simplified Chinese localization for {target_tag}",
                "-m",
                f"Rebuilt on upstream {target_tag} ({version}) from {PATCH_REL}.\n\n"
                "The catalogue is regenerated from the English one, so no translation\n"
                "conflict is possible; only the code-side call sites can conflict.",
            ],
            worktree,
        )
        commit = run(["git", "rev-parse", "HEAD"], worktree).stdout.strip()
        log(f"committed {commit[:9]} on top of {target_tag}")

        # Move the branch pointer from the main checkout; the worktree is
        # detached, so nothing is checked out on zh.
        moved = run(["git", "branch", "-f", BRANCH, commit], repo, check=False)
        if moved.returncode != 0:
            log(f"error: could not update the {BRANCH} branch (is it checked out elsewhere?)")
            log(moved.stderr.strip())
            return 1
        log(f"branch {BRANCH} -> {commit[:9]}  (base {target_tag})")

        if args.no_push:
            log("--no-push: built locally; run `git push --force-with-lease "
                f"{args.target} {BRANCH}` when ready")
            return 0

        log(f"pushing {BRANCH} to {args.target} …")
        # Fetch the target first so --force-with-lease has an up-to-date lease.
        run(["git", "fetch", args.target], repo, check=False)
        push = run(
            ["git", "push", "--force-with-lease", args.target, f"{BRANCH}:{BRANCH}"],
            repo,
            check=False,
        )
        if push.returncode != 0:
            log("error: push failed")
            log((push.stderr or push.stdout).strip())
            return 1
        log((push.stderr or push.stdout).strip())
        log(f"done — {BRANCH} is {target_tag} + localization")
        return 0
    finally:
        if not args.keep_worktree:
            run(["git", "worktree", "remove", "--force", str(worktree)], repo, check=False)
            if worktree.exists() and worktree.name.startswith("koe-zh-build-"):
                shutil.rmtree(worktree, ignore_errors=True)
        else:
            log(f"scratch worktree left at {worktree}")


if __name__ == "__main__":
    sys.exit(main())
