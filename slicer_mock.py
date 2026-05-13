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


# ── Volume nodes ──────────────────────────────────────────────────────────────

class _MockVolumeNode:
    """Scalar / label volume node. Backed by a vtkImageData and an IJK→RAS
    direction matrix. Slicer uses RAS internally; load/save handle the
    LPS↔RAS conversion when reading NIfTI / NRRD."""
    def __init__(self, image_data=None, ijk_to_ras=None, name="", node_class="vtkMRMLScalarVolumeNode"):
        self._poly        = None    # not a mesh; here for duck-typing
        self._image       = image_data if image_data is not None else vtk.vtkImageData()
        self._ijk_to_ras  = ijk_to_ras if ijk_to_ras is not None else vtk.vtkMatrix4x4()
        self._name        = name
        self._class       = node_class
        self._transform_id = None
        # Default direction matrix: identity (only set if ijk_to_ras was None)
        if ijk_to_ras is None:
            self._ijk_to_ras.Identity()
    def GetImageData(self):              return self._image
    def SetAndObserveImageData(self, d):  self._image = d
    def GetIJKToRASMatrix(self, m):       m.DeepCopy(self._ijk_to_ras)
    def SetIJKToRASMatrix(self, m):       self._ijk_to_ras.DeepCopy(m)
    def GetIJKToRASDirectionMatrix(self, m):
        # Just the 3x3 rotation/scale part of IJK→RAS, with translation zeroed
        m.DeepCopy(self._ijk_to_ras)
        for i in range(3):
            m.SetElement(i, 3, 0.0)
            m.SetElement(3, i, 0.0)
        m.SetElement(3, 3, 1.0)
    def SetIJKToRASDirectionMatrix(self, m):
        # Preserve current origin (column 3) when updating direction
        origin = [self._ijk_to_ras.GetElement(i, 3) for i in range(3)]
        self._ijk_to_ras.DeepCopy(m)
        for i in range(3):
            self._ijk_to_ras.SetElement(i, 3, origin[i])
    def GetOrigin(self):
        return tuple(self._ijk_to_ras.GetElement(i, 3) for i in range(3))
    def SetOrigin(self, x, y, z):
        self._ijk_to_ras.SetElement(0, 3, x)
        self._ijk_to_ras.SetElement(1, 3, y)
        self._ijk_to_ras.SetElement(2, 3, z)
    def GetSpacing(self):
        # Magnitude of each direction column
        return tuple(
            float(np.sqrt(sum(self._ijk_to_ras.GetElement(i, j) ** 2 for i in range(3))))
            for j in range(3)
        )
    def SetSpacing(self, sx, sy, sz):
        cur = self.GetSpacing()
        scales = (sx / cur[0] if cur[0] else 1.0,
                  sy / cur[1] if cur[1] else 1.0,
                  sz / cur[2] if cur[2] else 1.0)
        for j, s in enumerate(scales):
            for i in range(3):
                self._ijk_to_ras.SetElement(i, j, self._ijk_to_ras.GetElement(i, j) * s)
    def GetName(self):                    return self._name
    def SetName(self, n):                 self._name = n
    def GetClassName(self):               return self._class
    def CreateDefaultDisplayNodes(self):  pass
    def GetDisplayNode(self):             return _MockDisplayNode()
    def SetAndObserveTransformNodeID(self, tid): self._transform_id = tid


# ── Segmentation nodes (simplified — single-segment via binary labelmap) ──────

class _MockSegment:
    def __init__(self, segment_id="Segment_1", name="Segment", color=(1.0, 0.0, 0.0)):
        self._id    = segment_id
        self._name  = name
        self._color = color
        self._labelmap = None       # vtkImageData
        self._labelmap_ijk_to_ras = None  # vtkMatrix4x4
        self._label_value = 1
    def GetName(self):              return self._name
    def SetName(self, n):           self._name = n
    def GetColor(self):             return self._color
    def SetColor(self, r, g, b):    self._color = (r, g, b)
    def GetLabelValue(self):        return self._label_value
    def SetLabelValue(self, v):     self._label_value = v


class _MockSegmentation:
    """The vtkSegmentation owned by a vtkMRMLSegmentationNode."""
    def __init__(self):
        self._segments = {}   # ordered dict of segment_id → _MockSegment
        self._source_geometry_image_data = None  # vtkImageData (reference geometry)
    def GetNumberOfSegments(self):    return len(self._segments)
    def GetSegmentIDs(self):          return list(self._segments.keys())
    def GetSegment(self, segment_id): return self._segments.get(segment_id)
    def AddSegment(self, segment, segment_id=None):
        if segment_id is None:
            segment_id = segment._id
        self._segments[segment_id] = segment
        return segment_id
    def RemoveSegment(self, segment_id):
        self._segments.pop(segment_id, None)
    def GetSegmentIdBySegmentName(self, name):
        for sid, seg in self._segments.items():
            if seg.GetName() == name:
                return sid
        return ""
    def SetSourceRepresentationName(self, name): pass


class _MockSegmentationNode:
    def __init__(self, name="Segmentation"):
        self._segmentation     = _MockSegmentation()
        self._name             = name
        self._transform_id     = None
        self._reference_volume = None     # optional reference volume node
    def GetSegmentation(self):                   return self._segmentation
    def GetName(self):                           return self._name
    def SetName(self, n):                        self._name = n
    def GetClassName(self):                      return "vtkMRMLSegmentationNode"
    def CreateDefaultDisplayNodes(self):         pass
    def GetDisplayNode(self):                    return _MockDisplayNode()
    def SetAndObserveTransformNodeID(self, tid): self._transform_id = tid
    def SetReferenceImageGeometryParameterFromVolumeNode(self, vol):
        self._reference_volume = vol


# ── Segmentations logic (slicer.modules.segmentations.logic()) ────────────────

class _MockSegmentationsLogic:
    """Minimal stand-in for slicer.modules.segmentations.logic()."""

    @staticmethod
    def ImportLabelmapToSegmentationNode(labelmapNode, segmentationNode, terminologyContextName=""):
        """Convert each unique non-zero label value into a segment."""
        img = labelmapNode.GetImageData()
        if img is None:
            return False
        ijk_to_ras = vtk.vtkMatrix4x4()
        labelmapNode.GetIJKToRASMatrix(ijk_to_ras)
        from vtk.util.numpy_support import vtk_to_numpy
        arr = vtk_to_numpy(img.GetPointData().GetScalars())
        for label in [int(v) for v in np.unique(arr) if v != 0]:
            seg = _MockSegment(segment_id=f"Segment_{label}",
                               name=f"Segment_{label}")
            seg._labelmap = img
            seg._labelmap_ijk_to_ras = ijk_to_ras
            seg._label_value = label
            segmentationNode.GetSegmentation().AddSegment(seg)
        return True

    @staticmethod
    def ExportSegmentsToLabelmapNode(segmentationNode, segmentIDs, labelmapNode, referenceVolumeNode=None):
        """Burn the chosen segments into the labelmap node's image data."""
        segmentation = segmentationNode.GetSegmentation()
        if not segmentIDs:
            segmentIDs = segmentation.GetSegmentIDs()
        # Use the first segment's labelmap as the reference geometry if none provided
        first = segmentation.GetSegment(segmentIDs[0])
        if first is None or first._labelmap is None:
            return False
        if referenceVolumeNode is not None:
            ref_img = referenceVolumeNode.GetImageData()
            ijk_to_ras = vtk.vtkMatrix4x4()
            referenceVolumeNode.GetIJKToRASMatrix(ijk_to_ras)
        else:
            ref_img = first._labelmap
            ijk_to_ras = first._labelmap_ijk_to_ras
        # Build a fresh labelmap of the same geometry
        from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk
        shape = ref_img.GetDimensions()
        out = np.zeros(shape[::-1], dtype=np.int16)   # KJI
        for sid in segmentIDs:
            seg = segmentation.GetSegment(sid)
            if seg is None or seg._labelmap is None:
                continue
            seg_arr = vtk_to_numpy(seg._labelmap.GetPointData().GetScalars())
            seg_arr = seg_arr.reshape(seg._labelmap.GetDimensions()[::-1])
            mask = seg_arr == seg.GetLabelValue()
            if mask.shape != out.shape:
                continue   # silently skip geometry mismatch
            out[mask] = seg.GetLabelValue()
        new_img = vtk.vtkImageData()
        new_img.SetDimensions(*shape)
        new_img.SetSpacing(*ref_img.GetSpacing())
        new_img.SetOrigin(*ref_img.GetOrigin())
        new_img.GetPointData().SetScalars(numpy_to_vtk(out.flatten(order="C"), deep=True))
        labelmapNode.SetAndObserveImageData(new_img)
        labelmapNode.SetIJKToRASMatrix(ijk_to_ras)
        return True

    @staticmethod
    def ExportAllSegmentsToLabelmapNode(segmentationNode, labelmapNode, referenceVolumeNode=None):
        return _MockSegmentationsLogic.ExportSegmentsToLabelmapNode(
            segmentationNode, None, labelmapNode, referenceVolumeNode)

    @staticmethod
    def ExportVisibleSegmentsToLabelmapNode(*args, **kw):
        return _MockSegmentationsLogic.ExportAllSegmentsToLabelmapNode(*args, **kw)


class _MockScene:
    def AddNewNodeByClass(self, cls_name, name=""):
        if "ModelNode"          in cls_name: return _MockModelNode(name=name)
        if "MarkupsFiducial"    in cls_name: return _MockFiducialNode(name=name)
        if "TransformNode"      in cls_name: return _MockTransformNode(name=name)
        if "SegmentationNode"   in cls_name: return _MockSegmentationNode(name=name)
        if "LabelMapVolumeNode" in cls_name: return _MockVolumeNode(name=name, node_class=cls_name)
        if "VolumeNode"         in cls_name: return _MockVolumeNode(name=name, node_class=cls_name)
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
        # Fiducial markups → .mrk.json (RAS→LPS flip applied)
        if isinstance(node, _MockFiducialNode):
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
            return True
        # Volume → NRRD / NIfTI (RAS→LPS conversion on the IJK→RAS matrix)
        if isinstance(node, _MockVolumeNode):
            return _save_volume(node, path)
        # Segmentation → export segments to a labelmap, save that
        if isinstance(node, _MockSegmentationNode):
            tmp_lm = _MockVolumeNode(name=node.GetName(),
                                     node_class="vtkMRMLLabelMapVolumeNode")
            ok = _MockSegmentationsLogic.ExportAllSegmentsToLabelmapNode(
                node, tmp_lm, node._reference_volume)
            if not ok:
                return False
            return _save_volume(tmp_lm, path)
        return False

    # ── Volume / segmentation loading ─────────────────────────────────────────

    @staticmethod
    def loadVolume(path, properties=None):
        """Read a scalar volume from NRRD/NIfTI/MHA. Returns a _MockVolumeNode
        with image data in IJK-ordered VTK form and IJK→RAS matrix matching
        real Slicer's RAS convention."""
        image, ijk_to_ras = _read_volume_file(path)
        return _MockVolumeNode(image_data=image, ijk_to_ras=ijk_to_ras,
                               name=os.path.basename(path),
                               node_class="vtkMRMLScalarVolumeNode")

    @staticmethod
    def loadLabelVolume(path, properties=None):
        image, ijk_to_ras = _read_volume_file(path)
        return _MockVolumeNode(image_data=image, ijk_to_ras=ijk_to_ras,
                               name=os.path.basename(path),
                               node_class="vtkMRMLLabelMapVolumeNode")

    @staticmethod
    def loadSegmentation(path, properties=None):
        """Load a label volume as a single-segment segmentation node.
        For full .seg.nrrd multi-segment support, run under real Slicer."""
        image, ijk_to_ras = _read_volume_file(path)
        lm = _MockVolumeNode(image_data=image, ijk_to_ras=ijk_to_ras,
                             name=os.path.basename(path),
                             node_class="vtkMRMLLabelMapVolumeNode")
        seg_node = _MockSegmentationNode(name=os.path.basename(path))
        _MockSegmentationsLogic.ImportLabelmapToSegmentationNode(lm, seg_node)
        return seg_node

    # ── Numpy access for volume / segment data ────────────────────────────────

    @staticmethod
    def arrayFromVolume(volumeNode):
        """Return a numpy view of the volume scalars in KJI order (matches
        slicer.util.arrayFromVolume)."""
        from vtk.util.numpy_support import vtk_to_numpy
        img = volumeNode.GetImageData()
        scalars = img.GetPointData().GetScalars()
        if scalars is None:
            return np.zeros(img.GetDimensions()[::-1])
        arr = vtk_to_numpy(scalars)
        return arr.reshape(img.GetDimensions()[::-1])   # K,J,I

    @staticmethod
    def arrayFromVolumeModified(volumeNode):
        """Notify VTK that the underlying scalars were modified in-place."""
        img = volumeNode.GetImageData()
        if img is not None:
            scalars = img.GetPointData().GetScalars()
            if scalars is not None:
                scalars.Modified()
            img.Modified()

    @staticmethod
    def updateVolumeFromArray(volumeNode, narray):
        """Replace the volume's scalars with the given numpy array (KJI order)."""
        from vtk.util.numpy_support import numpy_to_vtk
        img = volumeNode.GetImageData() or vtk.vtkImageData()
        img.SetDimensions(narray.shape[::-1])
        img.AllocateScalars(vtk.VTK_DOUBLE, 1)   # caller may need to set type explicitly
        flat = np.ascontiguousarray(narray.flatten(order="C"))
        img.GetPointData().SetScalars(numpy_to_vtk(flat, deep=True))
        volumeNode.SetAndObserveImageData(img)

    @staticmethod
    def arrayFromSegmentBinaryLabelmap(segmentationNode, segmentId, referenceVolumeNode=None):
        seg = segmentationNode.GetSegmentation().GetSegment(segmentId)
        if seg is None or seg._labelmap is None:
            return None
        from vtk.util.numpy_support import vtk_to_numpy
        arr = vtk_to_numpy(seg._labelmap.GetPointData().GetScalars())
        arr = arr.reshape(seg._labelmap.GetDimensions()[::-1])
        return (arr == seg.GetLabelValue()).astype(np.uint8)

    @staticmethod
    def arrayFromSegmentInternalBinaryLabelmap(segmentationNode, segmentId):
        return _MockUtil.arrayFromSegmentBinaryLabelmap(segmentationNode, segmentId)

    @staticmethod
    def updateSegmentBinaryLabelmapFromArray(narray, segmentationNode, segmentId,
                                             referenceVolumeNode=None):
        """Write a binary mask back into the named segment. The mask is stored
        as a labelmap with the segment's label value."""
        from vtk.util.numpy_support import numpy_to_vtk
        seg = segmentationNode.GetSegmentation().GetSegment(segmentId)
        if seg is None:
            return False
        labelmap = vtk.vtkImageData()
        labelmap.SetDimensions(narray.shape[::-1])
        labelmap.AllocateScalars(vtk.VTK_SHORT, 1)
        out = (narray.astype(np.int16) * seg.GetLabelValue()).flatten(order="C")
        labelmap.GetPointData().SetScalars(numpy_to_vtk(out, deep=True))
        if referenceVolumeNode is not None:
            ijk_to_ras = vtk.vtkMatrix4x4()
            referenceVolumeNode.GetIJKToRASMatrix(ijk_to_ras)
            labelmap.SetSpacing(*referenceVolumeNode.GetImageData().GetSpacing())
            labelmap.SetOrigin(*referenceVolumeNode.GetImageData().GetOrigin())
            seg._labelmap_ijk_to_ras = ijk_to_ras
        seg._labelmap = labelmap
        return True

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


# ── Volume I/O helpers ────────────────────────────────────────────────────────

def _is_nrrd(path):  return path.lower().endswith((".nrrd", ".nhdr"))
def _is_nifti(path): return path.lower().endswith((".nii", ".nii.gz"))
def _is_mha(path):   return path.lower().endswith((".mha", ".mhd"))


def _matrix_lps_to_ras(m):
    """Return a copy of 4×4 matrix m with rows 0 and 1 negated (LPS↔RAS)."""
    out = vtk.vtkMatrix4x4()
    out.DeepCopy(m)
    for col in range(4):
        out.SetElement(0, col, -out.GetElement(0, col))
        out.SetElement(1, col, -out.GetElement(1, col))
    return out


def _read_volume_file(path):
    """Read NRRD/NIfTI/MHA → (vtkImageData, IJK→RAS vtkMatrix4x4 in RAS).

    NRRD: prefers Slicer's vtkTeem.vtkNRRDReader (handles RAS internally) if
    available; falls back to pynrrd if installed.
    NIfTI: vtkNIFTIImageReader gives back IJK + an LPS-oriented qform/sform;
    we negate rows 0/1 to convert to RAS.
    MHA: vtkMetaImageReader; we treat its returned direction as LPS and flip.
    """
    if _is_nrrd(path):
        try:
            import vtkTeem
            # In Slicer the class is named vtkTeemNRRDReader
            ReaderClass = getattr(vtkTeem, "vtkTeemNRRDReader", None) \
                          or getattr(vtkTeem, "vtkNRRDReader", None)
            if ReaderClass is None:
                raise ImportError("no NRRD reader in vtkTeem")
            r = ReaderClass()
            r.SetFileName(path)
            r.Update()
            image = r.GetOutput()
            # vtkTeem returns RAS-to-IJK; invert and that's IJK-to-RAS
            ras_to_ijk = r.GetRasToIjkMatrix()
            ijk_to_ras = vtk.vtkMatrix4x4()
            vtk.vtkMatrix4x4.Invert(ras_to_ijk, ijk_to_ras)
            return image, ijk_to_ras
        except ImportError:
            pass
        try:
            import nrrd
            data, header = nrrd.read(path)
            from vtk.util.numpy_support import numpy_to_vtk
            image = vtk.vtkImageData()
            image.SetDimensions(*data.shape)
            image.AllocateScalars(vtk.VTK_DOUBLE, 1)
            image.GetPointData().SetScalars(
                numpy_to_vtk(np.ascontiguousarray(data.flatten(order="F"), dtype=np.float64),
                             deep=True))
            ijk_to_ras = vtk.vtkMatrix4x4()
            ijk_to_ras.Identity()
            if "space directions" in header and "space origin" in header:
                for j in range(3):
                    for i in range(3):
                        ijk_to_ras.SetElement(i, j, float(header["space directions"][j][i]))
                for i in range(3):
                    ijk_to_ras.SetElement(i, 3, float(header["space origin"][i]))
            return image, ijk_to_ras
        except ImportError:
            raise RuntimeError(
                "NRRD support requires either Slicer's vtkTeem (run under "
                "PythonSlicer) or `pynrrd` (pip install pynrrd).")
    if _is_nifti(path):
        r = vtk.vtkNIFTIImageReader()
        r.SetFileName(path)
        r.Update()
        image = r.GetOutput()
        # Prefer qform; fall back to sform if no qform
        m = r.GetQFormMatrix() or r.GetSFormMatrix()
        if m is None:
            ijk_to_ras = vtk.vtkMatrix4x4()
            ijk_to_ras.Identity()
        else:
            ijk_to_ras = _matrix_lps_to_ras(m)
        return image, ijk_to_ras
    if _is_mha(path):
        r = vtk.vtkMetaImageReader()
        r.SetFileName(path)
        r.Update()
        image = r.GetOutput()
        # MetaImage stores direction in LPS by convention; build matrix and flip
        ijk_to_ras_lps = vtk.vtkMatrix4x4()
        ijk_to_ras_lps.Identity()
        # The reader does not expose direction directly; users can override the
        # matrix after load if they need precise orientation handling.
        ijk_to_ras = _matrix_lps_to_ras(ijk_to_ras_lps)
        return image, ijk_to_ras
    raise RuntimeError(f"Unsupported volume format: {path}")


def _save_volume(volumeNode, path):
    """Write a _MockVolumeNode to disk in the format implied by the extension.
    Converts the internal RAS direction matrix to LPS for NIfTI / MHA. NRRD
    via vtkTeem stores RAS directly (no flip required)."""
    image = volumeNode.GetImageData()
    if image is None:
        return False
    ijk_to_ras = vtk.vtkMatrix4x4()
    volumeNode.GetIJKToRASMatrix(ijk_to_ras)
    if _is_nrrd(path):
        try:
            import vtkTeem
            WriterClass = getattr(vtkTeem, "vtkTeemNRRDWriter", None) \
                          or getattr(vtkTeem, "vtkNRRDWriter", None)
            if WriterClass is None:
                raise ImportError("no NRRD writer in vtkTeem")
            w = WriterClass()
            w.SetInputData(image)
            w.SetFileName(path)
            ras_to_ijk = vtk.vtkMatrix4x4()
            vtk.vtkMatrix4x4.Invert(ijk_to_ras, ras_to_ijk)
            w.SetIJKToRASMatrix(ijk_to_ras)
            w.Write()
            return True
        except ImportError:
            try:
                import nrrd
                from vtk.util.numpy_support import vtk_to_numpy
                arr = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(
                    image.GetDimensions()[::-1])
                # pynrrd expects Fortran order for "space directions" alignment
                arr_f = np.asfortranarray(arr.transpose())
                directions = [[ijk_to_ras.GetElement(i, j) for i in range(3)] for j in range(3)]
                origin = [ijk_to_ras.GetElement(i, 3) for i in range(3)]
                nrrd.write(path, arr_f,
                           {"space": "right-anterior-superior",
                            "space directions": directions,
                            "space origin": origin})
                return True
            except ImportError:
                raise RuntimeError(
                    "NRRD write requires vtkTeem (PythonSlicer) or pynrrd.")
    if _is_nifti(path):
        w = vtk.vtkNIFTIImageWriter()
        w.SetInputData(image)
        w.SetFileName(path)
        # Convert RAS → LPS for the on-disk qform/sform
        ijk_to_lps = _matrix_lps_to_ras(ijk_to_ras)   # same function, involutive
        w.SetQFormMatrix(ijk_to_lps)
        w.SetSFormMatrix(ijk_to_lps)
        w.Write()
        return True
    if _is_mha(path):
        w = vtk.vtkMetaImageWriter()
        w.SetInputData(image)
        w.SetFileName(path)
        w.Write()
        return True
    raise RuntimeError(f"Unsupported volume write format: {path}")


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
    for sub in ("qMRMLUtils", "customLayoutSM",
                "customLayoutTableOnly", "customLayoutPlotOnly"):
        setattr(slicer_mock, sub, types.SimpleNamespace())
    # slicer.modules.<modulename>.logic() — only segmentations supported so far
    slicer_mock.modules = types.SimpleNamespace(
        segmentations=types.SimpleNamespace(
            logic=lambda: _MockSegmentationsLogic()
        ),
    )

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
