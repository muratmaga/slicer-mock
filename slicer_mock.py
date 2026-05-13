"""
slicer_mock.py — Minimal Slicer/MRML mock for headless ALPACALogic.

Usage:
    from slicer_mock import install, load_lm_numpy
    ALPACALogic = install("/path/to/ALPACA.py")
"""
import sys, os, json, types, subprocess, tempfile
import vtk
import numpy as np


# ── Node mocks ────────────────────────────────────────────────────────────────

class _MockDisplayNode:
    def SetVisibility(self, v): pass
    def SetColor(self, *a):     pass


class _MockModelNode:
    def __init__(self, polydata=None, name=""):
        self._poly          = polydata or vtk.vtkPolyData()
        self._name          = name
        self._transform_id  = None
    def GetPolyData(self):               return self._poly
    def GetMesh(self):                   return self._poly
    def SetAndObservePolyData(self, p):  self._poly = p
    def GetDisplayNode(self):            return _MockDisplayNode()
    def SetName(self, n):                self._name = n
    def GetName(self):                   return self._name
    def SetAndObserveTransformNodeID(self, tid): self._transform_id = tid
    def CreateDefaultDisplayNodes(self): pass


class _MockFiducialNode:
    def __init__(self, name=""):
        self._pts    = []
        self._name   = name
        self._labels = []
        self._descs  = []

    def AddControlPoint(self, pt):
        self._pts.append(list(pt))
        self._labels.append("")
        self._descs.append("")

    def GetNumberOfControlPoints(self):     return len(self._pts)

    def GetNthControlPointPosition(self, i, p=None):
        if p is not None:
            p[0], p[1], p[2] = self._pts[i]
        return tuple(self._pts[i])

    def SetLocked(self, v):                  pass
    def SetFixedNumberOfControlPoints(self, v): pass
    def GetDisplayNode(self):                return _MockDisplayNode()
    def SetName(self, n):                    self._name = n
    def GetName(self):                       return self._name
    def SetAndObserveTransformNodeID(self, tid): self._transform_id = tid
    def GetNthControlPointLabel(self, i):        return self._labels[i]
    def SetNthControlPointLabel(self, i, v):     self._labels[i] = v
    def GetNthControlPointDescription(self, i):  return self._descs[i]
    def SetNthControlPointDescription(self, i, v): self._descs[i] = v

    def as_numpy(self):
        return np.array(self._pts, dtype=float)


class _MockTransformNode:
    def __init__(self, name=""):
        self._name      = name
        self._transform = None
    def GetID(self):                              return id(self)
    def SetAndObserveTransformToParent(self, t):  self._transform = t


class _MockScene:
    def AddNewNodeByClass(self, cls_name, name=""):
        if "ModelNode"       in cls_name: return _MockModelNode(name=name)
        if "MarkupsFiducial" in cls_name: return _MockFiducialNode(name=name)
        if "TransformNode"   in cls_name: return _MockTransformNode(name=name)
        return types.SimpleNamespace()

    def RemoveNode(self, node): pass

    def GetFirstNodeByName(self, n): return None

    def GetNodesByName(self, n):
        class _R:
            def GetItemAsObject(self, i): return None
        return _R()


# ── Utility mock ──────────────────────────────────────────────────────────────

class _MockUtil:
    @staticmethod
    def loadModel(path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".ply":
            r = vtk.vtkPLYReader()
        elif ext in (".vtk", ".vtp"):
            r = vtk.vtkPolyDataReader()
        elif ext == ".obj":
            r = vtk.vtkOBJReader()
        else:
            r = vtk.vtkPLYReader()
        r.SetFileName(path)
        r.Update()
        poly = r.GetOutput()
        # Match real slicer.util.loadModel: PLY on disk is LPS, internal is RAS.
        # Flip X,Y so downstream ALPACA code sees RAS coordinates.
        from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk
        arr = vtk_to_numpy(poly.GetPoints().GetData()).copy()
        arr[:, 0] *= -1
        arr[:, 1] *= -1
        new_pts = vtk.vtkPoints()
        new_pts.SetData(numpy_to_vtk(np.ascontiguousarray(arr), deep=True))
        poly.SetPoints(new_pts)
        return _MockModelNode(polydata=poly, name=os.path.basename(path))

    @staticmethod
    def loadMarkups(path):
        node = _MockFiducialNode(name=os.path.basename(path))
        if path.endswith(".fcsv"):
            with open(path) as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    parts = line.strip().split(",")
                    if len(parts) >= 4:
                        # FCSV stores LPS; real Slicer converts LPS→RAS on read.
                        node.AddControlPoint([-float(parts[1]),
                                              -float(parts[2]),
                                               float(parts[3])])
        else:   # .mrk.json — coordinateSystem is LPS on disk; convert to RAS.
            with open(path) as f:
                d = json.load(f)
            cs = d["markups"][0].get("coordinateSystem", "LPS")
            for cp in d["markups"][0]["controlPoints"]:
                pos = cp["position"]
                if cs == "LPS":
                    node.AddControlPoint([-pos[0], -pos[1], pos[2]])
                else:
                    node.AddControlPoint(pos)
        return node

    @staticmethod
    def saveNode(node, path):
        if not isinstance(node, _MockFiducialNode):
            return
        # Internal coordinates are RAS; real Slicer writes LPS to .mrk.json.
        # Flip X,Y to match real Slicer's on-disk LPS convention.
        cps = [
            {
                "id": str(i + 1), "label": "", "description": "",
                "position": [-pt[0], -pt[1], pt[2]],
                "orientation": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                "selected": True, "locked": True, "visibility": True,
            }
            for i, pt in enumerate(node._pts)
        ]
        out = {
            "markups": [{
                "type": "Fiducial",
                "coordinateSystem": "LPS",
                "controlPoints": cps,
            }]
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(out, f, indent=2)

    @staticmethod
    def updateMarkupsControlPointsFromArray(node, arr):
        node._pts = arr.tolist()

    @staticmethod
    def pip_install(packages):
        if isinstance(packages, str):
            packages = [packages]
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + packages,
            check=True,
        )

    @staticmethod
    def createProgressDialog(**kwargs):
        return types.SimpleNamespace(close=lambda: None)

    @staticmethod
    def infoDisplay(msg):
        print("INFO:", msg)

    class WaitCursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass


class _MockApp:
    cachePath = tempfile.gettempdir()
    os        = "linux"
    def processEvents(self): pass


class _MockVtkSlicerTransformLogic:
    def hardenTransform(self, node): pass


# ── Public API ────────────────────────────────────────────────────────────────

def install(alpaca_py_path):
    """
    Inject the Slicer mock into sys.modules and load ALPACALogic from source.
    Returns the ALPACALogic class.
    """
    import importlib.util

    slicer_mock = types.ModuleType("slicer")
    slicer_mock.mrmlScene               = _MockScene()
    slicer_mock.util                    = _MockUtil
    slicer_mock.app                     = _MockApp()
    slicer_mock.vtkSlicerTransformLogic = _MockVtkSlicerTransformLogic
    for sub in ("modules", "qMRMLUtils", "customLayoutSM",
                "customLayoutTableOnly", "customLayoutPlotOnly"):
        setattr(slicer_mock, sub, types.SimpleNamespace())

    qt_mock = types.ModuleType("qt")
    qt_mock.QMessageBox = types.SimpleNamespace(
        critical=lambda *a, **kw: None,
        information=lambda *a, **kw: None,
    )
    qt_mock.QIcon = lambda *a: None

    ctk_mock = types.ModuleType("ctk")
    ctk_mock.ctkPathLineEdit = types.SimpleNamespace(Dirs=0, Files=1)
    ctk_mock.ctkWidgetsUtils = types.SimpleNamespace(grabWidget=lambda *a: None)

    sys.modules["slicer"]      = slicer_mock
    sys.modules["qt"]          = qt_mock
    sys.modules["ctk"]         = ctk_mock
    sys.modules["slicer.util"] = types.ModuleType("slicer.util")

    slm = types.ModuleType("slicer.ScriptedLoadableModule")
    for cls_name in ("ScriptedLoadableModule", "ScriptedLoadableModuleWidget",
                     "ScriptedLoadableModuleLogic", "ScriptedLoadableModuleTest"):
        setattr(slm, cls_name, object)
    sys.modules["slicer.ScriptedLoadableModule"] = slm

    spec = importlib.util.spec_from_file_location("_ALPACA", alpaca_py_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ALPACALogic


def load_lm_numpy(path):
    """Load a .mrk.json or .fcsv landmark file → (N,3) numpy array."""
    return _MockUtil.loadMarkups(path).as_numpy()


def rmse(A, B):
    """RMS Euclidean distance between two (N,3) arrays."""
    return float(np.sqrt(np.mean(np.sum(np.square(A - B), axis=1))))
