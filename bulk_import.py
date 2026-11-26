"""Import a folder of resumes into the talent pool from Terminal, with progress and an ETA.

    ./venv/bin/python bulk_import.py sample_pool_500
    ./venv/bin/python bulk_import.py ~/Downloads/resumes --model llama3:latest

Each resume is read once by the local model (about 10 seconds each on a MacBook Air), so
500 resumes take roughly an hour and a half. The pool is saved after every resume: you can stop
with Ctrl+C at any time and run the same command again to carry on where it stopped. Files
already in the pool are skipped in a fraction of a second. On a Mac, prefix the command with
`caffeinate -i` so the computer doesn't sleep during a long import.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import core


def fmt_minutes(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{max(m, 1)} min"


def main() -> int:
    ap = argparse.ArgumentParser(description="Import a folder of PDF/DOCX resumes into the talent pool.")
    ap.add_argument("folder", help="folder of resumes (searched recursively)")
    ap.add_argument("--model", help="Ollama model to use (default: the app's preferred installed model)")
    args = ap.parse_args()

    folder = Path(args.folder).expanduser()
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in core.SUPPORTED_TYPES)
    if not files:
        print(f"No PDF or DOCX files in {folder}")
        return 1
    try:
        installed = core.list_models()
    except core.OllamaError as e:
        print(e)
        return 1
    model = args.model or core.pick_default_model(installed)
    if not model:
        print("Ollama has no models. Run: ollama pull qwen3:4b")
        return 1

    pool = core.load_pool()
    known = {r.get("fileHash") for r in pool}
    print(f"{len(files)} resumes in {folder} · pool has {len(pool)} · model {model}")
    print("Stop any time with Ctrl+C; run the same command again to resume.\n")

    counts = {"added": 0, "duplicate": 0, "no_text": 0, "error": 0}
    model_time, model_calls = 0.0, 0
    errors = []
    start = time.time()
    try:
        for i, path in enumerate(files, start=1):
            data = path.read_bytes()
            if core.hashlib.sha256(data).hexdigest() in known:
                counts["duplicate"] += 1
                continue
            t0 = time.time()
            status, message, record = core.add_resume(path.name, data, model, pool)
            took = time.time() - t0
            counts[status] += 1
            if status in ("added", "error"):
                model_time += took
                model_calls += 1
            if status == "added":
                known.add(record["fileHash"])
                core.save_pool(pool)
            elif status == "error":
                errors.append(message)
            left = len(files) - i
            eta = (model_time / model_calls) * left if model_calls else 0
            line = f"[{i}/{len(files)}] {status:<9} {path.name[:48]:<48} {took:4.1f}s"
            if model_calls:
                line += f" · about {fmt_minutes(eta)} left"
            print(line, flush=True)
    except KeyboardInterrupt:
        print("\nStopped. Everything imported so far is saved; run the same command to continue.")

    mins = (time.time() - start) / 60
    print(f"\nDone in {mins:.1f} min: {counts['added']} added, {counts['duplicate']} already in the pool, "
          f"{counts['no_text']} without readable text, {counts['error']} errors. Pool size: {len(pool)}.")
    if model_calls:
        print(f"Average model time: {model_time / model_calls:.1f} s per resume.")
    for m in errors[:10]:
        print("  error:", m)
    if errors:
        print("Run the same command again to retry the errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
