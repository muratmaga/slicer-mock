# slicer-mock

A minimal mock for the `slicer` / `qt` / `ctk` Python modules so that **3D Slicer extension code can be imported and executed headlessly** — outside the Slicer application — from a plain SlicerPython process (e.g. on a SLURM cluster).

This was extracted from the SlicerMorph ALPACA workflow but the core (`slicer_mock.py`) is generic. It targets extension code that uses the `slicer.util` / `slicer.mrmlScene` / `slicer.ScriptedLoadableModule` surface plus VTK + numpy, which is the dominant pattern for SlicerMorph and most scripted Slicer modules.

---

## What you get

- **`slicer_mock.py`** — the mock. Replaces `slicer`, `qt`, `ctk`, and `slicer.ScriptedLoadableModule` in `sys.modules`. After install, you can import the target Slicer extension's `Logic` class normally and call it.
- **`atlas_mock_extension.py`** — an example of how to *extend* the mock for a specific use case. This particular extension patches the mock's `vtkSlicerTransformLogic.hardenTransform` with a working implementation, which is required for ALPACA's `buildConsensusAtlas` to actually mutate polydata between iterations. For other Slicer modules you might write a different extension, or none at all.

## What is mocked

The mock implements the surface most scripted Slicer modules need:

| Slicer API | Mock class / fn | Notes |
|---|---|---|
| `slicer.mrmlScene` | `_MockScene` | `AddNewNodeByClass`, `RemoveNode`, `GetFirstNodeByName`, `GetNodesByName` |
| `slicer.util.loadModel(path)` | `_MockUtil.loadModel` | Reads PLY/VTK/VTP/OBJ. **Applies LPS→RAS flip (X*=-1, Y*=-1) to match real Slicer's PLY load behaviour** |
| `slicer.util.loadMarkups(path)` | `_MockUtil.loadMarkups` | Reads `.fcsv` and `.mrk.json`. LPS→RAS conversion based on `coordinateSystem` field |
| `slicer.util.saveNode(node, path)` | `_MockUtil.saveNode` | Writes `.mrk.json` only (markups). Applies RAS→LPS flip on write |
| `slicer.util.pip_install(pkgs)` | `_MockUtil.pip_install` | `pip install --quiet` shell-out |
| `slicer.util.createProgressDialog` | `_MockUtil.createProgressDialog` | Returns a stub with `close()` |
| `slicer.util.infoDisplay` | `_MockUtil.infoDisplay` | Prints to stdout |
| `slicer.util.WaitCursor` | `_MockUtil.WaitCursor` | Context-manager no-op |
| `slicer.app` | `_MockApp` | Provides `.cachePath`, `.os`, `.processEvents()` |
| `slicer.vtkSlicerTransformLogic` | `_MockVtkSlicerTransformLogic` | `hardenTransform` is a no-op by default — patch via `atlas_mock_extension.py` when needed |
| `slicer.ScriptedLoadableModule.*` | `object`-derived classes | All four base classes (Module, Widget, Logic, Test) |
| Model node | `_MockModelNode` | `GetPolyData`, `GetMesh`, `SetAndObservePolyData`, `SetName`/`GetName`, `SetAndObserveTransformNodeID` |
| Fiducial / markups node | `_MockFiducialNode` | `AddControlPoint`, `GetNthControlPointPosition`, label/description getters and setters |
| Transform node | `_MockTransformNode` | `GetID`, `SetAndObserveTransformToParent` |
| `qt`, `ctk` | Empty modules | `qt.QMessageBox`, `qt.QIcon`, `ctk.ctkPathLineEdit`, `ctk.ctkWidgetsUtils` stubs |

The mock does **not** provide rendering, layouts, widget interaction, the subject hierarchy, DICOM I/O, or volume support. If your extension touches those it will fail; extend the mock.

## Coordinate system handling

3D Slicer uses RAS internally and writes PLY/MRK files in LPS by convention. The mock's `loadModel` applies an LPS→RAS flip (negate X and Y) on read so downstream code receives RAS coordinates exactly as in real Slicer; `loadMarkups` and `saveNode` do the same on the markups side. **If your code writes PLY files via `vtkPLYWriter` directly (not via `slicer.util.saveNode`)**, be aware that Slicer's vtkPLYWriter also applies a RAS→LPS flip on write — your output will be in LPS coordinates with a `SPACE=LPS` header. Both conventions are preserved by the mock so existing pipelines continue to work.

## Installation

```bash
git clone https://github.com/muratmaga/slicer-mock.git
```

Then put the directory on your `sys.path` (or use it directly via absolute path).

## Usage

The minimal headless bootstrap, before any Slicer-extension-related import:

```python
import os, sys, types

ALPACA_DIR   = "/path/to/ALPACA"  # or any Slicer extension root
ALPACA_PY    = os.path.join(ALPACA_DIR, "ALPACA.py")
SLICER_MOCK  = "/path/to/slicer-mock"  # this repo

# 1) Pre-mock qt before any import that touches it at module level
sys.modules.setdefault("qt", types.ModuleType("qt"))

# 2) Put the extension directory on sys.path so internal package imports
#    (e.g. `from Templates.X import Y`) resolve
sys.path.insert(0, ALPACA_DIR)

# 3) Put slicer-mock on sys.path
sys.path.insert(0, SLICER_MOCK)

# 4) Install the mock and load the extension's Logic class from source
from slicer_mock import install
ExtensionLogic = install(ALPACA_PY)   # returns the Logic class

# 5) (Optional) Apply any extension-specific patches
import atlas_mock_extension
atlas_mock_extension.patch()

# 6) Use the Logic as you would inside Slicer
logic = ExtensionLogic()
result = logic.someMethod(...)
```

After `install()` returns, the loaded module sees a fully wired `slicer.*` namespace and can call any of the mocked APIs.

### Loading models and markups

```python
import slicer
node = slicer.util.loadModel("/path/to/skull.ply")
polydata = node.GetPolyData()           # vtkPolyData in RAS
markups = slicer.util.loadMarkups("/path/to/landmarks.mrk.json")
arr = markups.as_numpy()                # numpy (N,3) in RAS
```

### Loading landmarks as numpy directly

```python
from slicer_mock import load_lm_numpy
landmarks = load_lm_numpy("/path/to/landmarks.mrk.json")  # (N,3) numpy in RAS
```

### Computing RMSE between two landmark sets

```python
from slicer_mock import rmse
err = rmse(landmarks_a, landmarks_b)
```

## Extending the mock

If your target extension calls something not in the table above, you have two options:

1. **Monkey-patch from your script**, similar to how `atlas_mock_extension.py` patches `hardenTransform`:

   ```python
   import slicer

   def my_hardenTransform(self, node):
       # actually apply the transform from node._transform_id to node._poly
       ...

   slicer.vtkSlicerTransformLogic.hardenTransform = my_hardenTransform
   ```

2. **Add the API to `slicer_mock.py`** — preferred when the API is generic enough to belong in the mock itself (e.g. a new method on `_MockModelNode`).

## Limitations

- No widget / UI interaction. Anything that requires button clicks, signals/slots, or a running Qt event loop will not work.
- No subject hierarchy beyond a stub.
- No volume nodes, DICOM I/O, or segmentation.
- The mock loads extension `.py` files from disk via `importlib`; it does not load Slicer extension manifests.
- `_MockVtkSlicerTransformLogic.hardenTransform` is a **no-op by default**. If your code depends on transforms being applied to mesh vertices, patch this method (see `atlas_mock_extension.py`).
- The LPS↔RAS handling assumes Slicer's PLY/MRK conventions. If your data lives in some other coordinate system, override `_MockUtil.loadModel` / `loadMarkups`.

## When to use this

Use this mock when:
- You want to run a SlicerMorph or other scripted Slicer extension headlessly on a cluster or in CI.
- You're calling well-defined `Logic` methods, not interacting with the GUI.
- You can tolerate the no-UI surface (most scientific computing scripts can).

Don't use this mock when:
- You need rendering, screenshots, or any visual output.
- Your extension is deeply tied to the subject hierarchy or scene events.
- You're prototyping new Slicer functionality — use real Slicer for that.

## How this was built

The mock was reverse-engineered from running ALPACA headlessly: every time an attribute was missing, a stub was added until ALPACA's `buildConsensusAtlas` and `matchingPCD` ran end-to-end. The same approach should generalise — start with this mock, run your target extension, add stubs as exceptions surface.

## License

MIT (same as SlicerMorph).
