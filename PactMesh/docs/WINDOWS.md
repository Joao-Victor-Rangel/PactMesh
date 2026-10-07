# Running PactMesh on Windows (PowerShell)

Run **one command at a time**. Pasting a block makes PowerShell treat the next lines as keystrokes, and
that can cancel an installation halfway (`ERROR: Operation cancelled by user`).

## 1. Get the code and create the virtual environment

```powershell
git clone -b pactmesh-mvp https://github.com/Joao-Victor-Rangel/PactMesh.git
cd PactMesh
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If the last line says running scripts is disabled, allow it for this window only and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

The prompt now starts with `(.venv)`. Every command below must run in a window that shows `(.venv)`.

## 2. Install and run the demo

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pactmesh demo --keep
```

Open the printed `http://127.0.0.1:8700/#token=...` link. If Windows Firewall asks, allow **private
networks**: everything listens on 127.0.0.1 only. Stop with `Ctrl+C`.

Optional checks:

```powershell
pytest
```

## 3. Laya (free, Apache-2.0)

```powershell
pip install -r requirements-model.txt
python -m pactmesh laya-run
```

`laya-run` downloads Laya the first time, serves it, runs the benchmark (calibration on validation,
metrics on the frozen test split), runs the 300-scenario safety evaluation with the calibrated model and
the demo with Cripto deciding through Laya. The results go to `evaluation\laya\results.md` and
`evaluation\results-model.json`. Use `--skip-demo` to stop after the evaluations.

## If something fails

| Message | Fix |
|---|---|
| `PyNaCl is not installed in this Python` / `No module named 'nacl'` | the virtual environment is not active (`(.venv)` missing) or step 2 was interrupted: activate and run the `pip install` again |
| `process 'relay' exited ... address already in use` | another demo is still running: close it, or `$env:PACTMESH_PORT_BASE=9700` and retry |
| `cannot load torch/transformers: OSError ...` | install the Microsoft Visual C++ Redistributable (x64), reopen the terminal |
| `cannot load torch/transformers: ImportError ...` | `pip install -r requirements-model.txt` inside `(.venv)` |
| `model server exited` | the last log lines are printed; the full log is `.pactmesh-laya\model-server.log` |
