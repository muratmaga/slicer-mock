"""Extension of sweep/slicer_mock.py for the original-space-gpa buildConsensusAtlas.

Patches the in-memory slicer mock after install() to support every slicer.*
call made by _denseConsensusAccumulate:

  slicer.vtkMRMLMarkupsFiducialNode()        – direct constructor
  slicer.mrmlScene.AddNode(node)             – no-op registration
  slicer.util.arrayFromMarkupsControlPoints  – returns np.array of _pts
  _MockTransformNode.Inverse()               – inverts stored vtkTransform
  _MockFiducialNode.AddControlPoint()        – appends to _pts
  _MockFiducialNode.SetFixedNumberOfControlPoints / SetNthControlPointLocked – no-ops
  vtkSlicerTransformLogic.hardenTransform    – real VTK transform application

Idempotent: safe to call multiple times.
"""
import sys
import numpy as np
import vtk

import slicer_mock as _sm

# ── Transform-node registry ──────────────────────────────────────────────────
_TRANSFORM_NODES = {}

_orig_TransformNode_init = _sm._MockTransformNode.__init__

def _tracked_init(self, name=""):
    _orig_TransformNode_init(self, name=name)
    _TRANSFORM_NODES[id(self)] = self

_sm._MockTransformNode.__init__ = _tracked_init


def _mock_inverse(self):
    """Invert the stored vtkTransform in place."""
    xform = getattr(self, "_transform", None)
    if xform is None:
        return
    m = vtk.vtkMatrix4x4()
    xform.GetMatrix(m)
    m.Invert()
    inv = vtk.vtkTransform()
    inv.SetMatrix(m)
    self._transform = inv

_sm._MockTransformNode.Inverse = _mock_inverse


# ── Fiducial-node method gaps ────────────────────────────────────────────────
def _fid_add_control_point(self, pt):
    if not hasattr(self, "_pts"):
        self._pts = []
    self._pts.append(list(pt))

def _fid_noop(self, *a, **kw):
    pass

if not hasattr(_sm._MockFiducialNode, "AddControlPoint"):
    _sm._MockFiducialNode.AddControlPoint = _fid_add_control_point
if not hasattr(_sm._MockFiducialNode, "SetFixedNumberOfControlPoints"):
    _sm._MockFiducialNode.SetFixedNumberOfControlPoints = _fid_noop
if not hasattr(_sm._MockFiducialNode, "SetNthControlPointLocked"):
    _sm._MockFiducialNode.SetNthControlPointLocked = _fid_noop


# ── Working hardenTransform ──────────────────────────────────────────────────
def _lps_to_ras(vtk_xform):
    """Wrap an ITK/LPS-convention VTK transform for application to RAS data.

    Slicer MRML transforms are stored in LPS (ITK convention).  Mesh and
    fiducial data live in RAS.  The correct application is:
        T_ras = flip @ T_lps @ flip,   flip = diag(-1, -1, 1, 1)
    """
    m4 = vtk.vtkMatrix4x4()
    vtk_xform.GetMatrix(m4)
    M = np.array([[m4.GetElement(i, j) for j in range(4)] for i in range(4)])
    flip = np.diag([-1., -1., 1., 1.])
    M_ras = flip @ M @ flip
    m4_ras = vtk.vtkMatrix4x4()
    for i in range(4):
        for j in range(4):
            m4_ras.SetElement(i, j, M_ras[i, j])
    t_ras = vtk.vtkTransform()
    t_ras.SetMatrix(m4_ras)
    return t_ras


class _WorkingTransformLogic:
    def hardenTransform(self, node):
        tid = getattr(node, "_transform_id", None)
        if tid is None:
            return
        xform_node = _TRANSFORM_NODES.get(tid)
        if xform_node is None:
            return
        vtk_xform = getattr(xform_node, "_transform", None)
        if vtk_xform is None:
            return

        t_ras = _lps_to_ras(vtk_xform)

        if isinstance(node, _sm._MockModelNode):
            tf = vtk.vtkTransformPolyDataFilter()
            tf.SetTransform(t_ras)
            tf.SetInputData(node._poly)
            tf.Update()
            new_pd = vtk.vtkPolyData()
            new_pd.DeepCopy(tf.GetOutput())
            node._poly = new_pd
        elif isinstance(node, _sm._MockFiducialNode):
            pts = getattr(node, "_pts", [])
            if pts:
                arr  = np.array(pts)
                ones = np.ones((len(arr), 1))
                M    = np.array([[t_ras.GetMatrix().GetElement(i, j)
                                  for j in range(4)] for i in range(4)])
                result = (M @ np.hstack([arr, ones]).T).T[:, :3]
                node._pts = result.tolist()
        node._transform_id = None


# ── patch() ──────────────────────────────────────────────────────────────────
def patch():
    if "slicer" not in sys.modules:
        raise RuntimeError("patch() must be called after slicer_mock.install()")
    slicer = sys.modules["slicer"]

    # hardenTransform
    slicer.vtkSlicerTransformLogic = _WorkingTransformLogic
    _sm.vtkSlicerTransformLogic = _WorkingTransformLogic

    # slicer.vtkMRMLMarkupsFiducialNode() constructor
    if not hasattr(slicer, "vtkMRMLMarkupsFiducialNode"):
        slicer.vtkMRMLMarkupsFiducialNode = _sm._MockFiducialNode

    # mrmlScene.AddNode / RemoveNode  (no-ops in mock)
    if not hasattr(slicer.mrmlScene, "AddNode"):
        slicer.mrmlScene.AddNode = lambda node: None
    if not hasattr(slicer.mrmlScene, "RemoveNode"):
        slicer.mrmlScene.RemoveNode = lambda node: None

    # slicer.app.processEvents (no-op — no Qt event loop in headless mock)
    if not hasattr(slicer, "app"):
        import types as _types
        slicer.app = _types.SimpleNamespace(processEvents=lambda: None)
    elif not hasattr(slicer.app, "processEvents"):
        slicer.app.processEvents = lambda: None

    # util.arrayFromMarkupsControlPoints
    if not hasattr(slicer.util, "arrayFromMarkupsControlPoints"):
        def _array_from_fid(node):
            return np.array(getattr(node, "_pts", []), dtype=np.float64)
        slicer.util.arrayFromMarkupsControlPoints = _array_from_fid
