#!/usr/bin/env python3
"""Run SlicerMorph's GPA module headlessly via slicer-mock.

GPA's button handler (GPAWidget.onLoad) cannot be called outside Slicer's
GUI: it mixes the analysis core with widget setup, layout assignment, 3D
camera zoom, scatter-plot wiring, etc. But the underlying math classes
GPALogic.loadLandmarks, GPALogic.LMData.doGpa / calcEigen / writeOutData
are pure numpy and can be driven directly.

This example replicates exactly what onLoad does on the analysis side:
    GPALogic().loadLandmarks(paths, [], 'mrk.json')   # read .mrk.json files
    LMData().doGpa(BoasOption=False)                  # Procrustes
    LMData.calcEigen()                                # PCA via SVD
    LMData.writeOutData(out_dir, file_names)          # CSV outputs

It produces the same five CSV files GPA's GUI would emit (outputData.csv,
meanShape.csv, eigenvalues.csv, eigenvector.csv, pcScores.csv).

Run with Slicer's bundled Python (canonical) — pandas is required and is
already bundled in PythonSlicer:

    /path/to/Slicer/bin/PythonSlicer test_gpa_headless.py \\
        --gpa-dir /path/to/SlicerMorph/GPA \\
        --landmarks /path/to/dir/with/*.mrk.json \\
        --out /tmp/gpa_out
"""
import argparse
import glob
import os
import sys
import types

# ── 1. Bootstrap slicer-mock ──────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.modules.setdefault("qt", types.ModuleType("qt"))

import slicer_mock as _sm                                  # noqa: E402

# Manually install the slicer namespace (we're not loading a single extension
# from a known path — instead we're using the mock as a general-purpose
# headless slicer surface).
_slicer = types.ModuleType("slicer")
_slicer.mrmlScene = _sm._MockScene()
_slicer.util      = _sm._MockUtil
_slicer.app       = _sm._MockApp()
_slicer.modules   = types.SimpleNamespace(
    segmentations=types.SimpleNamespace(logic=lambda: _sm._MockSegmentationsLogic()),
)
_slicer.vtkMRMLMarkupsFiducialNode = _sm._MockFiducialNode
_slicer.vtkMRMLModelNode           = _sm._MockModelNode
_slicer.vtkMRMLTransformNode       = _sm._MockTransformNode
sys.modules["slicer"] = _slicer

# install() also wires the qt/ctk mocks; we duplicate the relevant bits here
import qt
qt.QMessageBox      = types.SimpleNamespace(critical=lambda *a, **kw: None,
                                             information=lambda *a, **kw: None)
qt.QObject          = type("QObject", (), {})
qt.QEvent           = types.SimpleNamespace(MouseButtonPress=2, MouseMove=5, MouseButtonRelease=3)
qt.Qt               = types.SimpleNamespace(LeftButton=1, RightButton=2)
for n in ("QWidget", "QLabel", "QPushButton", "QLineEdit", "QTabWidget",
          "QFileDialog", "QColor", "QUrl", "QDesktopServices", "QIcon"):
    if not hasattr(qt, n):
        setattr(qt, n, type(n, (), {}))

# ScriptedLoadableModule base classes — GPALogic inherits from
# ScriptedLoadableModuleLogic
slm = types.ModuleType("slicer.ScriptedLoadableModule")
for n in ("ScriptedLoadableModule", "ScriptedLoadableModuleWidget",
          "ScriptedLoadableModuleLogic", "ScriptedLoadableModuleTest"):
    setattr(slm, n, type(n, (), {"__init__": lambda self, *a, **kw: None}))
sys.modules["slicer.ScriptedLoadableModule"] = slm

# ── 2. Parse arguments and locate inputs ──────────────────────────────────────
ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--gpa-dir", default="/home/maga/Desktop/SlicerMorph/GPA",
                help="Path to SlicerMorph/GPA/ (must contain GPA.py + Support/)")
ap.add_argument("--landmarks", required=True,
                help="Directory containing .mrk.json files (one per specimen)")
ap.add_argument("--out", required=True,
                help="Directory where GPA results (5 CSVs) will be written")
ap.add_argument("--boas", action="store_true",
                help="Use Boas coordinates (skip scaling step in GPA)")
ap.add_argument("--exclude", default="",
                help="Comma-separated 1-based landmark indices to drop")
args = ap.parse_args()

# Put GPA on sys.path AND its Support/ for the 'Support.gpa_lib' relative import
sys.path.insert(0, args.gpa_dir)
sys.path.insert(0, os.path.join(args.gpa_dir, "Support"))

# ── 3. Import GPA module and run the headless pipeline ────────────────────────
# Support/vtk_lib.py does `from __main__ import vtk` — make vtk visible in
# this module's globals before that import runs.
import vtk                       # noqa: E402, F401
import GPA                       # noqa: E402  — exposes LMData, GPALogic, gpa_lib
from GPA import LMData, GPALogic

import json, shutil, tempfile

src_paths = sorted(glob.glob(os.path.join(args.landmarks, "*.mrk.json")))
if not src_paths:
    print(f"No .mrk.json files in {args.landmarks}", file=sys.stderr)
    sys.exit(1)
files = [os.path.splitext(os.path.basename(p))[0] for p in src_paths]

# GPA.loadLandmarks requires each control point to have
# `positionStatus: "defined"` and `description` fields. Slicer's fiducial
# editor writes those; other producers (ALPACA matchingPCD, pure-numpy
# pipelines) often omit them. Patch a copy of each file in a temp dir so
# this example works against any source.
_tmp = tempfile.mkdtemp(prefix="gpa_lm_")
paths = []
patched = 0
for p in src_paths:
    with open(p) as f:
        d = json.load(f)
    cps = d["markups"][0]["controlPoints"]
    for cp in cps:
        if "positionStatus" not in cp:  cp["positionStatus"] = "defined"
        if "description"    not in cp:  cp["description"]    = ""
    out_p = os.path.join(_tmp, os.path.basename(p))
    with open(out_p, "w") as f:
        json.dump(d, f)
    paths.append(out_p)
    patched += 1
print(f"[1] Found {len(src_paths)} landmark files in {args.landmarks}"
      f" (patched {patched} → {_tmp})")

exclude = [int(x) for x in args.exclude.split(",") if x.strip()]
if exclude:
    print(f"    Excluding landmarks (1-based): {exclude}")

os.makedirs(args.out, exist_ok=True)

print("[2] Loading landmarks via GPALogic.loadLandmarks ...")
logic = GPALogic()
LM_arr, landmarkTypes = logic.loadLandmarks(paths, exclude, "mrk.json")
print(f"    array shape (n_landmarks, 3, n_subjects): {LM_arr.shape}")
print(f"    semi-landmark indices in source: {landmarkTypes or '(none)'}")

print("[3] Running Generalized Procrustes Analysis ...")
LM = LMData()
LM.lmOrig = LM_arr
LM.doGpa(BoasOption=args.boas)
print(f"    Procrustes distances per subject — min={LM.procdist.min():.6f}  "
      f"max={LM.procdist.max():.6f}  mean={LM.procdist.mean():.6f}")
print(f"    Centroid sizes — min={LM.centriodSize.min():.4f}  "
      f"max={LM.centriodSize.max():.4f}  mean={LM.centriodSize.mean():.4f}")

print("[4] Running PCA (SVD on the GPA-aligned shape matrix) ...")
LM.calcEigen()
n_pcs = LM.vec.shape[1]
print(f"    {n_pcs} principal components computed")
cumvar = (LM.val / LM.val.sum()).cumsum()
for i in range(min(5, n_pcs)):
    print(f"      PC{i+1:<2d}  eigenvalue={LM.val[i]:.6e}  "
          f"cumulative variance={cumvar[i]*100:.2f}%")

print("[5] Writing output CSVs ...")
LM.writeOutData(args.out, files)
for f in sorted(os.listdir(args.out)):
    p = os.path.join(args.out, f)
    if os.path.isfile(p):
        print(f"    {f:<22}  {os.path.getsize(p):,} bytes")

print(f"\nDone. GPA outputs in {args.out}/")
