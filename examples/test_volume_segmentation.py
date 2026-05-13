#!/usr/bin/env python3
"""End-to-end test of slicer-mock's volume + segmentation API.

Workflow exercised:
    1. Download IMPC_sample_data.nrrd from SlicerMorph SampleData
    2. Load it as a Slicer ScalarVolumeNode (mock)
    3. Threshold 100-255 → binary mask
    4. Median 5x5x5 smoothing
    5. Connected-components ("island tool") split into separate segments
    6. Save segmentation as NRRD
    7. Convert each segment to a 3D PLY surface

Run with Slicer's bundled Python interpreter (canonical):

    /path/to/Slicer/bin/PythonSlicer test_volume_segmentation.py

Output goes to /tmp/slicer_mock_test_output/.
"""
import os
import sys
import types
import urllib.request

import numpy as np

# ── Bootstrap the slicer-mock ─────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # repo root
sys.modules.setdefault("qt", types.ModuleType("qt"))

import slicer_mock as _sm
# Set up sys.modules['slicer'] manually — we don't need to load any extension
_slicer = types.ModuleType("slicer")
_slicer.mrmlScene = _sm._MockScene()
_slicer.util      = _sm._MockUtil
_slicer.app       = _sm._MockApp()
_slicer.modules   = types.SimpleNamespace(
    segmentations=types.SimpleNamespace(logic=lambda: _sm._MockSegmentationsLogic()),
)
sys.modules["slicer"] = _slicer

import slicer
import vtk
from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

OUT = "/tmp/slicer_mock_test_output"
os.makedirs(OUT, exist_ok=True)
URL  = "https://raw.githubusercontent.com/SlicerMorph/SampleData/master/IMPC_sample_data.nrrd"
NRRD = os.path.join(OUT, "IMPC_sample_data.nrrd")

# ── 1. Download ───────────────────────────────────────────────────────────────
if not os.path.exists(NRRD):
    print(f"[1] Downloading {URL}")
    urllib.request.urlretrieve(URL, NRRD)
print(f"[1] NRRD file: {NRRD} ({os.path.getsize(NRRD):,} bytes)")

# ── 2. Load volume ────────────────────────────────────────────────────────────
print("\n[2] Loading volume via slicer.util.loadVolume ...")
vol = slicer.util.loadVolume(NRRD)
dims = vol.GetImageData().GetDimensions()
print(f"    dimensions: {dims}")
print(f"    spacing   : {vol.GetSpacing()}")
print(f"    origin    : {vol.GetOrigin()}")
arr = slicer.util.arrayFromVolume(vol)
print(f"    numpy shape: {arr.shape}  dtype: {arr.dtype}")
print(f"    intensity range: [{arr.min()}, {arr.max()}]")

# ── 3. Threshold 100-255 ──────────────────────────────────────────────────────
print("\n[3] Thresholding 100-255 ...")
threshold_img = vtk.vtkImageThreshold()
threshold_img.SetInputData(vol.GetImageData())
threshold_img.ThresholdBetween(100, 255)
threshold_img.SetInValue(1)
threshold_img.SetOutValue(0)
threshold_img.SetOutputScalarTypeToUnsignedChar()
threshold_img.Update()

# Wrap the thresholded result in a label-map volume node
labelmap = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "threshold")
labelmap.SetAndObserveImageData(threshold_img.GetOutput())
ijk_to_ras = vtk.vtkMatrix4x4(); vol.GetIJKToRASMatrix(ijk_to_ras)
labelmap.SetIJKToRASMatrix(ijk_to_ras)
n_fg = int((vtk_to_numpy(threshold_img.GetOutput().GetPointData().GetScalars()) > 0).sum())
print(f"    foreground voxels: {n_fg:,} of {arr.size:,} ({n_fg/arr.size*100:.2f}%)")

# ── 4. Median 5x5x5 ───────────────────────────────────────────────────────────
print("\n[4] Median 5x5x5 smoothing ...")
median = vtk.vtkImageMedian3D()
median.SetInputData(labelmap.GetImageData())
median.SetKernelSize(5, 5, 5)
median.Update()
labelmap.SetAndObserveImageData(median.GetOutput())
n_fg_med = int((vtk_to_numpy(median.GetOutput().GetPointData().GetScalars()) > 0).sum())
print(f"    foreground after median: {n_fg_med:,}  ({(n_fg_med-n_fg)/n_fg*100:+.1f}% vs raw)")

# ── 5. Connected components (island tool) ─────────────────────────────────────
print("\n[5] Splitting into islands (connected components) ...")
cc = vtk.vtkImageConnectivityFilter()
cc.SetInputData(labelmap.GetImageData())
cc.SetScalarRange(1, 255)
cc.SetExtractionModeToAllRegions()
cc.SetLabelModeToConstantValue()      # default: each region gets the same value
cc.SetLabelModeToSizeRank()           # rank by size: largest=1, next=2, ...
cc.Update()
labeled = cc.GetOutput()
n_islands = cc.GetNumberOfExtractedRegions()
print(f"    found {n_islands} islands")

# Stuff the labelled volume back into the label-map node
labelmap.SetAndObserveImageData(labeled)

# ── 6. Convert labelmap to a Segmentation node ────────────────────────────────
print("\n[6] Building Segmentation node (via slicer.modules.segmentations.logic) ...")
seg_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", "islands")
seg_logic = slicer.modules.segmentations.logic()
seg_logic.ImportLabelmapToSegmentationNode(labelmap, seg_node)
seg_ids = seg_node.GetSegmentation().GetSegmentIDs()
print(f"    segments created: {len(seg_ids)}")
for sid in seg_ids[:5]:
    seg = seg_node.GetSegmentation().GetSegment(sid)
    mask = slicer.util.arrayFromSegmentBinaryLabelmap(seg_node, sid)
    print(f"      {sid:<15s} label={seg.GetLabelValue():<3d} voxels={int(mask.sum()):,}")
if len(seg_ids) > 5:
    print(f"      ... and {len(seg_ids)-5} more")

# ── 7. Save segmentation to disk via slicer.util.saveNode ─────────────────────
print("\n[7] Saving segmentation ...")
seg_path = os.path.join(OUT, "islands_segmentation.nrrd")
ok = slicer.util.saveNode(seg_node, seg_path)
print(f"    saveNode returned: {ok}")
if os.path.exists(seg_path):
    print(f"    saved {seg_path} ({os.path.getsize(seg_path):,} bytes)")

# ── 8. Convert each segment to a PLY surface ─────────────────────────────────
print("\n[8] Converting segments to 3D PLY models ...")
for sid in seg_ids:
    seg = seg_node.GetSegmentation().GetSegment(sid)
    if seg._labelmap is None:
        continue
    # Marching cubes on the segment's binary labelmap
    mc = vtk.vtkDiscreteMarchingCubes()
    mc.SetInputData(seg._labelmap)
    mc.SetValue(0, seg.GetLabelValue())
    mc.Update()
    surf = mc.GetOutput()
    if surf.GetNumberOfPoints() == 0:
        print(f"    {sid}: empty surface, skipping")
        continue
    # Bring IJK → RAS via the labelmap's affine
    tx = vtk.vtkTransform()
    tx.SetMatrix(seg._labelmap_ijk_to_ras)
    tf = vtk.vtkTransformPolyDataFilter()
    tf.SetTransform(tx)
    tf.SetInputData(surf)
    tf.Update()
    # Write PLY (Slicer's vtkPLYWriter handles RAS→LPS automatically; under
    # plain VTK we write raw RAS coordinates without a SPACE header)
    out_ply = os.path.join(OUT, f"{sid}.ply")
    w = vtk.vtkPLYWriter()
    w.SetFileName(out_ply)
    w.SetInputData(tf.GetOutput())
    w.SetFileTypeToBinary()
    w.Write()
    print(f"    {sid}: {tf.GetOutput().GetNumberOfPoints():,} verts  →  {out_ply}")

print(f"\nDone. All outputs in {OUT}/")
