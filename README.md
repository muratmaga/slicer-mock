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
| `slicer.modules.segmentations.logic()` | `_MockSegmentationsLogic` | `ImportLabelmapToSegmentationNode`, `ExportSegmentsToLabelmapNode`, `ExportAllSegmentsToLabelmapNode` |
| `slicer.ScriptedLoadableModule.*` | `object`-derived classes | All four base classes (Module, Widget, Logic, Test) |
| Model node | `_MockModelNode` | `GetPolyData`, `GetMesh`, `SetAndObservePolyData`, `SetName`/`GetName`, `SetAndObserveTransformNodeID` |
| Fiducial / markups node | `_MockFiducialNode` | `AddControlPoint`, `GetNthControlPointPosition`, label/description getters and setters |
| Transform node | `_MockTransformNode` | `GetID`, `SetAndObserveTransformToParent` |
| Scalar / label volume node | `_MockVolumeNode` | `GetImageData`, `SetAndObserveImageData`, `GetIJKToRASMatrix`, `GetIJKToRASDirectionMatrix`, `GetOrigin`/`SetOrigin`, `GetSpacing`/`SetSpacing`, `GetClassName` |
| Segmentation node | `_MockSegmentationNode` | `GetSegmentation`, `SetReferenceImageGeometryParameterFromVolumeNode` |
| Segment | `_MockSegment` | `GetName`, `GetColor`, `GetLabelValue`, internal `_labelmap` (vtkImageData) |
| `slicer.util.loadVolume` | `_MockUtil.loadVolume` | NRRD (via vtkTeem if available, else pynrrd), NIfTI (vtkNIFTIImageReader), MHA (vtkMetaImageReader). Returns RAS-oriented IJK→RAS matrix |
| `slicer.util.loadLabelVolume` | `_MockUtil.loadLabelVolume` | As above, labelled as label-map node |
| `slicer.util.loadSegmentation` | `_MockUtil.loadSegmentation` | Loads a label volume and converts it to a single-or-multi-segment segmentation (one segment per unique label value) |
| `slicer.util.arrayFromVolume` | `_MockUtil.arrayFromVolume` | Returns numpy view in K,J,I order (matches real Slicer) |
| `slicer.util.updateVolumeFromArray` | `_MockUtil.updateVolumeFromArray` | Replaces volume scalars |
| `slicer.util.arrayFromSegmentBinaryLabelmap` | `_MockUtil.arrayFromSegmentBinaryLabelmap` | Returns uint8 binary mask for one segment |
| `slicer.util.updateSegmentBinaryLabelmapFromArray` | `_MockUtil.updateSegmentBinaryLabelmapFromArray` | Writes a binary mask back into a segment |
| `qt`, `ctk` | Empty modules | `qt.QMessageBox`, `qt.QIcon`, `ctk.ctkPathLineEdit`, `ctk.ctkWidgetsUtils` stubs |

The mock does **not** provide rendering, layouts, widget interaction, the subject hierarchy, DICOM I/O, or volume support. If your extension touches those it will fail; extend the mock.

## Coordinate system handling — important

3D Slicer uses **RAS** internally and stores PLY / MRK / NIfTI / NRRD on disk in **LPS** by convention. Mishandling this is the #1 cause of mesh / volume / landmark files loading in Slicer with the X / Y axes flipped (left-right mirror, anterior-posterior swap). The mock handles the conversion consistently across all file I/O:

| Operation | What the mock does |
|---|---|
| `slicer.util.loadModel(path)` (PLY/VTK/VTP/OBJ) | Read raw bytes, then **flip X, Y** so downstream code receives a RAS-oriented mesh |
| `slicer.util.saveNode(modelNode, "*.ply")` | **Flip X, Y back to LPS, write a `comment SPACE=LPS` header**, then `vtkPLYWriter`. Slicer (and any Slicer-aware reader) loads the result at the correct anatomical position |
| `slicer.util.loadMarkups(path)` (`.fcsv`, `.mrk.json`) | LPS→RAS flip on read, honouring the `coordinateSystem` field in `.mrk.json` |
| `slicer.util.saveNode(markupsNode, "*.mrk.json")` | RAS→LPS flip on write, sets `"coordinateSystem": "LPS"` in JSON |
| `slicer.util.loadVolume(path)` (NRRD) | Via `vtkTeem.vtkTeemNRRDReader` which yields a RAS-IJK matrix directly — no extra flip needed |
| `slicer.util.loadVolume(path)` (NIfTI) | Read qform/sform (LPS by convention), negate rows 0/1 → IJK→RAS matrix in RAS |
| `slicer.util.saveNode(volumeNode, "*.nrrd")` | Via `vtkTeemNRRDWriter` with the RAS IJK matrix |
| `slicer.util.saveNode(volumeNode, "*.nii.gz")` | Convert IJK→RAS back to LPS for the qform/sform on disk |
| `slicer.util.saveNode(segNode, "*.seg.nrrd")` | NRRD label volume + Slicer segmentation metadata (`Segmentation_MasterRepresentation`, `Segment*_ID/Name/Color/LabelValue/Layer/Extent/Tags`) — recognised by Slicer as a segmentation rather than a label volume |

**The rule of thumb:** call `slicer.util.saveNode(node, path)` instead of raw `vtkPLYWriter`, `vtkNIFTIImageWriter`, etc. The mock's `saveNode` dispatches on node type and applies the correct flip + header conventions for each output format. Calling the raw VTK writers directly skips all that and produces files that Slicer will load at the wrong orientation.

If you must use the raw writers (e.g. for a workflow Slicer doesn't natively handle), reproduce the conversion explicitly: for PLY output of RAS-oriented coordinates, flip X and Y, and write a `comment SPACE=LPS` header before `end_header`. Slicer will then load the file correctly.

## Installation

```bash
git clone https://github.com/muratmaga/slicer-mock.git
```

Then put the directory on your `sys.path` (or use it directly via absolute path).

## Running the script

This mock provides only the `slicer`, `qt`, and `ctk` API surface. It does **not** provide `vtk`, `itk`, `numpy`, `scipy`, `cpdalp`, or any other Slicer-bundled native dependency. Those must be importable from the Python interpreter you launch.

The intended way is to **run your script under Slicer's bundled `PythonSlicer` interpreter**, which already ships with the full Slicer dependency stack including the SlicerMorph-required versions of `itk-fpfh` and `itk-ransac`:

```bash
# Plain Python interpreter — VTK/ITK/numpy/etc. available, `slicer` is NOT
/path/to/Slicer-5.X.X-linux-amd64/bin/PythonSlicer my_headless_script.py

# Or via Slicer in no-GUI mode — `slicer` IS available natively, mock unnecessary
/path/to/Slicer-5.X.X-linux-amd64/Slicer --no-main-window --python-script my_script.py
```

On a SLURM cluster the pattern is typically:

```bash
#!/bin/bash
#SBATCH ...
ALPACA_DIR=/path/to/ALPACA
SLICER_BIN=/path/to/Slicer/bin/PythonSlicer
bash "${SLICER_BIN}" "${ALPACA_DIR}/my_headless_script.py"
```

You can also run under any other Python install that has `vtk`, `itk`, `itk-fpfh`, `itk-ransac`, `numpy`, `scipy`, and `cpdalp` available — but you lose Slicer-specific VTK behaviour (e.g. the automatic `SPACE=LPS` header on PLY writes) and extension code that depends on the SlicerMorph patched VTK will misbehave. **Prefer PythonSlicer** unless you have a specific reason not to.

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

### Loading volumes and accessing array data

```python
import slicer
import numpy as np

vol = slicer.util.loadVolume("/path/to/scan.nii.gz")
arr = slicer.util.arrayFromVolume(vol)  # numpy view, K,J,I order, in scanner units

# Modify in place
arr[arr < 100] = 0
slicer.util.arrayFromVolumeModified(vol)

# Or replace wholesale with a new array
new_arr = (arr > 200).astype(np.uint8) * 255
slicer.util.updateVolumeFromArray(vol, new_arr)

# Save back to disk
slicer.util.saveNode(vol, "/path/to/output.nrrd")   # NRRD via vtkTeem
slicer.util.saveNode(vol, "/path/to/output.nii.gz") # NIfTI with LPS qform/sform
```

Origin / spacing / direction:

```python
vol.GetOrigin()         # (Rx, Ay, Sz) in RAS
vol.GetSpacing()        # (sx, sy, sz)
m = vtk.vtkMatrix4x4(); vol.GetIJKToRASMatrix(m)
m = vtk.vtkMatrix4x4(); vol.GetIJKToRASDirectionMatrix(m)  # rotation+scale, no translation
```

### Loading segmentations and accessing binary masks

A label volume (a NIfTI / NRRD file where each voxel value identifies a segment) loads as a multi-segment segmentation, one segment per non-zero label value:

```python
seg = slicer.util.loadSegmentation("/path/to/labels.nii.gz")
segment_ids = seg.GetSegmentation().GetSegmentIDs()
for sid in segment_ids:
    mask = slicer.util.arrayFromSegmentBinaryLabelmap(seg, sid)
    # mask is a uint8 numpy array, 1 inside the segment, 0 outside
    ...

# Round-trip: convert segmentation back to a labelmap volume
import slicer
lmlogic = slicer.modules.segmentations.logic()
labelmap = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "tmp_lm")
lmlogic.ExportAllSegmentsToLabelmapNode(seg, labelmap)
slicer.util.saveNode(labelmap, "/path/to/exported_labels.nii.gz")

# Or save the segmentation directly (uses the all-segments labelmap path internally)
slicer.util.saveNode(seg, "/path/to/segmentation.nrrd")
```

`.seg.nrrd` (Slicer's multi-segment NRRD with embedded metadata) is **not** fully supported by the mock. The mock falls back to treating it as a generic label volume, which loses segment names / colors / multi-representation data. For full `.seg.nrrd` round-trip, run under real Slicer.

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
- No DICOM I/O.
- **`.seg.nrrd` multi-segment metadata is not preserved.** Segmentations load as labelmap-backed single/multi-segment nodes (one segment per unique label value); segment names, colors, and Slicer-specific metadata are lost on round-trip. Use real Slicer for `.seg.nrrd` work.
- **NRRD support outside PythonSlicer requires `pynrrd`.** Under real PythonSlicer, `vtkTeem.vtkNRRDReader`/`vtkNRRDWriter` is used directly. Under plain Python the mock falls back to `pynrrd` if installed; if neither is available, NRRD I/O raises.
- Volume coordinate handling assumes the NIfTI qform/sform is in LPS (the standard convention) and converts to RAS on read; MHA direction handling is identity by default (override the IJK→RAS matrix manually if you need precise MHA orientations).
- The mock loads extension `.py` files from disk via `importlib`; it does not load Slicer extension manifests.
- `_MockVtkSlicerTransformLogic.hardenTransform` is a **no-op by default**. If your code depends on transforms being applied to mesh vertices, patch this method (see `atlas_mock_extension.py`).
- The LPS↔RAS handling assumes Slicer's PLY/MRK/NIfTI conventions. If your data lives in some other coordinate system, override the load functions.

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
