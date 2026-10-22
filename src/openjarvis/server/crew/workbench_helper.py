#!/usr/bin/env python3
"""Aibussol Workbench helper: runs on YOUR computer and lets Helix's crew build in your real projects with Claude Code.

Needs: Python 3.9+, Claude Code installed and signed in (it runs on your own Claude plan), Git, and Unity / Unreal for game projects.

First run (Windows PowerShell example; one line):
    python aibussol_workbench.py --token wb.XXXX --project "Deadzone=C:\\Users\\you\\Unity\\DeadzoneRun"
        --unity "C:\\Program Files\\Unity\\Hub\\Editor\\2022.3.40f1\\Editor\\Unity.exe"
After that, just:  python aibussol_workbench.py      (the settings are saved in ~/.aibussol_workbench.json)

What it does: says hello to Aibussol every minute, picks up jobs Helix gives to "Rig", runs Claude Code in the folder mapped to the
job's project, and reports back. It only makes outgoing requests; nothing on your computer is opened to the internet.
Rules it gives Claude Code: work only in that folder, commit to a crew/<name> branch, never push to main, never upload or publish
anything (you ship builds yourself). Stop it any time with Ctrl+C.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "1.4"
CONFIG = Path.home() / ".aibussol_workbench.json"
POLL_S, HELLO_S, PROGRESS_S = 20, 60, 120


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG, 0o600)
    except Exception:
        pass


def call(cfg: dict, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(cfg["server"].rstrip("/") + path, data=json.dumps(body or {}).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["token"],
                                          # a named client: the site's firewall refuses Python's default user agent
                                          "User-Agent": f"AibussolWorkbench/{VERSION} (+https://aibussol.com)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def find_claude(cfg: dict) -> str | None:
    for name in [cfg.get("claude"), "claude", "claude.cmd", "claude.exe"]:
        if name and (shutil.which(name) or Path(name).is_file()):
            return shutil.which(name) or name
    return None


def project_folder(cfg: dict, project: str) -> tuple[str | None, str]:
    """The folder for a job's project: a mapped one, else a folder of that name in the projects root (made if new and named clearly)."""
    projects = cfg.get("projects") or {}
    key = re.sub(r"[^a-z0-9]", "", (project or "").lower())
    for name, folder in projects.items():
        if re.sub(r"[^a-z0-9]", "", name.lower()) == key:
            return folder, name
    root = cfg.get("projects_root")
    if root and Path(root).is_dir() and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,39}", project or ""):
        for d in Path(root).iterdir():
            if d.is_dir() and re.sub(r"[^a-z0-9]", "", d.name.lower()) == key:
                cfg.setdefault("projects", {})[d.name] = str(d)
                save_config(cfg)
                return str(d), d.name
        new = Path(root) / project
        new.mkdir(parents=True, exist_ok=True)            # a new game: its own empty folder; Claude Code creates the Unity project in it
        cfg.setdefault("projects", {})[project] = str(new)
        save_config(cfg)
        print(f"[workbench] new project folder {new}")
        return str(new), project
    if "default" in projects:
        return projects["default"], "default"
    return None, project


def build_prompt(job: dict, cfg: dict, folder_name: str) -> str:
    others = [(n, f) for n, f in (cfg.get("projects") or {}).items() if n != folder_name and Path(f).is_dir()]
    tools = []
    if cfg.get("unity"):
        tools.append(f'Unity editor: "{cfg["unity"]}" (batch mode: -batchmode -nographics -quit -projectPath . -logFile -)')
    if cfg.get("unreal"):
        tools.append(f'Unreal editor / UAT: "{cfg["unreal"]}"')
    folder = (cfg.get("projects") or {}).get(folder_name)
    empty = bool(folder) and Path(folder).is_dir() and not any(Path(folder).iterdir())
    return (
        f"You are Rig, the Workbench member of Michael's Aibussol crew, working on his own computer in the project folder "
        f"'{folder_name}' (the current directory).\n\n"
        + ("THIS FOLDER IS EMPTY: it is a new project. First create a new Unity project in it with the Unity editor in batch mode "
           "(-batchmode -quit -createProject .), set it up for the game (URP 2D or 3D as fits, the Input System, Android), then build the game.\n\n"
           if empty else "")
        +
        f"Mission: {job.get('goal', '')}\nYour task: {job.get('title', '')}\n{job.get('brief', '')}\n\n"
        f"Results from the crew so far:\n{job.get('results_so_far') or '- none'}\n\n"
        f"Michael's house rules:\n{job.get('house_rules', '')}\n\n"
        + ("Tools on this computer:\n- " + "\n- ".join(tools) + "\n\n" if tools else "")
        + "Rules for this job:\n"
        "- Change files only inside this folder. Read the existing project first and follow how it is built (for games: like Deadzone Run).\n"
        + ("- You may READ and copy FROM these other project folders on this computer (never change them, except running a Unity build "
           "into that project's own Builds folder when the task asks for it): " + "; ".join(f"{n} = {f}" for n, f in others) + "\n" if others else "")
        +
        "- If this folder is a git repo, create or reuse a branch named crew/<short-name>, commit your work there, and never push to "
        "main or merge anything.\n"
        "- Build and test when you can (Unity batch mode, play-mode tests, compile checks). Fix what fails.\n"
        "- Never upload to an app store, publish, deploy, spend money or send messages. Michael ships builds himself.\n"
        "- Finish with a short report: what you changed, what you built and tested and the result, what still needs Michael "
        "(for example: open the project in Unity and play the level with a PlayStation controller)."
    )


def run_job(cfg: dict, job: dict) -> None:
    tid = job["task_id"]
    folder, name = project_folder(cfg, job.get("project", ""))
    if not folder or not Path(folder).is_dir():
        call(cfg, f"/crew/workbench/{tid}/done", {"ok": False, "summary": f"No folder on this computer is mapped to the project "
             f"'{job.get('project') or '(none)'}'. Run the helper once with --project \"{job.get('project') or 'Name'}=C:\\\\path\\\\to\\\\project\"."})
        return
    claude = find_claude(cfg)
    if not claude:
        call(cfg, f"/crew/workbench/{tid}/done", {"ok": False, "summary": "Claude Code is not installed on this computer (or not on PATH)."})
        return
    print(f"[workbench] job: {job.get('title')} in {folder}")
    started, stop = time.time(), threading.Event()

    def progress():
        while not stop.wait(PROGRESS_S):
            try:
                call(cfg, f"/crew/workbench/{tid}/progress", {"note": f"Still working ({int((time.time() - started) / 60)} min)."})
            except Exception:
                pass

    threading.Thread(target=progress, daemon=True).start()
    # the job goes in on stdin: on Windows, claude.cmd cuts a command-line argument at its first line break
    extra_dirs = [f for n, f in (cfg.get("projects") or {}).items() if n != name and Path(f).is_dir()]
    cmd = [claude, "-p", "--output-format", "json", *(["--add-dir", *extra_dirs] if extra_dirs else []), "--permission-mode", "acceptEdits",
           "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash", "--max-turns", str(cfg.get("max_turns", 80))]
    ok, summary = False, ""
    try:
        p = subprocess.run(cmd, cwd=folder, input=build_prompt(job, cfg, name), capture_output=True, text=True, timeout=int(job.get("timeout_s") or 4 * 3600),
                           encoding="utf-8", errors="replace")
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except Exception:
            out = {}
        summary = str(out.get("result") or p.stdout[-4000:] or p.stderr[-2000:] or "No output.")
        ok = p.returncode == 0 and not out.get("is_error")
    except subprocess.TimeoutExpired:
        summary = "Stopped: the job ran longer than its time limit."
    except Exception as exc:
        summary = f"The helper could not run Claude Code: {exc}"
    finally:
        stop.set()
    minutes = int((time.time() - started) / 60)
    call(cfg, f"/crew/workbench/{tid}/done", {"ok": ok, "summary": summary[:20000], "minutes": minutes})
    print(f"[workbench] {'done' if ok else 'failed'} after {minutes} min")


def hello(cfg: dict, busy: bool) -> None:
    call(cfg, "/crew/workbench/hello", {"host": platform.node(), "projects": list((cfg.get("projects") or {}).keys()),
                                         "claude": bool(find_claude(cfg)), "unity": bool(cfg.get("unity") and Path(cfg["unity"]).exists()),
                                         "unreal": bool(cfg.get("unreal") and Path(cfg["unreal"]).exists()), "busy": busy, "version": VERSION})


def main() -> int:
    ap = argparse.ArgumentParser(description="Aibussol Workbench helper")
    ap.add_argument("--server", help="default https://aibussol.com")
    ap.add_argument("--token", help="the token from 'Pair your PC' on the Station")
    ap.add_argument("--project", action="append", default=[], help='NAME=FOLDER, e.g. "Deadzone=C:\\Games\\DeadzoneRun" (repeat)')
    ap.add_argument("--unity", help="path to Unity.exe")
    ap.add_argument("--unreal", help="path to the Unreal editor or RunUAT")
    ap.add_argument("--claude", help="path to the claude command, if it is not on PATH")
    ap.add_argument("--projects-root", help=r"folder for new game projects (default C:\UnityProjects when it exists)")
    a = ap.parse_args()
    cfg = load_config()
    cfg.setdefault("server", "https://aibussol.com")
    for k in ("server", "token", "unity", "unreal", "claude"):
        if getattr(a, k):
            cfg[k] = getattr(a, k)
    if a.projects_root:
        cfg["projects_root"] = a.projects_root
    elif not cfg.get("projects_root") and Path(r"C:\UnityProjects").is_dir():
        cfg["projects_root"] = r"C:\UnityProjects"
    for pr in a.project:
        name, _, folder = pr.partition("=")
        if name.strip() and folder.strip():
            cfg.setdefault("projects", {})[name.strip()] = folder.strip().strip('"')
    if not cfg.get("token"):
        print("Pair first: on the Station, open Connections > Your PC > Pair, then run with --token wb.…")
        return 2
    save_config(cfg)
    print(f"[workbench] Aibussol Workbench {VERSION} for {cfg['server']}; projects: {', '.join(cfg.get('projects', {})) or 'none yet'}")
    if not find_claude(cfg):
        print("[workbench] warning: Claude Code was not found. Install it and sign in, or pass --claude C:\\path\\to\\claude.cmd")
    last_hello = 0.0
    while True:
        try:
            if time.time() - last_hello > HELLO_S:
                hello(cfg, False)
                last_hello = time.time()
            job = call(cfg, "/crew/workbench/next").get("job")
            if job:
                hello(cfg, True)
                run_job(cfg, job)
                last_hello = 0.0
                continue
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                print("[workbench] Aibussol refused the token. Pair again on the Station and run with --token.")
                return 1
            print(f"[workbench] server answered {exc.code}; trying again shortly")
        except Exception as exc:
            print(f"[workbench] no connection ({type(exc).__name__}); trying again shortly")
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
